# -*- coding: utf-8 -*-
"""Воронка ИУ в кабинете: логическая цепочка, правила агента и живые решения.

Владелец 25.07.2026: «чтоб я увидел логическую цепочку и правила, по которым действует агент,
чтоб визуально понимал что происходит».

Почему это важно инженерно, а не только для красоты: до сих пор поведение агента можно было
понять только чтением кода. Владелец видел результат (сообщение клиенту) и не видел причину.
Здесь наружу отдаётся ровно то, что реально управляет агентом:
  • этапы воронки и что агент делает на каждом (считается тем же funnel_next_step, что уходит
    в промпт — не пересказ, а сам источник);
  • реестр правил с приоритетами и ПРИЧИНОЙ появления каждого (funnel_rules.RULES);
  • трасса решений: какое правило сработало на живом диалоге и что вышло (agent_decisions).

Только чтение. Ничего не меняет: контроль здесь — «видеть и понимать», а правки поведения
идут через правила в коде и тесты, иначе поведение снова расползётся.
"""
from __future__ import annotations

import logging

from flask import jsonify, request

import funnel_rules
from app import app, pg_connect  # noqa: E402

log = logging.getLogger("funnel_view")

# Человеческие названия этапов и порядок цепочки. Идентификаторы — из реестра, чтобы UI и
# поведение не разъезжались.
CHAIN = (
    (funnel_rules.STAGE_NEW, "Новый лид", "написал про ИУ — сделка заводится сразу"),
    (funnel_rules.STAGE_CONTACTED, "Связались", "ответ доставлен клиенту"),
    (funnel_rules.STAGE_FORM_DONE, "Анкета заполнена", "в сделке появились данные анкеты"),
    (funnel_rules.STAGE_TERMS, "Согласование условий", "клиент подтвердил анкету"),
    ("C16:NDA", "Документы и подписание", "реквизиты собраны, договор собран"),
    ("C16:UC_SGZRVS", "Документы подписаны", "клиент подтвердил подписание"),
    ("C16:PREPAYMENT_INVOIC", "Счёт на оплату", "счёт выставлен бухгалтером"),
    ("C16:EXECUTING", "Счёт оплачен", "оплату подтвердил бухгалтер"),
    ("C16:CONNECTED", "Подключён", "кабинет подключён"),
)


def _steps_by_stage() -> dict[str, dict]:
    """Что агент делает на каждом этапе — считаем ТЕМ ЖЕ кодом, что уходит агенту в промпт."""
    import tg_agent

    out: dict[str, dict] = {}
    for stage_id, _title, _trigger in CHAIN:
        # Номер сделки в тексте шага — заглушка: страница показывает шаг ЭТАПА, а не конкретной
        # сделки. Без заглушки в текст подставлялось «сделку None».
        deal = {"deal_id": "<номер сделки>", "stage_id": stage_id, "custom_fields": {}}
        try:
            step = tg_agent.funnel_next_step(deal)
        except Exception:  # noqa: BLE001 — страница не должна падать из-за одного этапа
            log.warning("шаг этапа %s не посчитан", stage_id, exc_info=True)
            continue
        out[stage_id] = {"step": step.get("step", ""), "need": step.get("need", ""),
                         "action": step.get("action", "")}
    return out


def _deal_counts() -> dict[str, int]:
    """Сколько сделок стоит на каждом этапе — чтобы цепочка была живой, а не схемой."""
    try:
        from mcp import context_server as cs
        rows = cs._crm_call("crm.deal.list", {
            "filter": {"CATEGORY_ID": funnel_rules.STAGE_NEW.split(":")[0].lstrip("C") or 16},
            "select": ["ID", "STAGE_ID"], "order": {"ID": "DESC"}}).get("result") or []
    except Exception:  # noqa: BLE001 — без счётчиков страница всё равно полезна
        log.warning("счётчики сделок недоступны", exc_info=True)
        return {}
    counts: dict[str, int] = {}
    for row in rows:
        stage = str(row.get("STAGE_ID") or "")
        counts[stage] = counts.get(stage, 0) + 1
    return counts


@app.get("/api/agent-center/funnel/map")
def funnel_map():
    """Логическая цепочка воронки + правила агента с причинами появления."""
    try:
        steps, counts = _steps_by_stage(), _deal_counts()
        chain = [{
            "stage_id": stage_id,
            "title": title,
            "trigger": trigger,
            "deals": counts.get(stage_id, 0),
            **steps.get(stage_id, {}),
        } for stage_id, title, trigger in CHAIN]
        rules = [{
            "slot": "Ход по сообщению клиента" if r.slot == "message" else "Сторож анкеты",
            "priority": r.priority,
            "name": r.name,
            "action": r.action,
            "origin": r.origin,
        } for r in sorted(funnel_rules.RULES, key=lambda r: (r.slot != "message", r.priority))]
        return jsonify({
            "chain": chain,
            "rules": rules,
            "invariants": [
                "У человека в воронке ровно одна сделка — дубль от анкеты склеивается.",
                "Сделка заводится только при интересе к ИУ: поставщики и болтовня в воронку не идут.",
                "Условия уходят дословно из документа и один раз.",
                "Анкету агент замечает сам — клиенту не нужно писать «заполнил».",
                "Одни и те же данные анкеты сверяются один раз, изменённые — заново.",
                "Этап в CRM всегда догоняет факт: ответили → «Связались», анкета → «Анкета заполнена».",
                "Агент не обещает того, чего не сделает: расчёт экономики, артикул, сроки от себя.",
                "На что знает ответ — отвечает; чего нет в источниках — уносит людям.",
            ],
        })
    except Exception:  # noqa: BLE001
        log.exception("карта воронки не собрана")
        return jsonify({"error": "Не удалось собрать карту воронки."}), 500


@app.get("/api/agent-center/funnel/decisions")
def funnel_decisions():
    """Трасса решений: какое правило сработало, на каких фактах и что вышло."""
    limit = min(int(request.args.get("limit") or 60), 300)
    dialog = (request.args.get("dialog") or "").strip()
    sql = ("SELECT created_at, dialog_id, deal_id, slot, rule, action, origin, facts, outcome"
           " FROM agent_decisions")
    params: list = []
    if dialog.isdigit():
        sql += " WHERE dialog_id = %s"
        params.append(int(dialog))
    sql += " ORDER BY id DESC LIMIT %s"
    params.append(limit)
    try:
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                rows = list(cur.fetchall())
    except Exception:  # noqa: BLE001
        log.exception("трасса решений не прочитана")
        return jsonify({"error": "Не удалось прочитать трассу решений."}), 500
    return jsonify({"decisions": [{
        "at": str(r["created_at"])[:19],
        "dialog_id": str(r["dialog_id"]),
        "deal_id": r["deal_id"],
        "slot": "сообщение" if r["slot"] == "message" else "сторож",
        "rule": r["rule"],
        "action": r["action"],
        "origin": r["origin"],
        "facts": r["facts"],
        "outcome": r["outcome"],
    } for r in rows]})


log.info("funnel_view loaded: /api/agent-center/funnel/* routes registered")
