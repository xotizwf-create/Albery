"""Трасса решений агента: что решили, по какому правилу, на каких фактах и что вышло.

Владелец 25.07.2026 (по итогам трёх разборов подряд): «нужно аккуратно отслеживать логику».

Зачем. Журнал сообщений показывает, ЧТО ушло клиенту, но не показывает ПОЧЕМУ. Каждый разбор
(«агент тупит», «почему не поздоровался», «почему не прислал сверку») начинался с чтения кода и
восстановления состояния по крупицам. Здесь пишется само решение — по одной строке видно, какое
правило сработало и на каких фактах, без чтения кода.

Запись НИКОГДА не должна ломать разговор с клиентом: любая ошибка тут гасится в лог.
"""
from __future__ import annotations

import json
import logging

log = logging.getLogger("decision-log")

# Поля снимка, которые действительно нужны при разборе. Весь объект не пишем: в трассе не место
# тексту клиента (он есть в журнале) и служебному мусору.
_FACT_FIELDS = ("stage", "terms_sent", "first_contact", "wants_terms", "anketa_seen",
                "legacy_surveyed")


def _facts_json(facts) -> str:
    data = {name: getattr(facts, name, None) for name in _FACT_FIELDS}
    data["anketa"] = "есть" if getattr(facts, "anketa", "") else "нет"
    data["anketa_new"] = bool(getattr(facts, "anketa_is_new", False))
    data["is_question"] = bool(getattr(facts, "is_question", False))
    data["iu_intent"] = bool(getattr(facts, "iu_intent", False))
    return json.dumps(data, ensure_ascii=False)


# Одно и то же «ничего не отправлять» сторож принимает каждую минуту по каждому приглашённому:
# 25.07.2026 за сутки так набежало 708 записей по ДВУМ диалогам, и лента решений в кабинете стала
# бесполезной. Повтор того же решения не пишем — только изменение.
_last: dict[tuple[int, str], str] = {}


def record(db, decision, *, slot: str, outcome: str = "") -> None:
    """Записать решение. `db` — контекстный менеджер соединения (передаём, чтобы не тащить БД сюда)."""
    facts = getattr(decision, "facts", None)
    if facts is None:
        return
    key = (int(getattr(facts, "uid", 0) or 0), slot)
    fingerprint = f"{decision.rule}|{decision.action}|{outcome}"
    if _last.get(key) == fingerprint:
        return          # решение не изменилось — в трассе такая строка уже есть
    _last[key] = fingerprint
    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO agent_decisions"
                    " (dialog_id, deal_id, slot, rule, action, origin, facts, outcome)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)",
                    (int(getattr(facts, "uid", 0) or 0),
                     getattr(facts, "deal_id", None),
                     slot, decision.rule, decision.action, decision.origin,
                     _facts_json(facts), outcome[:500]))
    except Exception:  # noqa: BLE001 — трасса не имеет права ронять ход
        log.warning("решение не записано в трассу", exc_info=True)
