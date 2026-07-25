# -*- coding: utf-8 -*-
"""«Работа с воронками» в кабинете: выбор воронки, её сценарий и управление агентом.

Владелец 25.07.2026: «сделаем инструмент „Работа с воронками“, внутри можно выбрать воронку и
сценарий настраивать в неё, чтобы этим можно было прям управлять».

Что отдаётся и почему именно так:
  • список воронок Битрикса — выбор, с какой работаем;
  • цепочка этапов выбранной воронки (этапы читаются из Битрикса, а не зашиты) + шаг агента на
    каждом: чего он ждёт от клиента и что делает. Шаг считается тем же кодом, что уходит агенту в
    промпт, поэтому страница не может разъехаться с поведением;
  • правила реестра с приоритетом и ПРИЧИНОЙ появления каждого — только чтение: условия завязаны
    на факты и закрыты тестами, править их мышкой значит вернуть расползание поведения;
  • ТЕКСТ шага владелец правит сам — он уходит в промпт, и формулировки для своих клиентов
    владелец знает лучше. Каждая правка пишется в историю (кто, когда, с чего на что);
  • выключатель агента на воронке: владелец может остановить автоответы сам.
"""
from __future__ import annotations

import logging

from flask import jsonify, request, session

import funnel_rules
import funnel_scenario
from app import app, pg_connect  # noqa: E402

log = logging.getLogger("funnel_view")

# Воронка, на которой работает агент ИУ. Остальные видны, но сценария у них пока нет.
AGENT_FUNNEL_ID = 16

INVARIANTS = (
    "У человека в воронке ровно одна сделка — дубль от анкеты склеивается.",
    "Сделка заводится только при интересе к ИУ: поставщики и болтовня в воронку не идут.",
    "Условия уходят дословно из документа и один раз.",
    "Анкету агент замечает сам — клиенту не нужно писать «заполнил».",
    "Одни и те же данные анкеты сверяются один раз, изменённые — заново.",
    "Этап в CRM всегда догоняет факт: ответили → «Связались», анкета → «Анкета заполнена».",
    "Агент не обещает того, чего не сделает: расчёт экономики, артикул, сроки от себя.",
    "На что знает ответ — отвечает; чего нет в источниках — уносит людям.",
)

# Когда наступает этап — короткие подсказки для воронки агента. Для остальных воронок берём
# то, что владелец напишет сам.
TRIGGERS = {
    funnel_rules.STAGE_NEW: "написал про ИУ — сделка заводится сразу",
    funnel_rules.STAGE_CONTACTED: "ответ доставлен клиенту",
    funnel_rules.STAGE_FORM_DONE: "в сделке появились данные анкеты",
    funnel_rules.STAGE_TERMS: "клиент подтвердил анкету",
}

MAX_FIELD = 4000


def _author() -> str:
    """Кто правит сценарий — для истории. Кабинет за админской сессией."""
    for key in ("user_name", "full_name", "login", "user"):
        value = session.get(key)
        if value:
            return str(value)[:120]
    return "кабинет"


def _funnels() -> list[dict]:
    """Воронки сделок из Битрикса."""
    from mcp import context_server as cs
    res = cs._crm_call("crm.category.list", {"entityTypeId": 2}).get("result") or {}
    rows = res.get("categories") if isinstance(res, dict) else res
    return [{"id": int(r.get("id")), "name": str(r.get("name") or f"Воронка {r.get('id')}")}
            for r in (rows or []) if str(r.get("id", "")).strip() != ""]


def _stages(funnel_id: int) -> list[dict]:
    """Этапы воронки — из Битрикса: переименовали этап, страница показала новое имя."""
    from mcp import context_server as cs
    entity = f"DEAL_STAGE_{funnel_id}" if funnel_id else "DEAL_STAGE"
    rows = cs._crm_call("crm.status.list", {"filter": {"ENTITY_ID": entity},
                                           "order": {"SORT": "ASC"}}).get("result") or []
    return [{"stage_id": str(r.get("STATUS_ID")), "title": str(r.get("NAME") or "")}
            for r in rows]


def _deal_counts(funnel_id: int) -> dict[str, int]:
    """Сколько сделок стоит на каждом этапе — чтобы цепочка была живой, а не схемой."""
    try:
        from mcp import context_server as cs
        rows = cs._crm_call("crm.deal.list", {"filter": {"CATEGORY_ID": funnel_id},
                                              "select": ["ID", "STAGE_ID"],
                                              "order": {"ID": "DESC"}}).get("result") or []
    except Exception:  # noqa: BLE001 — без счётчиков страница всё равно полезна
        log.warning("счётчики сделок воронки %s недоступны", funnel_id, exc_info=True)
        return {}
    counts: dict[str, int] = {}
    for row in rows:
        stage = str(row.get("STAGE_ID") or "")
        counts[stage] = counts.get(stage, 0) + 1
    return counts


def _code_step(stage_id: str) -> dict:
    """Шаг из КОДА — тот самый, что уходит агенту в промпт (без настроек владельца)."""
    import tg_agent
    deal = {"deal_id": "<номер сделки>", "stage_id": stage_id, "custom_fields": {}}
    try:
        step = tg_agent.funnel_next_step(deal)
    except Exception:  # noqa: BLE001 — страница не должна падать из-за одного этапа
        log.warning("шаг этапа %s не посчитан", stage_id, exc_info=True)
        return {}
    return {"step": step.get("step", ""), "need": step.get("need", ""),
            "action": step.get("action", "")}


def _saved_scenario(funnel_id: int) -> dict[str, dict]:
    """Настройки этапов, сохранённые владельцем."""
    try:
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT stage_id, trigger, need, action, enabled, updated_at,"
                            " updated_by FROM funnel_scenarios WHERE funnel_id = %s", (funnel_id,))
                rows = list(cur.fetchall())
    except Exception:  # noqa: BLE001
        log.warning("настройки воронки %s не прочитаны", funnel_id, exc_info=True)
        return {}
    return {str(r["stage_id"] or ""): {
        "trigger": r["trigger"] or "", "need": r["need"] or "", "action": r["action"] or "",
        "enabled": bool(r["enabled"]),
        "updated_at": str(r["updated_at"])[:19], "updated_by": r["updated_by"] or "",
    } for r in rows}


@app.get("/api/agent-center/funnels")
def funnels_list():
    """Список воронок для выбора в инструменте."""
    try:
        funnels = _funnels()
    except Exception:  # noqa: BLE001
        log.exception("список воронок не получен")
        return jsonify({"error": "Не удалось получить список воронок из Битрикса."}), 500
    saved = {}
    try:
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT funnel_id, count(*) c FROM funnel_scenarios"
                            " WHERE stage_id <> '' GROUP BY funnel_id")
                saved = {int(r["funnel_id"]): int(r["c"]) for r in cur.fetchall()}
    except Exception:  # noqa: BLE001
        log.warning("сводка настроек не прочитана", exc_info=True)
    return jsonify({"funnels": [{
        **f,
        "agent": f["id"] == AGENT_FUNNEL_ID,
        "customized_stages": saved.get(f["id"], 0),
        "enabled": funnel_scenario.agent_enabled(pg_connect, f["id"]),
    } for f in funnels]})


@app.get("/api/agent-center/funnel/<int:funnel_id>/map")
def funnel_map(funnel_id: int):
    """Сценарий воронки: этапы, шаги агента, правила и инварианты."""
    try:
        stages, counts = _stages(funnel_id), _deal_counts(funnel_id)
        saved = _saved_scenario(funnel_id)
        chain = []
        for stage in stages:
            stage_id = stage["stage_id"]
            code = _code_step(stage_id) if funnel_id == AGENT_FUNNEL_ID else {}
            custom = saved.get(stage_id) or {}
            chain.append({
                "stage_id": stage_id,
                "title": stage["title"],
                "trigger": custom.get("trigger") or TRIGGERS.get(stage_id, ""),
                "deals": counts.get(stage_id, 0),
                "step": code.get("step", ""),
                # Что реально уйдёт агенту: настройка владельца поверх кода.
                "need": custom.get("need") or code.get("need", ""),
                "action": custom.get("action") or code.get("action", ""),
                "code_need": code.get("need", ""),
                "code_action": code.get("action", ""),
                "customized": bool(custom.get("need") or custom.get("action")
                                   or custom.get("trigger")),
                "updated_at": custom.get("updated_at", ""),
                "updated_by": custom.get("updated_by", ""),
            })
        rules = [{
            "slot": "Ход по сообщению клиента" if r.slot == "message" else "Сторож анкеты",
            "priority": r.priority, "name": r.name, "action": r.action, "origin": r.origin,
        } for r in sorted(funnel_rules.RULES, key=lambda r: (r.slot != "message", r.priority))]
        return jsonify({
            "funnel_id": funnel_id,
            "agent": funnel_id == AGENT_FUNNEL_ID,
            "enabled": funnel_scenario.agent_enabled(pg_connect, funnel_id),
            "chain": chain,
            "rules": rules if funnel_id == AGENT_FUNNEL_ID else [],
            "invariants": list(INVARIANTS) if funnel_id == AGENT_FUNNEL_ID else [],
        })
    except Exception:  # noqa: BLE001
        log.exception("карта воронки %s не собрана", funnel_id)
        return jsonify({"error": "Не удалось собрать карту воронки."}), 500


@app.put("/api/agent-center/funnel/<int:funnel_id>/stage")
def funnel_stage_save(funnel_id: int):
    """Сохранить сценарий этапа: чего агент ждёт и что делает.

    Пустое поле = «вернуть как в коде»: владелец всегда может откатиться к базовому сценарию."""
    body = request.get_json(silent=True) or {}
    stage_id = str(body.get("stage_id") or "").strip()
    if not stage_id:
        return jsonify({"error": "Не указан этап."}), 400
    fields = {name: str(body.get(name) or "").strip()[:MAX_FIELD]
              for name in ("trigger", "need", "action")}
    author = _author()
    try:
        before = (_saved_scenario(funnel_id).get(stage_id) or {})
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO funnel_scenarios (funnel_id, stage_id, trigger, need, action,"
                    " updated_by) VALUES (%s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT (funnel_id, stage_id) DO UPDATE SET trigger = EXCLUDED.trigger,"
                    " need = EXCLUDED.need, action = EXCLUDED.action, updated_at = now(),"
                    " updated_by = EXCLUDED.updated_by",
                    (funnel_id, stage_id, fields["trigger"], fields["need"], fields["action"],
                     author))
                for name, value in fields.items():
                    old = str(before.get(name) or "")
                    if old != value:
                        cur.execute(
                            "INSERT INTO funnel_scenario_history (funnel_id, stage_id, field,"
                            " old_value, new_value, author) VALUES (%s, %s, %s, %s, %s, %s)",
                            (funnel_id, stage_id, name, old[:MAX_FIELD], value, author))
    except Exception:  # noqa: BLE001
        log.exception("сценарий этапа %s воронки %s не сохранён", stage_id, funnel_id)
        return jsonify({"error": "Не удалось сохранить сценарий этапа."}), 500
    funnel_scenario.invalidate()      # агент подхватит настройку сразу, а не через минуту
    log.info("сценарий этапа %s воронки %s изменён из кабинета (%s)", stage_id, funnel_id, author)
    return jsonify({"saved": True, "stage_id": stage_id})


@app.post("/api/agent-center/funnel/<int:funnel_id>/enabled")
def funnel_toggle(funnel_id: int):
    """Включить или остановить агента на воронке."""
    body = request.get_json(silent=True) or {}
    enabled = bool(body.get("enabled"))
    author = _author()
    try:
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO funnel_scenarios (funnel_id, stage_id, enabled, updated_by)"
                    " VALUES (%s, '', %s, %s)"
                    " ON CONFLICT (funnel_id, stage_id) DO UPDATE SET enabled = EXCLUDED.enabled,"
                    " updated_at = now(), updated_by = EXCLUDED.updated_by",
                    (funnel_id, enabled, author))
                cur.execute(
                    "INSERT INTO funnel_scenario_history (funnel_id, stage_id, field, old_value,"
                    " new_value, author) VALUES (%s, '', 'enabled', %s, %s, %s)",
                    (funnel_id, str(not enabled), str(enabled), author))
    except Exception:  # noqa: BLE001
        log.exception("переключатель воронки %s не сохранён", funnel_id)
        return jsonify({"error": "Не удалось переключить агента."}), 500
    funnel_scenario.invalidate()
    log.info("агент на воронке %s %s из кабинета (%s)", funnel_id,
             "включён" if enabled else "ОСТАНОВЛЕН", author)
    return jsonify({"enabled": enabled})


@app.get("/api/agent-center/funnel/<int:funnel_id>/history")
def funnel_history(funnel_id: int):
    """История правок сценария: кто, когда и что именно поменял."""
    try:
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT created_at, stage_id, field, old_value, new_value, author"
                    " FROM funnel_scenario_history WHERE funnel_id = %s"
                    " ORDER BY id DESC LIMIT 50", (funnel_id,))
                rows = list(cur.fetchall())
    except Exception:  # noqa: BLE001
        log.exception("история правок воронки %s не прочитана", funnel_id)
        return jsonify({"error": "Не удалось прочитать историю правок."}), 500
    return jsonify({"history": [{
        "at": str(r["created_at"])[:19], "stage_id": r["stage_id"], "field": r["field"],
        "old": (r["old_value"] or "")[:400], "new": (r["new_value"] or "")[:400],
        "author": r["author"],
    } for r in rows]})


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


log.info("funnel_view loaded: /api/agent-center/funnel* routes registered")
