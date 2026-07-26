"""ИИ-консультант ИУ в ОТКРЫТОЙ ЛИНИИ Битрикса — с перехватом управления человеком.

Зачем канал: Telegram-ветка отвечает от лица личного аккаунта менеджера через Business API — это
вне CRM, переписка не живёт в карточке, а робот соседнего канала уже путал агента (24.07.2026,
диалог 980579939). Открытая линия ведёт разговор в сделке воронки ИУ, показывает историю людям и
даёт штатную передачу оператору.

ГЛАВНОЕ ПРАВИЛО КАНАЛА (требование владельца 26.07.2026): человек в любой момент забирает
разговор себе и в любой момент отдаёт обратно боту.

  * человек написал в диалог  →  бот замолкает (ничего делать руками не надо, перехват — это
    сам факт, что заговорил живой сотрудник);
  * человек написал команду возврата («/бот») → бот снова ведёт разговор.

Битрикс отдать диалог ОБРАТНО боту не умеет: imopenlines.operator.transfer принимает только id
сотрудника или queue<ID> (проверено по документации 26.07.2026). Поэтому бот из чата не выходит
вообще, а «кто ведёт» — наше состояние в openline_dialogs.

Модуль намеренно без Flask, без сети и без БД внутри логики: решение принимает чистая функция
`decide`, а ввод-вывод передаётся снаружи (`handle_event`). Так канал проверяется тестами до
того, как его увидит живой клиент.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Callable

log = logging.getLogger("openline_agent")

CHANNEL = "openline"

# Решения хода
ANSWER = "answer"          # ответить клиенту
IGNORE = "ignore"          # ничего не делать (но входящее записать в журнал)
TAKEOVER = "takeover"      # человек вмешался — управление уходит людям, бот молчит
RESUME = "resume"          # человек вернул разговор боту

# Команды возврата управления. Пишет их СОТРУДНИК в диалоге; клиенту такие слова управлением не
# считаются — иначе клиент фразой «бот» включал бы автоответчик посреди разговора с человеком.
RETURN_COMMANDS = (
    "/бот", "/bot", "бот", "бот вернись", "бот, вернись", "верни бота", "вернуть бота",
    "бот продолжай", "бот, продолжай", "включить бота", "бот на связь", "передать боту",
)

# Служебные сообщения самой линии приходят от отправителя 0 («Создана новая сделка», «Обращение
# направлено на …», приветствие, оценка). Это не человек и не клиент — в разговор их не берём.
SYSTEM_SENDER_IDS = {0, "0", "", None}


def _norm(text: str) -> str:
    return " ".join(str(text or "").strip().lower().split()).strip(" .!?")


def is_return_command(text: str) -> bool:
    """Текст сотрудника — это команда «веди дальше, бот»?"""
    return _norm(text) in {_norm(c) for c in RETURN_COMMANDS}


@dataclass
class Dialog:
    """Разобранный чат открытой линии: кто клиент и к какой сделке привязан разговор."""

    chat_id: int
    connector: str = ""
    line_id: int | None = None
    connector_chat_id: str = ""
    client_user_id: int | None = None
    deal_id: int | None = None
    session_id: int | None = None
    deal_category_id: int | None = None

    @property
    def dialog_id(self) -> str:
        return f"chat{self.chat_id}"


def _to_int(value: Any) -> int | None:
    try:
        text = str(value).strip()
        return int(text) if text and text.lstrip("-").isdigit() else None
    except (TypeError, ValueError):
        return None


def parse_entity_id(value: str) -> dict[str, Any]:
    """`entity_id` чата линии → коннектор, линия, чат коннектора и ПОЛЬЗОВАТЕЛЬ-КЛИЕНТ.

    Живой пример с прода (26.07.2026): `albery_probe|6|probe-client-1|114` — id клиента идёт
    четвёртым. Именно по нему отличаем клиента от сотрудника: гость линии заведён служебным
    пользователем, которого `user.get` даже не отдаёт."""
    parts = str(value or "").split("|")
    return {
        "connector": parts[0] if parts else "",
        "line_id": _to_int(parts[1]) if len(parts) > 1 else None,
        "connector_chat_id": parts[2] if len(parts) > 2 else "",
        "client_user_id": _to_int(parts[3]) if len(parts) > 3 else None,
    }


def parse_entity_data_1(value: str) -> dict[str, Any]:
    """`entity_data_1` чата линии → к какому элементу CRM привязан разговор.

    Живой пример: `Y|DEAL|162|N|N|20|1785059887|0|0|0` — тип, id элемента, затем id сессии."""
    parts = str(value or "").split("|")
    return {
        "crm_entity_type": (parts[1] if len(parts) > 1 else "").upper(),
        "crm_entity_id": _to_int(parts[2]) if len(parts) > 2 else None,
        "session_id": _to_int(parts[5]) if len(parts) > 5 else None,
    }


def dialog_from_chat(chat: dict[str, Any]) -> Dialog | None:
    """Собрать Dialog из ответа `im.dialog.get`. Не линия — не наш случай."""
    if not isinstance(chat, dict):
        return None
    if str(chat.get("entity_type") or "").upper() != "LINES":
        return None
    chat_id = _to_int(chat.get("id"))
    if not chat_id:
        return None
    ids = parse_entity_id(chat.get("entity_id") or "")
    crm = parse_entity_data_1(chat.get("entity_data_1") or "")
    return Dialog(
        chat_id=chat_id,
        connector=ids["connector"],
        line_id=ids["line_id"],
        connector_chat_id=ids["connector_chat_id"],
        client_user_id=ids["client_user_id"],
        deal_id=crm["crm_entity_id"] if crm["crm_entity_type"] == "DEAL" else None,
        session_id=crm["session_id"],
    )


@dataclass
class Event:
    """Входящее сообщение чата линии, уже вынутое из события Битрикса."""

    chat_id: int
    author_id: Any
    text: str = ""
    message_id: Any = None
    bot_id: Any = None


@dataclass
class Decision:
    action: str
    reason: str
    author: str = ""            # client | operator | system | bot
    text: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


def classify_author(event: Event, dialog: Dialog, *, bot_ids: set = frozenset()) -> str:
    """Кто написал: клиент, сотрудник, сам бот или служба линии."""
    author = str(event.author_id or "").strip()
    if event.author_id in SYSTEM_SENDER_IDS or author in {"0", ""}:
        return "system"
    if author in {str(b) for b in bot_ids if b}:
        return "bot"
    if dialog.client_user_id is not None and _to_int(author) == dialog.client_user_id:
        return "client"
    return "operator"


def decide(event: Event, dialog: Dialog, state: dict[str, Any], *,
           bot_ids: set = frozenset(), funnel_category_id: int | None = None,
           agent_enabled: bool = True) -> Decision:
    """Что делать с сообщением. Чистая функция: ни сети, ни БД — только правила.

    Порядок проверок важен: сначала «это вообще не человек», потом управление, потом ответ."""
    who = classify_author(event, dialog, bot_ids=bot_ids)
    bot_active = bool(state.get("bot_active", True))

    if who in ("bot", "system"):
        # Свои же сообщения и служебные записи линии не должны ни отвечать, ни перехватывать:
        # иначе бот выключил бы сам себя первым же собственным ответом.
        return Decision(IGNORE, "служебное сообщение линии или ответ самого бота", author=who)

    if who == "operator":
        if is_return_command(event.text):
            if bot_active:
                return Decision(IGNORE, "бот и так ведёт разговор", author=who,
                                meta={"command": True})
            return Decision(RESUME, "сотрудник вернул разговор боту", author=who,
                            meta={"command": True})
        if bot_active:
            return Decision(TAKEOVER, "в разговор вступил сотрудник — управление у людей",
                            author=who, text=event.text)
        return Decision(IGNORE, "разговор ведёт человек", author=who, text=event.text)

    # Дальше — только клиент.
    if not bot_active:
        return Decision(IGNORE, "разговор ведёт человек — бот молчит", author=who,
                        text=event.text)
    if not agent_enabled:
        # Выключатель воронки в кабинете главнее: владелец должен уметь остановить агента сам.
        return Decision(IGNORE, "агент воронки выключен в кабинете", author=who, text=event.text)
    if dialog.deal_id is None:
        return Decision(IGNORE, "разговор не привязан к сделке", author=who, text=event.text)
    if (funnel_category_id is not None and dialog.deal_category_id is not None
            and int(dialog.deal_category_id) != int(funnel_category_id)):
        # Линия может обслуживать не только воронку ИУ: чужие сделки ведут люди.
        return Decision(IGNORE, "сделка не в воронке ИУ", author=who, text=event.text,
                        meta={"category_id": dialog.deal_category_id})
    if not str(event.text or "").strip():
        # Вложение без текста: ответить нечего, но и перехватом это не является.
        return Decision(IGNORE, "сообщение клиента без текста", author=who)
    return Decision(ANSWER, "клиент написал, разговор ведёт бот", author=who, text=event.text)


# --- состояние диалога (PostgreSQL) ------------------------------------------------------------
# Слой намеренно тонкий и fail-open по чтению: если БД недоступна, канал не должен «залипнуть» в
# состоянии, которого никто не видит. Неизвестный диалог = бот ведёт (как при первом обращении).

_DEFAULT_STATE = {"bot_active": True, "known": False}


def load_state(db: Callable, chat_id: int) -> dict[str, Any]:
    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT chat_id, deal_id, bot_active, control_by, control_reason"
                    " FROM openline_dialogs WHERE chat_id = %s", (int(chat_id),))
                row = cur.fetchone()
    except Exception:  # noqa: BLE001
        log.warning("состояние диалога %s не прочитано — считаем, что ведёт бот", chat_id,
                    exc_info=True)
        return dict(_DEFAULT_STATE)
    if not row:
        return dict(_DEFAULT_STATE)
    data = dict(row) if not isinstance(row, dict) else row
    return {
        "bot_active": bool(data.get("bot_active", True)),
        "known": True,
        "deal_id": data.get("deal_id"),
        "control_by": data.get("control_by"),
        "control_reason": data.get("control_reason"),
    }


def remember_dialog(db: Callable, dialog: Dialog) -> None:
    """Запомнить разговор (или обновить привязку к сделке), не трогая, кто им управляет."""
    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO openline_dialogs (chat_id, line_id, connector, client_user_id,"
                    " deal_id, session_id) VALUES (%s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT (chat_id) DO UPDATE SET deal_id = COALESCE(EXCLUDED.deal_id,"
                    " openline_dialogs.deal_id), session_id = COALESCE(EXCLUDED.session_id,"
                    " openline_dialogs.session_id), line_id = COALESCE(EXCLUDED.line_id,"
                    " openline_dialogs.line_id), client_user_id = COALESCE(EXCLUDED.client_user_id,"
                    " openline_dialogs.client_user_id), updated_at = now()",
                    (int(dialog.chat_id), dialog.line_id, dialog.connector or None,
                     dialog.client_user_id, dialog.deal_id, dialog.session_id))
    except Exception:  # noqa: BLE001
        log.warning("диалог %s не записан", dialog.chat_id, exc_info=True)


def history_block(db: Callable, chat_id: int, current_text: str = "", limit: int = 12) -> str:
    """Последние реплики этого диалога — чтобы агент помнил, о чём уже говорили.

    Без истории каждый ход — чистый лист: клиент здоровается, агент отвечает «Здравствуйте!»,
    клиент спрашивает по делу — и агент здоровается второй раз (жалоба владельца 22.07.2026 по
    Telegram-ветке; в открытой линии грабли те же). Текущее сообщение в истории не повторяем."""
    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT direction, text FROM bitrix_bot_messages"
                    " WHERE dialog_id = %s AND kind = %s"
                    " ORDER BY id DESC LIMIT %s",
                    (f"chat{int(chat_id)}", "openline", int(limit)))
                rows = list(cur.fetchall())[::-1]
    except Exception:  # noqa: BLE001 — без истории агент ответит хуже, но ответит
        log.warning("история диалога %s недоступна", chat_id, exc_info=True)
        return ""
    current = (current_text or "").strip()
    lines = []
    for row in rows:
        data = dict(row) if not isinstance(row, dict) else row
        text = (data.get("text") or "").strip()
        if not text or (text == current and data.get("direction") == "in"):
            continue
        lines.append(f"{'Клиент' if data.get('direction') == 'in' else 'Ты'}: {text[:400]}")
    return "\n".join(lines)


def set_control(db: Callable, chat_id: int, *, bot_active: bool, by_user_id: Any = None,
                reason: str = "") -> None:
    """Переключить, кто ведёт разговор. Пишем и КТО переключил — для разбора спорных случаев."""
    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO openline_dialogs (chat_id, bot_active, control_by, control_reason,"
                    " control_changed_at) VALUES (%s, %s, %s, %s, now())"
                    " ON CONFLICT (chat_id) DO UPDATE SET bot_active = EXCLUDED.bot_active,"
                    " control_by = EXCLUDED.control_by, control_reason = EXCLUDED.control_reason,"
                    " control_changed_at = now(), updated_at = now()",
                    (int(chat_id), bool(bot_active), _to_int(by_user_id), (reason or "")[:500]))
    except Exception:  # noqa: BLE001
        log.warning("управление диалогом %s не переключено", chat_id, exc_info=True)


# --- ход канала ---------------------------------------------------------------------------------

# Подтверждения смены управления уходят СИСТЕМНЫМ сообщением: в открытой линии обычное сообщение
# бота ушло бы клиенту, и он читал бы нашу внутреннюю кухню.
TAKEOVER_NOTE = "🤝 Разговор ведёт сотрудник. Бот молчит, пока не вернут командой «/бот»."
RESUME_NOTE = "🤖 Разговор снова ведёт бот."


def handle_event(event: Event, *, bitrix, db: Callable, brain: Callable,
                 journal: Callable | None = None, bot_ids: set = frozenset(),
                 funnel_category_id: int | None = None,
                 agent_enabled: Callable | None = None) -> Decision:
    """Один ход канала: разобрать чат → принять решение → выполнить его.

    `bitrix` — объект с методом `call(method, params)`; `brain(dialog, text)` — ответ клиенту;
    `journal(...)` — запись в журнал переписок. Всё внешнее приходит снаружи, чтобы ход целиком
    проверялся тестами."""
    chat = bitrix.call("im.dialog.get", {"DIALOG_ID": f"chat{int(event.chat_id)}"}) or {}
    dialog = dialog_from_chat(chat.get("result") if isinstance(chat.get("result"), dict) else chat)
    if dialog is None:
        return Decision(IGNORE, "чат не является диалогом открытой линии", author="")

    if dialog.deal_id and funnel_category_id is not None:
        dialog.deal_category_id = _deal_category(bitrix, dialog.deal_id)

    enabled = True
    if agent_enabled is not None:
        try:
            enabled = bool(agent_enabled())
        except Exception:  # noqa: BLE001 — выключатель недоступен: ведём себя как обычно
            log.warning("выключатель воронки недоступен", exc_info=True)

    state = load_state(db, dialog.chat_id)
    decision = decide(event, dialog, state, bot_ids=bot_ids,
                      funnel_category_id=funnel_category_id, agent_enabled=enabled)

    if decision.author in ("client", "operator") and journal is not None and decision.text:
        _safe_journal(journal, dialog, decision, event)

    if decision.action == TAKEOVER:
        remember_dialog(db, dialog)
        set_control(db, dialog.chat_id, bot_active=False, by_user_id=event.author_id,
                    reason="сотрудник вступил в разговор")
        _system_note(bitrix, event, dialog, TAKEOVER_NOTE)
        log.info("линия: диалог %s перешёл к сотруднику %s", dialog.chat_id, event.author_id)
        return decision

    if decision.action == RESUME:
        remember_dialog(db, dialog)
        set_control(db, dialog.chat_id, bot_active=True, by_user_id=event.author_id,
                    reason="сотрудник вернул разговор боту")
        _system_note(bitrix, event, dialog, RESUME_NOTE)
        log.info("линия: диалог %s возвращён боту сотрудником %s", dialog.chat_id, event.author_id)
        return decision

    if decision.action != ANSWER:
        return decision

    remember_dialog(db, dialog)
    answer = ""
    try:
        answer = (brain(dialog, decision.text, history_block(db, dialog.chat_id, decision.text))
                  or "").strip()
    except Exception as exc:  # noqa: BLE001
        # Сбой мозга не имеет права означать тишину клиенту: разговор отдаём людям, как в
        # Telegram-ветке (правило владельца от 25.07.2026 — молчание хуже честной передачи).
        log.warning("мозг не ответил в диалоге %s: %s", dialog.chat_id, str(exc)[:200])
        set_control(db, dialog.chat_id, bot_active=False, by_user_id=None,
                    reason=f"сбой модели: {str(exc)[:200]}")
        _system_note(bitrix, event, dialog,
                     "⚠️ Бот не смог ответить (сбой модели) — разговор передан людям.")
        return Decision(TAKEOVER, "сбой модели — разговор передан людям", author=decision.author,
                        meta={"error": str(exc)[:200]})
    if not answer:
        set_control(db, dialog.chat_id, bot_active=False, by_user_id=None,
                    reason="пустой ответ модели")
        _system_note(bitrix, event, dialog,
                     "⚠️ Бот не смог ответить (пустой ответ) — разговор передан людям.")
        return Decision(TAKEOVER, "пустой ответ модели — разговор передан людям",
                        author=decision.author)

    bitrix.call("imbot.message.add", {
        "BOT_ID": event.bot_id, "DIALOG_ID": dialog.dialog_id, "MESSAGE": answer,
    })
    if journal is not None:
        _safe_journal(journal, dialog, Decision(ANSWER, "ответ бота", author="bot", text=answer),
                      event, direction="out")
    return Decision(ANSWER, "ответ отправлен клиенту", author=decision.author, text=answer)


def _deal_category(bitrix, deal_id: int) -> int | None:
    try:
        res = bitrix.call("crm.deal.get", {"id": int(deal_id)}) or {}
        deal = res.get("result") if isinstance(res, dict) else None
        deal = deal if isinstance(deal, dict) else res
        return _to_int((deal or {}).get("CATEGORY_ID"))
    except Exception:  # noqa: BLE001 — воронку не узнали: решает общий путь, а не исключение
        log.warning("воронка сделки %s не определена", deal_id, exc_info=True)
        return None


def _system_note(bitrix, event: Event, dialog: Dialog, text: str) -> None:
    """Внутренняя пометка в чат линии. SYSTEM='Y' — её видят сотрудники, а не клиент."""
    try:
        bitrix.call("imbot.message.add", {
            "BOT_ID": event.bot_id, "DIALOG_ID": dialog.dialog_id,
            "MESSAGE": text, "SYSTEM": "Y",
        })
    except Exception:  # noqa: BLE001 — пометка не важнее самого переключения управления
        log.warning("служебная пометка в диалог %s не отправлена", dialog.chat_id, exc_info=True)


def _safe_journal(journal: Callable, dialog: Dialog, decision: Decision, event: Event,
                  *, direction: str = "in") -> None:
    try:
        journal(dialog=dialog, direction=direction, text=decision.text,
                author=decision.author, event=event)
    except Exception:  # noqa: BLE001
        log.warning("журнал линии недоступен", exc_info=True)


def enabled() -> bool:
    """Канал включается флагом: выкатываем код молча, включаем осознанно."""
    return str(os.getenv("OPENLINE_AGENT_ENABLED", "0")).strip().lower() in {"1", "true", "yes", "on"}


def bot_id(state: dict[str, Any] | None = None) -> str:
    """id бота открытой линии. ОКРУЖЕНИЕ ГЛАВНЕЕ файла состояния — и это не вкусовщина.

    26.07.2026 id бота линии положили в общий `.b24_testbot_state.json`. Приложение переписывает
    этот файл ЦЕЛИКОМ на каждом событии, поэтому запись извне живёт до первого же события: ключ
    пропал, приложение не нашло основного бота и «самозалечилось» от события бота линии —
    основным ботом сотрудников стал бот линии (в состоянии bot_id стал 116 вместо 24).
    Окружение приложение не трогает никогда, поэтому канал опирается на него."""
    from_env = str(os.getenv("B24_OPENLINE_BOT_ID", "") or "").strip()
    return from_env or str((state or {}).get("openline_bot_id") or "").strip()
