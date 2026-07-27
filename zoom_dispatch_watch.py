"""Автоматическая отправка задач по созвонам — без Telegram, силами самого Албери.

Зачем модуль (инцидент 27.07.2026, сообщил владелец: «не отправляются задачи по созвонам»).
Прежняя цепочка была такой: созвон → отчёт → сводка владельцу в Telegram → владелец пишет
«ставь» → задачи уходят в Битрикс. У неё два слабых места, и оба выстрелили в один день:

  1. Telegram-бот Hermes перестал отвечать (токен отозван) — сводка до владельца не дошла,
     значит «ставь» не прозвучало, значит задачи не ушли. Единственный канал согласования
     оказался и единственной точкой отказа.
  2. Все тревоги системы шли через тот же Telegram. Когда ломается именно он, сообщить о
     поломке нечем — система замолчала целиком.

Решение владельца (27.07.2026): Telegram из этой цепочки убрать, отправку делает Албери сам.
Отсюда правила модуля:

  * отправка идёт автоматически, согласование в мессенджере больше не требуется;
  * ЛЮБОЙ сбой отправки становится задачей в Битриксе на владельца — канал тревоги не
    совпадает с каналом, за которым он следит, и молчания больше быть не может;
  * берутся только свежие созвоны (окно ``ZOOM_AUTO_DISPATCH_MAX_AGE_HOURS``): включение
    автоматики не должно выстрелить накопившимся хвостом задач по давно прошедшим встречам;
  * на один созвон — одна тревога: повтор каждые пять минут превратил бы задачи владельца
    в свалку.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

log = logging.getLogger("zoom_dispatch_watch")

OWNER_BITRIX_USER_ID = 16
AGENT_BITRIX_USER_ID = 22


def enabled() -> bool:
    return os.getenv("ZOOM_AUTO_DISPATCH_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}


def max_age_hours() -> int:
    try:
        value = int(os.getenv("ZOOM_AUTO_DISPATCH_MAX_AGE_HOURS", "24") or 24)
    except (TypeError, ValueError):
        value = 24
    return min(168, max(1, value))


def pending_calls(*, now: datetime | None = None, connect: Callable[[], Any] | None = None) -> list[dict[str, Any]]:
    """Созвоны с готовым отчётом и задачами, по которым отправки ещё не было.

    Тревога, уже поставленная по созвону, из выборки его НЕ убирает: сбой мог быть
    временным (недоступен портал), и следующая попытка обязана состояться.
    """
    timestamp = now or datetime.now(timezone.utc)
    cutoff = timestamp - timedelta(hours=max_age_hours())
    factory = connect or _default_connect()
    with factory() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text AS call_id,
                       start_time_msk,
                       COALESCE(topic, '') AS topic,
                       jsonb_array_length(
                           COALESCE(raw_json -> 'ai_report' -> 'analysis' -> 'operational_tasks',
                                    '[]'::jsonb)
                       ) AS tasks_count,
                       (raw_json -> 'ai_report' -> 'bitrix_dispatch_alert') IS NOT NULL
                           AS alerted
                  FROM zoom_calls
                 WHERE start_time_msk >= %s
                   AND (raw_json -> 'ai_report' -> 'bitrix_dispatch') IS NULL
                   AND COALESCE(analytical_note, '') <> ''
                   AND jsonb_array_length(
                           COALESCE(raw_json -> 'ai_report' -> 'analysis' -> 'operational_tasks',
                                    '[]'::jsonb)
                       ) > 0
                 ORDER BY start_time_msk
                """,
                (cutoff,),
            )
            return [dict(row) for row in cur.fetchall()]


def run_once(
    *,
    now: datetime | None = None,
    connect: Callable[[], Any] | None = None,
    dispatch: Callable[[str], Any] | None = None,
    alert: Callable[..., Any] | None = None,
    mark_alerted: Callable[[str, str], Any] | None = None,
) -> dict[str, Any]:
    """Один проход: отправить всё готовое, о каждом сбое сказать в Битриксе."""
    if not enabled():
        return {"enabled": False, "sent": [], "failed": [], "alerted": []}

    sender = dispatch or _default_dispatch
    notifier = alert or _default_alert
    marker = mark_alerted or _default_mark_alerted

    sent: list[str] = []
    failed: list[dict[str, Any]] = []
    alerted: list[str] = []
    for call in pending_calls(now=now, connect=connect):
        call_id = str(call["call_id"])
        try:
            sender(call_id)
        except Exception as exc:  # noqa: BLE001 — сбой одного созвона не останавливает остальные
            reason = str(exc)[:1000]
            log.warning("созвон %s: задачи не отправлены — %s", call_id, reason)
            failed.append({"call_id": call_id, "reason": reason})
            if call.get("alerted"):
                continue
            try:
                notifier(call=call, reason=reason)
                marker(call_id, reason)
                alerted.append(call_id)
            except Exception:  # noqa: BLE001
                # Не смогли даже пожаловаться — это худший исход, он обязан быть в журнале
                # громко: тихо пройти мимо потерянных задач нельзя.
                log.exception("созвон %s: не удалось поставить тревогу в Битриксе", call_id)
            continue
        log.info("созвон %s: задачи отправлены", call_id)
        sent.append(call_id)
    return {"enabled": True, "sent": sent, "failed": failed, "alerted": alerted}


def alert_deadline() -> str:
    """Срок разбора тревоги — тот же, что у карточек созвона: сегодня 18:00 МСК, а если до
    вечера уже недалеко, то следующее рабочее утро.

    Срок здесь ОБЯЗАТЕЛЕН, и это не формальность: постановщик задач Битрикса отказывается
    создавать задачу без дедлайна. Тревога без срока просто не создалась бы — и потерянные
    задачи снова остались бы незамеченными (поймано живой проверкой 27.07.2026).
    """
    import business_hours

    return business_hours.zoom_lead_deadline_at().isoformat()


def build_alert(call: dict[str, Any], reason: str) -> dict[str, str]:
    """Текст тревоги. Пишем так, чтобы по нему можно было действовать без раскопок."""
    started = call.get("start_time_msk")
    when = started.strftime("%d.%m.%Y %H:%M") if hasattr(started, "strftime") else str(started or "")
    topic = str(call.get("topic") or "").strip() or "без темы"
    tasks_count = call.get("tasks_count") or 0
    return {
        "deadline": alert_deadline(),
        "title": f"Албери: задачи по созвону {when} не ушли в Битрикс",
        "description": (
            f"[b]Что случилось[/b]\n"
            f"Созвон {when} ({topic}) разобран, в отчёте {tasks_count} задач(и), "
            f"но отправка не состоялась.\n\n"
            f"[b]Причина от системы[/b]\n{reason}\n\n"
            f"[b]Что сделать[/b]\n"
            f"Открыть созвон в кабинете Албери → «Отправка задач» и отправить вручную, "
            f"либо поправить разметку отчёта и дождаться следующей попытки — она "
            f"выполняется автоматически.\n\n"
            f"[b]Идентификатор созвона[/b]\n{call.get('call_id')}"
        ),
        "result_criteria": "Задачи созвона доведены до ответственных или причина сбоя устранена.",
    }


def _default_connect() -> Callable[[], Any]:
    from shared.db import connect

    return connect


def _default_dispatch(call_id: str) -> Any:
    import app

    return app.dispatch_zoom_operational_tasks(call_id)


def _default_alert(*, call: dict[str, Any], reason: str) -> Any:
    from mcp import context_server as cs

    payload = build_alert(call, reason)
    return cs.tool_create_bitrix_task({
        "title": payload["title"],
        "description": payload["description"],
        "responsible_bitrix_user_id": OWNER_BITRIX_USER_ID,
        "creator_bitrix_user_id": AGENT_BITRIX_USER_ID,
        "result_criteria": payload["result_criteria"],
        "deadline": payload["deadline"],
    })


def _default_mark_alerted(call_id: str, reason: str) -> None:
    from shared.db import connect

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE zoom_calls
                   SET raw_json = jsonb_set(
                           COALESCE(raw_json, '{}'::jsonb),
                           '{ai_report,bitrix_dispatch_alert}',
                           to_jsonb(%s::text),
                           true
                       )
                 WHERE id = %s
                """,
                (
                    f"{datetime.now(timezone.utc).isoformat()} :: {reason[:500]}",
                    call_id,
                ),
            )
