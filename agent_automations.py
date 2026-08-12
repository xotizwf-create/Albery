"""Per-agent scheduled automations (Центр Агента → Агенты → «Автоматизации»).

Each agent (the universal/main one and every subagent) keeps its own list of cron
automations in `agent_automations`. Two kinds:
  - kind='agent'  — executed HERE by a scheduler thread: one `hermes -z` turn on the
    agent's automation alias (`-t automation-agent-<slug>`), so the run is bounded by
    exactly the tools/instructions the owner enabled for that agent. PostgreSQL owns the
    queue, stage leases, effect ledger and delivery retry; the Hermes subprocess consumes
    the same global run-slot pool as live Bitrix/Telegram turns.
  - kind='system' — mirror/control rows for legacy Hermes cron jobs that live on
    the box (`hermes cron list`: zoom-to-tasks, owner-daily, owner-weekly, leader-digest);
    their external executors remain outside this durable agent-automation worker.

Registers routes on the shared Flask `app` at import time (same pattern as b24bot /
agent_center); agent_center imports this module at its bottom — app.py stays frozen.
Imports from agent_center/b24bot are lazy (inside functions) per the project's
circular-import rule.
"""
from __future__ import annotations

import logging
import json
import os
import re
import socket
import subprocess
import threading
import time
import uuid

from datetime import timedelta
from typing import Any

from flask import jsonify, request

from app import MSK_TZ, app, msk_now, pg_connect
import cron_schedule
from shared.role import background_jobs_enabled

_AUTOMATION_TIMEOUT_S = int(os.getenv("AGENT_AUTOMATION_TIMEOUT_S", "300"))
# A 'running' row older than timeout+retry window+slack means the process restarted
# mid-run — treat it as interrupted (self-heals: display + run-now unblock).
_RUNNING_STALE_S = _AUTOMATION_TIMEOUT_S * 2 + 900
# Count ceiling per agent. This is NOT the overload guard — the worker pool below is (every
# fire is queued and executed at most _AUTOMATION_WORKERS at a time, so N automations never
# spawn N parallel LLM turns). So the count only needs to be a generous anti-runaway ceiling,
# not a functional limit: raising it from the old 10 lets real fleets (e.g. one annual
# birthday reminder per employee, task 594) coexist. Per-automation frequency stays capped
# separately (_SELF_MAX_FIRES_PER_DAY). Override with AGENT_SELF_AUTOMATIONS_MAX.
_SELF_AUTOMATIONS_MAX = int(os.getenv("AGENT_SELF_AUTOMATIONS_MAX", "100"))
# Every run is a full LLM turn — frequency is capped hard. The owner (UI) may go down
# to every 15 minutes; an agent scheduling itself from chat — at most hourly.
_OWNER_MAX_FIRES_PER_DAY = 96
_SELF_MAX_FIRES_PER_DAY = 24
_NAME_MAX = 80
_TASK_MAX = 4000
_RESULT_KEEP = 2000

# PostgreSQL is the queue; one worker advances durable stages. The heavy Hermes stage uses
# shared.run_slots, so the real host limit covers live Bitrix, Telegram and agent automation.
# Delivery is a separate light stage and never repeats a successful brain/tool stage.
_AUTOMATION_WORKERS = 1
_RETRY_DELAY_S = int(os.getenv("AGENT_AUTOMATION_RETRY_DELAY_S", "120"))
_QUEUE_POLL_S = float(os.getenv("AGENT_AUTOMATION_QUEUE_POLL_S", "1"))
_SLOT_WAIT_S = float(os.getenv("AGENT_AUTOMATION_SLOT_WAIT_S", "30"))
_LEASE_S = _AUTOMATION_TIMEOUT_S + 120
_MAX_BRAIN_ATTEMPTS = 2
_MAX_DELIVERY_ATTEMPTS = 3
_WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


# --- Storage ---------------------------------------------------------------------------------

_COLS = ("id, agent_slug, name, description, schedule, prompt, deliver_to, delivery_channel, "
         "delivery_profile, delivery_conversation_id, kind, created_by, "
         "creator_label, is_active, last_run_at, last_status, last_result, last_error, created_at, "
         "system_key")


def _load_rows(where: str = "", args: tuple = ()) -> list[dict[str, Any]]:
    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_COLS} FROM agent_automations {where} "
                "ORDER BY (kind = 'system') DESC, id",
                args,
            )
            return list(cur.fetchall())


def _row_by_id(auto_id: int) -> dict[str, Any] | None:
    rows = _load_rows("WHERE id = %s", (auto_id,))
    return rows[0] if rows else None


def _when(dt: Any) -> str:
    if not dt:
        return ""
    try:
        return dt.astimezone(MSK_TZ).strftime("%d.%m %H:%M")
    except Exception:  # noqa: BLE001
        return ""


def _running_is_stale(r: dict[str, Any]) -> bool:
    if r.get("last_status") != "running" or not r.get("last_run_at"):
        return False
    try:
        return (msk_now() - r["last_run_at"].astimezone(MSK_TZ)).total_seconds() > _RUNNING_STALE_S
    except Exception:  # noqa: BLE001
        return False


# --- «Кто создал» for the tab's creator filter --------------------------------------------
# creator_label is free text ("владелец (панель)", "Hermes cron · owner-daily",
# "агент «X» (сам) · по просьбе: пользователь Bitrix24 id=30"). Derive a CLEAN person/creator
# label for grouping, resolving a Bitrix id to the employee's name. The raw label stays as tooltip.
_USER_NAMES_CACHE: dict[str, Any] = {"at": 0.0, "map": {}}
_BITRIX_ID_RE = re.compile(r"id\s*=?\s*(\d+)")
_REQUESTED_BY_RE = re.compile(r"по\s+просьбе:\s*(.+)$", re.IGNORECASE)


def _user_names() -> dict[int, str]:
    """bitrix_user_id → ФИО, 60s cache."""
    now = time.time()
    if now - float(_USER_NAMES_CACHE["at"] or 0) < 60 and _USER_NAMES_CACHE["map"]:
        return _USER_NAMES_CACHE["map"]
    names: dict[int, str] = {}
    try:
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT bitrix_user_id, full_name FROM users "
                            "WHERE bitrix_user_id IS NOT NULL AND COALESCE(full_name,'') <> ''")
                names = {int(r["bitrix_user_id"]): r["full_name"] for r in cur.fetchall()}
    except Exception:  # noqa: BLE001
        logging.exception("automations: user-names load failed")
    _USER_NAMES_CACHE.update(at=now, map=names)
    return names


def _owner_name(names: dict[int, str]) -> str:
    """The owner's real name (first configured owner id, default 16 = Александр Никитенко)."""
    for i in re.findall(r"\d+", os.getenv("B24_TESTBOT_OWNER_USER_IDS", "16")):
        if int(i) in names:
            return names[int(i)]
    return "Владелец"


def _creator_display(created_by: str, creator_label: str, kind: str, names: dict[int, str]) -> str:
    """Clean «кто ФАКТИЧЕСКИ создал» label for the creator filter — a real person's name
    (owner → Александр Никитенко; an employee who asked the agent → that employee), or
    «Hermes (система)» for the built-in server crons. Never the participant/responsible."""
    cl = (creator_label or "").strip()
    low = cl.lower()
    if kind == "system" or low.startswith("hermes cron") or "системн" in low:
        return "Hermes (система)"
    m = _REQUESTED_BY_RE.search(cl)  # self-created: «… · по просьбе: <кто>»
    if m:
        who = m.group(1).strip()
        wid = _BITRIX_ID_RE.search(who)
        if wid and int(wid.group(1)) in names:
            return names[int(wid.group(1))]
        return _owner_name(names) if who.lower() == "владелец" else who
    if created_by == "owner":
        return _owner_name(names)
    return cl or "—"


def _schedule_view(schedule: str, is_active: bool) -> tuple[Any, str]:
    """Next run + human label with per-row degradation: one malformed stored schedule
    must surface as that row's own warning, never as a 500 for the whole list
    (a registry row with a comment inside the cron field broke the tab, 2026-07-17)."""
    try:
        nxt = cron_schedule.next_run(schedule, msk_now()) if is_active else None
        return nxt, cron_schedule.describe(schedule)
    except ValueError:
        return None, f"⚠ некорректное расписание «{schedule}» — нужно 5 полей cron, исправьте строку"


def _automation_json(r: dict[str, Any], names: dict[int, str] | None = None) -> dict[str, Any]:
    names = names if names is not None else _user_names()
    nxt, schedule_label = _schedule_view(r["schedule"], bool(r["is_active"]))
    status = r["last_status"] or ""
    if _running_is_stale(r):
        status = "interrupted"
    # System rows are live-editable when their executor is known (system_key, migration 057).
    system_manageable = r["kind"] != "system" or bool(r.get("system_key"))
    return {
        "id": r["id"],
        "agent_slug": r["agent_slug"],
        "name": r["name"],
        "description": r["description"] or "",
        "schedule": r["schedule"],
        "schedule_label": schedule_label,
        "can_edit": system_manageable,
        "can_run": system_manageable and (r.get("system_key") or "").partition(":")[0] != "app",
        "prompt": r["prompt"] or "",
        "deliver_to": r["deliver_to"] or "",
        "delivery_channel": r.get("delivery_channel") or "bitrix",
        "delivery_profile": r.get("delivery_profile") or r["agent_slug"],
        "delivery_conversation_id": r.get("delivery_conversation_id") or r["deliver_to"] or "",
        "kind": r["kind"],
        "created_by": r["created_by"],
        "creator_label": r["creator_label"] or "",
        "creator": _creator_display(r["created_by"], r["creator_label"] or "", r["kind"], names),
        "is_active": bool(r["is_active"]),
        "next_run": _when(nxt),
        "last_run": _when(r["last_run_at"]),
        "last_status": status,
        "last_result": r["last_result"] or "",
        "last_error": r["last_error"] or "",
    }


# --- Recurring Bitrix tasks shown as kind='task' rows -----------------------------------------
# The owner asked that a recurring TASK requested in chat is visible in the same «Автоматизации»
# tab. The rows live in bitrix_recurring_tasks and are fired by recurring_scheduler.py
# DETERMINISTICALLY (a plain tasks.task.add, no LLM turn — so they cost nothing per fire and
# don't count against the automation frequency caps). Here we only render/manage them.

def _recurring_json(r: dict[str, Any], names: dict[int, str] | None = None) -> dict[str, Any]:
    names = names if names is not None else _user_names()
    spec = r.get("spec")
    if isinstance(spec, str):
        try:
            import json as _json
            spec = _json.loads(spec)
        except Exception:  # noqa: BLE001
            spec = {}
    spec = spec if isinstance(spec, dict) else {}
    parts = [f"Создаёт задачу в Bitrix: «{r['title']}»"]
    if r.get("responsible_name"):
        parts.append("исполнитель — " + str(r["responsible_name"]))
    if r.get("deadline_desc"):
        parts.append("дедлайн " + str(r["deadline_desc"]))
    if r.get("result_criteria"):
        parts.append("результат: " + str(r["result_criteria"]))
    if spec.get("checklist"):
        parts.append(f"чек-лист из {len(spec['checklist'])} пунктов")
    status, result = "", ""
    if r.get("last_error"):
        status = "error"
    elif r.get("last_task_id"):
        status, result = "ok", f"Создана задача №{r['last_task_id']}"
    return {
        # Negative id keeps React keys/busy-tracking unique next to real automations;
        # the API identifier for recurring endpoints is recurring_id.
        "id": -int(r["id"]),
        "recurring_id": int(r["id"]),
        "agent_slug": r.get("agent_slug") or "main",
        "name": r["title"],
        "description": "",
        "schedule": "",
        "schedule_label": r.get("schedule_desc") or "",
        "prompt": ", ".join(parts),
        "deliver_to": "",
        "kind": "task",
        "created_by": "self",
        "creator_label": "агент (из чата)",
        # The person who actually CREATED/requested this recurring task (creator_bitrix_id),
        # resolved to a name — NOT the responsible/participant. Task 1556: filter by who created it.
        "creator": (names.get(int(r["creator_bitrix_id"]))
                    if r.get("creator_bitrix_id") and int(r["creator_bitrix_id"]) in names
                    else "Из чата"),
        "is_active": bool(r.get("active")),
        "next_run": _when(r.get("next_run_at")),
        "last_run": _when(r.get("last_created_at")),
        "last_status": status,
        "last_result": result,
        "last_error": r.get("last_error") or "",
        # Machine-readable schedule for the tab's day/time editor. daily = all 7 days;
        # monthly rows get no weekday list (the editor offers only the time there).
        "period": r.get("period") or "daily",
        "weekdays": (list(r.get("weekdays") or []) if (r.get("period") or "daily") == "weekly"
                     else ([1, 2, 3, 4, 5, 6, 7] if (r.get("period") or "daily") == "daily" else [])),
        "create_time": r.get("create_time") or "",
    }


_RECURRING_COLS = ("id, title, responsible_name, creator_bitrix_id, schedule_desc, deadline_desc, "
                   "result_criteria, active, next_run_at, last_created_at, last_task_id, last_error, "
                   "spec, agent_slug, period, weekdays, day_of_month, create_time")


def _recurring_rows(where: str, args: tuple) -> list[dict[str, Any]]:
    try:
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT {_RECURRING_COLS} FROM bitrix_recurring_tasks {where} ORDER BY id", args)
                return list(cur.fetchall())
    except Exception:  # noqa: BLE001
        logging.exception("recurring tasks load failed")
        return []


def _validate(name: str, schedule: str, prompt: str, max_per_day: int) -> str | None:
    """Returns a user-facing error string, or None when the automation is valid."""
    if not name:
        return "Укажите название автоматизации."
    if len(name) > _NAME_MAX:
        return f"Название длиннее {_NAME_MAX} символов."
    if not prompt:
        return "Опишите задачу: что агент должен делать при каждом запуске."
    if len(prompt) > _TASK_MAX:
        return f"Задача длиннее {_TASK_MAX} символов — сократите."
    try:
        fires = cron_schedule.max_fires_per_day(schedule)
    except ValueError as exc:
        return f"Расписание: {exc}"
    if fires > max_per_day:
        per = "15 минут" if max_per_day == _OWNER_MAX_FIRES_PER_DAY else "час"
        return (f"Слишком часто ({fires} запусков/сутки): каждый запуск — полноценный ход агента. "
                f"Минимальный интервал — раз в {per}.")
    return None


# --- REST API (behind the site's admin session, like the rest of /api/agent-center) ----------

@app.get("/api/agent-center/agents/<slug>/automations")
def agent_automations_list(slug: str):
    try:
        names = _user_names()
        rows = _load_rows("WHERE agent_slug = %s", (slug,))
        payload = [_automation_json(r, names) for r in rows]
        # Recurring Bitrix tasks of this agent ride along as kind='task' rows.
        payload += [_recurring_json(r, names)
                    for r in _recurring_rows("WHERE COALESCE(agent_slug, 'main') = %s", (slug,))]
        return jsonify({"automations": payload})
    except Exception:  # noqa: BLE001
        logging.exception("agent automations list failed: %s", slug)
        return jsonify({"error": "Не удалось загрузить автоматизации."}), 500


# --- Recurring-task rows management (the kind='task' rows of the same tab) -------------------

@app.patch("/api/agent-center/recurring-tasks/<int:rec_id>")
def recurring_task_update(rec_id: int):
    body = request.get_json(silent=True) or {}
    # Schedule edit (day-of-week chips + time in the tab editor) — shared helper with the
    # update_recurring_task MCP tool; recomputes deadline offset, human text and next_run_at.
    schedule_changes = {k: body.get(k) for k in ("weekdays", "create_time", "deadline_time")
                        if body.get(k) is not None}
    if schedule_changes:
        try:
            from mcp.context_server import McpError, apply_recurring_update
            apply_recurring_update(rec_id, schedule_changes)
        except McpError as exc:
            return jsonify({"error": exc.message}), 400
        except Exception:  # noqa: BLE001
            logging.exception("recurring task schedule edit failed: %s", rec_id)
            return jsonify({"error": "Не удалось изменить расписание."}), 500
        if body.get("is_active") is None:
            return jsonify({"ok": True})
    if body.get("is_active") is None:
        return jsonify({"error": "Нечего менять: передайте weekdays/create_time или is_active."}), 400
    is_active = bool(body.get("is_active"))
    try:
        rows = _recurring_rows("WHERE id = %s", (rec_id,))
        if not rows:
            return jsonify({"error": "Регулярная задача не найдена."}), 404
        next_run = None
        if is_active:
            # Re-enabling: recompute the next fire so a long-disabled row doesn't fire instantly
            # on a stale next_run_at from the past.
            import recurring_scheduler
            with pg_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT id, period, interval_every, weekdays, day_of_month, "
                                "create_time, until_date, created_at FROM bitrix_recurring_tasks "
                                "WHERE id = %s", (rec_id,))
                    full = dict(cur.fetchone())
            next_run = recurring_scheduler._compute_next(full, msk_now())
        with pg_connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    if is_active:
                        cur.execute("UPDATE bitrix_recurring_tasks SET active = TRUE, next_run_at = %s, "
                                    "last_error = NULL, updated_at = now() WHERE id = %s", (next_run, rec_id))
                    else:
                        cur.execute("UPDATE bitrix_recurring_tasks SET active = FALSE, updated_at = now() "
                                    "WHERE id = %s", (rec_id,))
        return jsonify({"ok": True})
    except Exception:  # noqa: BLE001
        logging.exception("recurring task update failed: %s", rec_id)
        return jsonify({"error": "Не удалось сохранить."}), 500


@app.delete("/api/agent-center/recurring-tasks/<int:rec_id>")
def recurring_task_delete(rec_id: int):
    try:
        with pg_connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM bitrix_recurring_tasks WHERE id = %s RETURNING id", (rec_id,))
                    if cur.fetchone() is None:
                        return jsonify({"error": "Регулярная задача не найдена."}), 404
        return jsonify({"ok": True})
    except Exception:  # noqa: BLE001
        logging.exception("recurring task delete failed: %s", rec_id)
        return jsonify({"error": "Не удалось удалить."}), 500


@app.post("/api/agent-center/recurring-tasks/<int:rec_id>/run")
def recurring_task_run_now(rec_id: int):
    """Create one task instance right now (verification button). Deterministic — no LLM turn;
    next_run_at is left untouched, so the regular schedule is unaffected."""
    try:
        from datetime import timedelta

        import recurring_scheduler
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, title, description, responsible_bitrix_id, creator_bitrix_id, "
                            "period, interval_every, weekdays, day_of_month, create_time, "
                            "deadline_after_seconds, until_date, spec, created_at "
                            "FROM bitrix_recurring_tasks WHERE id = %s", (rec_id,))
                row = cur.fetchone()
        if not row:
            return jsonify({"error": "Регулярная задача не найдена."}), 404
        row = dict(row)
        spec = recurring_scheduler._row_spec(row)
        if not spec.get("responsible_bitrix_id"):
            return jsonify({"error": "В записи нет исполнителя — создать задачу нельзя."}), 400
        now = msk_now()
        dl_secs = int(spec.get("deadline_after_seconds") or row.get("deadline_after_seconds") or 0)
        deadline_iso = (now + timedelta(seconds=dl_secs if dl_secs > 0 else 24 * 3600)).isoformat()
        from mcp import context_server as cs
        res = cs.create_oneoff_task_from_spec(spec, deadline_iso)
        task_id = res.get("task_id")
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE bitrix_recurring_tasks SET last_created_at = now(), last_task_id = %s, "
                            "last_error = NULL, updated_at = now() WHERE id = %s", (task_id, rec_id))
        return jsonify({"ok": True, "task_id": task_id})
    except Exception as exc:  # noqa: BLE001
        logging.exception("recurring task run-now failed: %s", rec_id)
        return jsonify({"error": f"Не удалось создать задачу: {str(exc)[:200]}"}), 502


@app.post("/api/agent-center/agents/<slug>/automations")
def agent_automations_create(slug: str):
    from agent_center import _agent_by_slug
    if not _agent_by_slug(slug):
        return jsonify({"error": "Агент не найден."}), 404
    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "").strip()
    schedule = str(body.get("schedule") or "").strip()
    prompt = str(body.get("prompt") or "").strip()
    problem = _validate(name, schedule, prompt, _OWNER_MAX_FIRES_PER_DAY)
    if problem:
        return jsonify({"error": problem}), 400
    delivery_channel = str(body.get("delivery_channel") or "bitrix").strip().lower()
    delivery_conversation_id = str(
        body.get("delivery_conversation_id") or body.get("deliver_to") or ""
    ).strip()
    if delivery_channel not in {"bitrix", "telegram"}:
        return jsonify({"error": "Канал доставки должен быть bitrix или telegram."}), 400
    if not delivery_conversation_id:
        return jsonify({"error": "Укажите диалог доставки."}), 400
    try:
        with pg_connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO agent_automations (agent_slug, name, description, schedule, "
                        "prompt, deliver_to, delivery_channel, delivery_profile, "
                        "delivery_conversation_id, kind, created_by, creator_label) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, "
                        "'agent', 'owner', 'владелец (панель)') "
                        "ON CONFLICT (agent_slug, name) DO NOTHING RETURNING id",
                        (slug, name, str(body.get("description") or "").strip(), schedule,
                         prompt, delivery_conversation_id,
                         delivery_channel,
                         str(body.get("delivery_profile") or slug).strip(),
                         delivery_conversation_id),
                    )
                    created = cur.fetchone()
        if not created:
            return jsonify({"error": "Автоматизация с таким названием уже есть у этого агента."}), 409
        return jsonify({"ok": True, "id": created["id"]})
    except Exception:  # noqa: BLE001
        logging.exception("agent automation create failed: %s", slug)
        return jsonify({"error": "Не удалось создать автоматизацию."}), 500


def _update_system_row(row: dict[str, Any], body: dict[str, Any]):
    """UI edits of a system row write THROUGH to the real executor (hermes cron / cron.d /
    the in-app thread that reads its row), then land in the registry. Deterministic jobs,
    not LLM turns — so the frequency cap is a sane 288/day (every 5 min), not the agent cap."""
    import system_automations
    schedule = str(body["schedule"]).strip() if body.get("schedule") is not None else None
    is_active = bool(body["is_active"]) if body.get("is_active") is not None else None
    if schedule is not None:
        try:
            if cron_schedule.max_fires_per_day(schedule) > 288:
                return jsonify({"error": "Слишком часто: минимальный интервал системной автоматизации — 5 минут."}), 400
        except ValueError as exc:
            return jsonify({"error": f"Расписание: {exc}"}), 400
    if schedule is not None or is_active is not None:
        problem = system_automations.edit_system(row, schedule, is_active)
        if problem:
            return jsonify({"error": problem}), 502
    name = str(body.get("name") if body.get("name") is not None else row["name"]).strip() or row["name"]
    description = str(body.get("description") if body.get("description") is not None else row["description"]).strip()
    try:
        with pg_connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE agent_automations SET name = %s, description = %s, schedule = %s, "
                        "is_active = %s, updated_at = now() WHERE id = %s",
                        (name, description,
                         schedule if schedule is not None else row["schedule"],
                         is_active if is_active is not None else bool(row["is_active"]),
                         row["id"]),
                    )
        return jsonify({"ok": True})
    except Exception:  # noqa: BLE001
        logging.exception("system automation update failed: %s", row["id"])
        return jsonify({"error": "Исполнитель обновлён, но запись в реестре не сохранилась — обновите страницу."}), 500


@app.patch("/api/agent-center/automations/<int:auto_id>")
def agent_automation_update(auto_id: int):
    row = _row_by_id(auto_id)
    if not row:
        return jsonify({"error": "Автоматизация не найдена."}), 404
    body = request.get_json(silent=True) or {}
    if row["kind"] == "system":
        if not row.get("system_key"):
            return jsonify({"error": "У этой системной автоматизации не указан исполнитель — правится только на сервере."}), 403
        return _update_system_row(row, body)
    name = str(body.get("name") if body.get("name") is not None else row["name"]).strip()
    schedule = str(body.get("schedule") if body.get("schedule") is not None else row["schedule"]).strip()
    prompt = str(body.get("prompt") if body.get("prompt") is not None else row["prompt"]).strip()
    problem = _validate(name, schedule, prompt, _OWNER_MAX_FIRES_PER_DAY)
    if problem:
        return jsonify({"error": problem}), 400
    description = str(body.get("description") if body.get("description") is not None else row["description"]).strip()
    deliver_to = str(body.get("deliver_to") if body.get("deliver_to") is not None else row["deliver_to"]).strip()
    delivery_channel = str(body.get("delivery_channel") if body.get("delivery_channel") is not None
                           else row.get("delivery_channel") or "bitrix").strip().lower()
    if delivery_channel not in {"bitrix", "telegram"}:
        return jsonify({"error": "Канал доставки должен быть bitrix или telegram."}), 400
    delivery_profile = str(body.get("delivery_profile") if body.get("delivery_profile") is not None
                           else row.get("delivery_profile") or row["agent_slug"]).strip()
    delivery_conversation_id = str(
        body.get("delivery_conversation_id")
        if body.get("delivery_conversation_id") is not None
        else row.get("delivery_conversation_id") or deliver_to
    ).strip()
    if not delivery_conversation_id:
        return jsonify({"error": "Укажите диалог доставки."}), 400
    deliver_to = delivery_conversation_id
    is_active = bool(body.get("is_active")) if body.get("is_active") is not None else bool(row["is_active"])
    try:
        with pg_connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE agent_automations SET name = %s, description = %s, schedule = %s, "
                        "prompt = %s, deliver_to = %s, delivery_channel = %s, "
                        "delivery_profile = %s, delivery_conversation_id = %s, is_active = %s, "
                        "updated_at = now() WHERE id = %s",
                        (name, description, schedule, prompt, deliver_to, delivery_channel,
                         delivery_profile, delivery_conversation_id, is_active, auto_id),
                    )
        return jsonify({"ok": True})
    except Exception:  # noqa: BLE001
        logging.exception("agent automation update failed: %s", auto_id)
        return jsonify({"error": "Не удалось сохранить (возможно, имя уже занято)."}), 500


@app.delete("/api/agent-center/automations/<int:auto_id>")
def agent_automation_delete(auto_id: int):
    row = _row_by_id(auto_id)
    if not row:
        return jsonify({"error": "Автоматизация не найдена."}), 404
    if row["kind"] == "system":
        return jsonify({"error": "Системную автоматизацию нельзя удалить из UI — её задание живёт на сервере. "
                                 "Выключите её тумблером, а удаление закажите у агента."}), 403
    try:
        with pg_connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM agent_automations WHERE id = %s", (auto_id,))
        return jsonify({"ok": True})
    except Exception:  # noqa: BLE001
        logging.exception("agent automation delete failed: %s", auto_id)
        return jsonify({"error": "Не удалось удалить."}), 500


@app.post("/api/agent-center/automations/<int:auto_id>/run")
def agent_automation_run_now(auto_id: int):
    row = _row_by_id(auto_id)
    if not row:
        return jsonify({"error": "Автоматизация не найдена."}), 404
    if row["kind"] == "system":
        import system_automations
        problem = system_automations.run_system(row)
        if problem:
            return jsonify({"error": problem}), 502
        return jsonify({"ok": True, "started": True,
                        "note": "Запуск передан исполнителю — статус обновится после завершения."})
    try:
        run_id = _enqueue_run(row, "manual", msk_now())
    except Exception:  # noqa: BLE001
        logging.exception("agent automation %s: atomic manual enqueue failed", auto_id)
        return jsonify({"error": "Не удалось поставить запуск в надёжную очередь."}), 500
    if run_id is None:
        return jsonify({"error": "Уже поставлена в очередь или выполняется."}), 409
    return jsonify({"ok": True, "started": True, "run_id": run_id})


# --- Execution -------------------------------------------------------------------------------

def _automation_prompt(agent: dict[str, Any], row: dict[str, Any]) -> str:
    role = (agent.get("role_prompt") or "").strip()
    head = (
        "[Служебный запуск по расписанию — автоматизация «" + row["name"] + "» агента «"
        + str(agent.get("name") or agent["slug"]) + "». Это НЕ сообщение пользователя: молча выполни "
        "задачу автоматизации и верни ГОТОВЫЙ текст, который будет отправлен в настроенный канал "
        "от твоего имени. ИЗОЛЯЦИЯ: ты автономный агент со СВОИМ набором инструментов и инструкций — "
        "работай ТОЛЬКО ими; другие агенты, их задачи и автоматизации тебя не касаются, не ссылайся "
        "на них и не пытайся выполнять чужую работу."
        + (" ТВОЯ РОЛЬ: " + role if role else "")
        + " Правила: пиши по-русски, кратко и по делу; БЕЗ Markdown (#, **, `, таблицы) — жирный "
        "только [b]...[/b], перечисления списком «- »; реальные данные бери ТОЛЬКО из инструментов, "
        "ничего не выдумывай. ЧЕСТНОСТЬ: если для задачи не хватает инструментов или данных — прямо "
        "напиши в ответе, чего не хватает, вместо предположений. ПРАВИЛО ТИШИНЫ: если сообщать нечего "
        "(нет новых данных/событий и задача подразумевает «только при изменениях»), ответь ровно одним "
        "словом SILENT — сообщение не отправится.]"
    )
    parts = [head]
    try:
        from agent_center import agent_selected_knowledge
        skills = agent_selected_knowledge(agent).get("skills") or []
    except Exception:  # noqa: BLE001
        logging.exception("automation %s: selected knowledge load failed", row["id"])
        skills = []
    if skills:
        parts.append("ТВОИ НАВЫКИ (подключены владельцем): "
                     + "; ".join(f"«{s['title']}» — {s['description']}" for s in skills))
        for s in skills:
            if s.get("content"):
                parts.append("ПОЛНЫЙ ТЕКСТ НАВЫКА «" + s["title"] + "» — следуй ему буквально:\n"
                             + s["content"])
    learned = agent.get("instructions") or []
    if learned:
        parts.append("ТВОИ ЛИЧНЫЕ ИНСТРУКЦИИ (применяй обязательно):\n"
                     + "\n\n".join(f"— {i['name']}:\n{i['content']}" for i in learned))
    parts.append("Текущие дата и время: " + msk_now().strftime("%d.%m.%Y %H:%M")
                 + " МСК — это «сегодня/сейчас» для любых расчётов.")
    parts.append("ЗАДАЧА АВТОМАТИЗАЦИИ:\n" + row["prompt"])
    return "\n\n".join(parts)


def _is_silent(answer: str) -> bool:
    return answer.strip().strip("«»\"'.").upper() == "SILENT"


def _bb_sanitize(text: str) -> str:
    """Единый санитайзер Markdown→BB живёт в b24bot (там же его применяют все ответы бота):
    таблицы, жирный, заголовки, ссылки, код, списки. Здесь — та же сетка перед доставкой."""
    from b24bot import bb_sanitize
    return bb_sanitize(text)


def _deliver(agent: dict[str, Any], row: dict[str, Any], text: str) -> tuple[bool, str | None]:
    """deliver_to supports SEVERAL comma-separated targets (user ids and/or chatNNN) — the owner
    wants some digests both in Никитенко's private dialog and to the «ИИ Агент» account. Success
    when at least one target got the message; failures are reported per target."""
    from b24bot import _albery_bitrix_notify
    raw = (row["deliver_to"] or "").strip() or os.getenv("ALBERY_BITRIX_NOTIFY_CHAT", "chat728")
    targets = [t.strip() for t in raw.replace(";", ",").split(",") if t.strip()]
    message = "[b]⏰ " + row["name"] + "[/b]\n" + _bb_sanitize(text)
    errors: list[str] = []
    delivered_any = False
    for target in targets:
        ok, err = _albery_bitrix_notify(message, dialog_id=target, bot_id=agent.get("bitrix_bot_id"))
        if ok:
            delivered_any = True
        else:
            errors.append(f"{target}: {err}")
    if errors and delivered_any:  # partial failure must not fail the run, but must be visible
        logging.warning("agent automation %s: partial delivery failure: %s", row["id"], "; ".join(errors))
    return delivered_any, ("; ".join(errors) if errors else None)


def _run_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "agent_slug": row["agent_slug"],
        "name": row["name"],
        "prompt": row.get("prompt") or "",
        "deliver_to": row.get("deliver_to") or "",
        "delivery_channel": row.get("delivery_channel") or "bitrix",
        "delivery_profile": row.get("delivery_profile") or row["agent_slug"],
        "delivery_conversation_id": (
            row.get("delivery_conversation_id") or row.get("deliver_to") or ""
        ),
    }


def _enqueue_run(row: dict[str, Any], trigger_kind: str, scheduled_for) -> int | None:
    """Atomically persist one manual or scheduled fire; None means duplicate/active."""
    auto_id = int(row["id"])
    if trigger_kind == "schedule":
        minute = scheduled_for.replace(second=0, microsecond=0)
        idem = f"schedule:{auto_id}:{minute.isoformat()}"
    else:
        idem = f"manual:{auto_id}:{uuid.uuid4().hex}"
    with pg_connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, agent_slug, name, prompt, deliver_to, delivery_channel, "
                    "delivery_profile, delivery_conversation_id, kind, is_active "
                    "FROM agent_automations WHERE id = %s FOR UPDATE",
                    (auto_id,),
                )
                locked = cur.fetchone()
                if not locked or locked["kind"] != "agent":
                    return None
                if trigger_kind == "schedule" and not locked["is_active"]:
                    return None
                snapshot = json.dumps(_run_snapshot(locked), ensure_ascii=False)
                cur.execute(
                    "INSERT INTO agent_automation_runs "
                    "(automation_id, agent_slug, idempotency_key, trigger_kind, scheduled_for, "
                    "automation_snapshot) VALUES (%s, %s, %s, %s, %s, %s::jsonb) "
                    "ON CONFLICT DO NOTHING RETURNING id",
                    (auto_id, locked["agent_slug"], idem, trigger_kind, scheduled_for, snapshot),
                )
                created = cur.fetchone()
                if created is None:
                    return None
                cur.execute(
                    "UPDATE agent_automations SET last_run_at = %s, last_status = 'queued', "
                    "last_error = NULL, updated_at = now() WHERE id = %s",
                    (scheduled_for, auto_id),
                )
                return int(created["id"])


def _claim_due_run() -> dict[str, Any] | None:
    with pg_connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM agent_automation_runs "
                    "WHERE status IN ('queued','brain_retry','delivery_pending','delivery_retry') "
                    "AND available_at <= now() ORDER BY available_at, id "
                    "FOR UPDATE SKIP LOCKED LIMIT 1"
                )
                run = cur.fetchone()
                if not run:
                    return None
                brain = run["status"] in ("queued", "brain_retry")
                claimed = "brain_running" if brain else "delivery_running"
                counter = "brain_attempts" if brain else "delivery_attempts"
                cur.execute(
                    f"UPDATE agent_automation_runs SET status = %s, {counter} = {counter} + 1, "
                    "claimed_by = %s, lease_until = now() + (%s * interval '1 second'), "
                    "started_at = COALESCE(started_at, now()), updated_at = now() WHERE id = %s "
                    "RETURNING *",
                    (claimed, _WORKER_ID, _LEASE_S, run["id"]),
                )
                claimed_run = cur.fetchone()
                cur.execute(
                    "UPDATE agent_automations SET last_status = 'running', updated_at = now() "
                    "WHERE id = %s",
                    (claimed_run["automation_id"],),
                )
                claimed_run["claimed_stage"] = "brain" if brain else "delivery"
                return claimed_run


def active_automation_run_for_agent(agent_slug: str) -> int | None:
    """Bind the static automation connector alias to one currently leased brain run."""
    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM agent_automation_runs WHERE agent_slug = %s "
                "AND status = 'brain_running' AND lease_until > now() ORDER BY id",
                (agent_slug,),
            )
            rows = list(cur.fetchall())
    return int(rows[0]["id"]) if len(rows) == 1 else None


def _set_run_status(run_id: int, status: str, error: str | None = None,
                    delay_s: int = 0) -> None:
    terminal = status in ("done", "silent", "error", "review")
    with pg_connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE agent_automation_runs SET status = %s, last_error = %s, "
                    "available_at = now() + (%s * interval '1 second'), lease_until = NULL, "
                    "claimed_by = NULL, finished_at = CASE WHEN %s THEN now() ELSE finished_at END, "
                    "updated_at = now() WHERE id = %s RETURNING automation_id, result_text",
                    (status, (error or "")[:500] or None, delay_s, terminal, run_id),
                )
                row = cur.fetchone()
                if not row:
                    return
                parent_status = {
                    "done": "ok", "silent": "silent", "error": "error", "review": "review",
                    "brain_retry": "queued", "delivery_retry": "queued",
                }.get(status, "running")
                cur.execute(
                    "UPDATE agent_automations SET last_status = %s, last_result = %s, "
                    "last_error = %s, updated_at = now() WHERE id = %s",
                    (parent_status, (row.get("result_text") or "")[:_RESULT_KEEP],
                     (error or "")[:500] or None, row["automation_id"]),
                )


def _brain_failure(run: dict[str, Any], error: str) -> None:
    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT had_mutating_effect FROM agent_automation_runs WHERE id = %s", (run["id"],))
            state = cur.fetchone() or {}
    if state.get("had_mutating_effect"):
        _set_run_status(run["id"], "review", error + " — возможны уже выполненные действия")
    elif int(run["brain_attempts"]) < _MAX_BRAIN_ATTEMPTS:
        _set_run_status(run["id"], "brain_retry", error, _RETRY_DELAY_S)
    else:
        _set_run_status(run["id"], "error", error)


def _hermes_once(cmd: list, timeout_s: int, tag: str) -> tuple[Any, str | None]:
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_s,
            cwd="/root", env={**os.environ, "HOME": "/root"},
        ), None
    except subprocess.TimeoutExpired:
        logging.warning("agent automation %s: hermes timed out after %ss", tag, timeout_s)
        return None, "timeout"


def _prepare_delivery(run: dict[str, Any], answer: str) -> None:
    snapshot = run["automation_snapshot"]
    channel = str(snapshot.get("delivery_channel") or "bitrix").strip().lower()
    raw = str(snapshot.get("delivery_conversation_id") or snapshot.get("deliver_to") or "").strip()
    if not raw and channel == "bitrix":
        raw = os.getenv("ALBERY_BITRIX_NOTIFY_CHAT", "chat728")
    if channel not in {"bitrix", "telegram"} or not raw:
        raise RuntimeError("некорректный типизированный адрес доставки")
    targets = [t.strip() for t in raw.replace(";", ",").split(",") if t.strip()]
    profile_slug = str(snapshot.get("delivery_profile") or run["agent_slug"]).strip()
    from zoom import extract_export_artifacts
    clean_answer, artifacts, _invalid = extract_export_artifacts(answer)
    artifact_tokens: list[str] = []
    if artifacts:
        import attachments
        for artifact in artifacts:
            token = attachments.store_attachment(
                data=artifact["data"],
                file_name=artifact["display_name"],
                kind="agent_doc",
                extracted_text="",
                agent_slug=str(run["agent_slug"]),
                dialog_id="automation:" + str(run["id"]),
                mime=artifact.get("mime"),
            )
            if not token:
                raise RuntimeError("generated automation artifact could not be persisted")
            artifact_tokens.append(token)
    with pg_connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE agent_automation_runs SET result_text = %s, status = 'delivery_pending', "
                    "brain_finished_at = now(), available_at = now(), lease_until = NULL, "
                    "claimed_by = NULL, last_error = NULL, updated_at = now() WHERE id = %s",
                    (clean_answer, run["id"]),
                )
                for target in targets:
                    # Text and files are distinct durable parts. If the provider accepts the text
                    # and a later file fails, only that file is retried; a Telegram caption limit
                    # can never truncate the actual automation result.
                    cur.execute(
                        "INSERT INTO agent_automation_deliveries "
                        "(run_id, target, channel, profile_slug, part_no, rendered_text) "
                        "VALUES (%s, %s, %s, %s, 0, %s) "
                        "ON CONFLICT (run_id, target, part_no) DO NOTHING",
                        (run["id"], target, channel, profile_slug, clean_answer),
                    )
                    for part_no, token in enumerate(artifact_tokens, start=1):
                        cur.execute(
                            "INSERT INTO agent_automation_deliveries "
                            "(run_id, target, channel, profile_slug, part_no, attachment_token, rendered_text) "
                            "VALUES (%s, %s, %s, %s, %s, %s, '') "
                            "ON CONFLICT (run_id, target, part_no) DO NOTHING",
                            (run["id"], target, channel, profile_slug, part_no, token),
                        )


def _process_brain(run: dict[str, Any]) -> None:
    snapshot = run["automation_snapshot"]
    try:
        from agent_center import _agent_by_slug
        agent = _agent_by_slug(run["agent_slug"])
        if not agent:
            raise RuntimeError("агент не найден")
        if not agent.get("is_active"):
            raise RuntimeError("агент выключен")
        from shared.run_slots import build_default
        slot = build_default().acquire(_SLOT_WAIT_S)
        if slot is None:
            with pg_connect() as conn:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE agent_automation_runs SET status = 'brain_retry', "
                            "brain_attempts = GREATEST(brain_attempts - 1, 0), available_at = now() + "
                            "interval '15 seconds', lease_until = NULL, claimed_by = NULL, "
                            "last_error = 'ожидание общего слота Hermes', updated_at = now() WHERE id = %s",
                            (run["id"],),
                        )
                        cur.execute(
                            "UPDATE agent_automations SET last_status = 'queued', updated_at = now() "
                            "WHERE id = %s",
                            (run["automation_id"],),
                        )
            return
        if slot.is_local_fallback:
            # On a DB outage independent process-local semaphores are not a server-wide limit.
            # Live employee turns retain their historic availability fallback; background work
            # fails closed so it cannot create the unsafe "2 live + automation" combination.
            slot.release()
            with pg_connect() as conn:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE agent_automation_runs SET status = 'brain_retry', "
                            "brain_attempts = GREATEST(brain_attempts - 1, 0), available_at = now() + "
                            "interval '30 seconds', lease_until = NULL, claimed_by = NULL, "
                            "last_error = 'общий серверный лимит недоступен', updated_at = now() WHERE id = %s",
                            (run["id"],),
                        )
                        cur.execute(
                            "UPDATE agent_automations SET last_status = 'queued', updated_at = now() "
                            "WHERE id = %s",
                            (run["automation_id"],),
                        )
            return
        try:
            prompt = _automation_prompt(agent, snapshot)
            extra = os.getenv("B24_EXTRA_TOOLSETS", "web").strip().strip(",")
            connector = f"automation-agent-{agent['slug']}"
            toolsets = f"{connector},{extra}" if extra else connector
            proc, failure = _hermes_once(
                ["hermes", "-z", prompt, "-t", toolsets, "--yolo"],
                _AUTOMATION_TIMEOUT_S,
                f"run={run['id']}/{snapshot['name']}",
            )
        finally:
            slot.release()
        if failure == "timeout":
            raise RuntimeError(f"таймаут {_AUTOMATION_TIMEOUT_S} с")
        from b24bot import _hermes_answer_is_error
        answer = (proc.stdout or "").strip()
        if not answer:
            raise RuntimeError("пустой ответ мозга")
        if proc.returncode != 0 or _hermes_answer_is_error(answer):
            raise RuntimeError("ошибка LLM: " + answer[:200])
        if _is_silent(answer):
            _set_run_status(run["id"], "silent")
        else:
            _prepare_delivery(run, answer)
    except Exception as exc:  # noqa: BLE001
        logging.warning("agent automation run %s brain stage failed: %s", run["id"], exc)
        _brain_failure(run, str(exc)[:500])


def _process_delivery(run: dict[str, Any]) -> None:
    snapshot = run["automation_snapshot"]
    from agent_center import _agent_by_slug
    agent = _agent_by_slug(run["agent_slug"])
    if not agent or not agent.get("is_active"):
        _set_run_status(run["id"], "error", "агент не найден или выключен перед доставкой")
        return
    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM agent_automation_deliveries WHERE run_id = %s "
                "AND status IN ('pending','retry') AND available_at <= now() ORDER BY id",
                (run["id"],),
            )
            deliveries = list(cur.fetchall())
    for delivery in deliveries:
        with pg_connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE agent_automation_deliveries SET status = 'sending', attempts = attempts + 1, "
                        "lease_until = now() + (%s * interval '1 second'), updated_at = now() WHERE id = %s",
                        (_LEASE_S, delivery["id"]),
                    )
        channel = str(delivery.get("channel") or "bitrix").lower()
        ambiguous = False
        known_retryable = channel == "bitrix"
        try:
            if channel == "telegram":
                import tg_multi
                token = agent.get("telegram_bot_token")
                if not token or str(delivery.get("profile_slug") or run["agent_slug"]) != str(agent["slug"]):
                    raise tg_multi.TelegramAPIError(
                        "sendMessage", "Telegram identity is not bound to this profile", status_code=400
                    )
                with pg_connect() as access_conn:
                    with access_conn.cursor() as access_cur:
                        access_cur.execute(
                            "SELECT 1 FROM telegram_bot_access WHERE bot = %s AND is_active "
                            "AND tg_user_id IS NOT NULL AND tg_user_id::text = %s LIMIT 1",
                            (agent["slug"], str(delivery["target"])),
                        )
                        if not access_cur.fetchone():
                            raise tg_multi.TelegramAPIError(
                                "sendMessage", "Telegram recipient access is absent or revoked",
                                status_code=403,
                            )
                part_text = (delivery.get("rendered_text") if delivery.get("rendered_text") is not None
                             else run["result_text"] or "")
                message = "⏰ " + snapshot["name"] + "\n" + str(part_text)
                if delivery.get("attachment_token"):
                    import attachments
                    blob = attachments.attachment_bytes(str(delivery["attachment_token"]))
                    if not blob:
                        raise tg_multi.TelegramAPIError(
                            "sendDocument", "stored artifact is unavailable", status_code=400
                        )
                    data, file_name = blob
                    tg_multi.api(
                        token, "sendDocument", chat_id=delivery["target"],
                        document=(file_name, data), caption=("📎 " + file_name)[:1024],
                    )
                else:
                    tg_multi.api(token, "sendMessage", chat_id=delivery["target"], text=message[:4000])
                ok, error = True, None
            else:
                part_text = (delivery.get("rendered_text") if delivery.get("rendered_text") is not None
                             else run["result_text"] or "")
                message = "[b]⏰ " + snapshot["name"] + "[/b]\n" + _bb_sanitize(part_text)
                if delivery.get("attachment_token"):
                    import attachments
                    from b24bot import _b24_app_access_token, _b24_app_file_reply
                    blob = attachments.attachment_bytes(str(delivery["attachment_token"]))
                    if not blob:
                        ok, error = False, "stored artifact is unavailable"
                    else:
                        data, file_name = blob
                        endpoint, access_token = _b24_app_access_token()
                        message_id, error_kind = _b24_app_file_reply(
                            endpoint, access_token, agent.get("bitrix_bot_id"), delivery["target"],
                            "📎 " + file_name, [{"data": data, "display_name": file_name,
                                       "filename": file_name, "byte_size": len(data)}],
                        )
                        ok = bool(message_id and error_kind is None)
                        error = error_kind
                        ambiguous = error_kind == "ambiguous"
                        known_retryable = error_kind == "known"
                else:
                    from b24bot import _albery_bitrix_notify
                    ok, error = _albery_bitrix_notify(
                        message, dialog_id=delivery["target"], bot_id=agent.get("bitrix_bot_id"),
                        retry_transient=False,
                    )
        except Exception as exc:
            try:
                import tg_multi
                if isinstance(exc, tg_multi.TelegramDeliveryAmbiguous):
                    ambiguous = True
                elif isinstance(exc, tg_multi.TelegramAPIError):
                    known_retryable = exc.retryable
                else:
                    ambiguous = True
            except Exception:  # noqa: BLE001
                ambiguous = True
            ok, error = False, str(exc)[:500]
        if channel == "bitrix" and not ok and any(
            marker in str(error or "").lower()
            for marker in ("timed out", "timeout", "connection", "remote end closed")
        ):
            ambiguous = True
        if ambiguous:
            with pg_connect() as conn:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE agent_automation_deliveries SET status = 'review', last_error = %s, "
                            "lease_until = NULL, updated_at = now() WHERE id = %s",
                            (("неизвестен итог доставки: " + str(error))[:500], delivery["id"]),
                        )
            continue
        attempts = int(delivery["attempts"]) + 1
        status = (
            "delivered" if ok
            else ("retry" if known_retryable and attempts < _MAX_DELIVERY_ATTEMPTS else "error")
        )
        with pg_connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE agent_automation_deliveries SET status = %s, last_error = %s, "
                        "available_at = now() + (%s * interval '1 second'), lease_until = NULL, "
                        "delivered_at = CASE WHEN %s THEN now() ELSE delivered_at END, updated_at = now() "
                        "WHERE id = %s",
                        (status, (error or "")[:500] or None, _RETRY_DELAY_S, bool(ok), delivery["id"]),
                    )
    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, count(*) AS n FROM agent_automation_deliveries "
                "WHERE run_id = %s GROUP BY status",
                (run["id"],),
            )
            counts = {r["status"]: int(r["n"]) for r in cur.fetchall()}
    if counts.get("review") or counts.get("sending"):
        _set_run_status(run["id"], "review", "итог одной из доставок требует ручной проверки")
    elif counts.get("pending") or counts.get("retry"):
        _set_run_status(run["id"], "delivery_retry", "повторяется только доставка", _RETRY_DELAY_S)
    elif counts.get("delivered"):
        _set_run_status(run["id"], "done", "часть получателей недоступна" if counts.get("error") else None)
    else:
        _set_run_status(run["id"], "error", "сообщение не доставлено ни одному получателю")


def _worker_loop() -> None:
    while True:
        try:
            run = _claim_due_run()
            if run is None:
                time.sleep(_QUEUE_POLL_S)
            elif run["claimed_stage"] == "brain":
                _process_brain(run)
            else:
                _process_delivery(run)
        except Exception:  # noqa: BLE001
            logging.exception("agent automation durable worker iteration failed")
            time.sleep(_QUEUE_POLL_S)


def _claim(auto_id: int, minute_start) -> bool:
    """Persist one scheduled minute; the idempotency key blocks duplicates across restarts."""
    row = _row_by_id(auto_id)
    if not row:
        return False
    try:
        return _enqueue_run(row, "schedule", minute_start) is not None
    except Exception:  # noqa: BLE001
        logging.exception("agent automation %s: durable schedule enqueue failed", auto_id)
        return False


def _scheduler_tick(minute_start) -> None:
    _recover_durable_runs()
    rows = _load_rows("WHERE kind = 'agent' AND is_active")
    for row in rows:
        try:
            due = cron_schedule.matches(row["schedule"], minute_start)
        except ValueError:
            continue
        if due:
            try:
                _enqueue_run(row, "schedule", minute_start)
            except Exception:  # noqa: BLE001
                logging.exception("agent automation %s: scheduler enqueue failed", row["id"])


def _recover_durable_runs() -> int:
    """Recover expired leases without replaying an outcome that may already be external."""
    recovered = 0
    parent_states: dict[int, str] = {}
    try:
        with pg_connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    # A brain with no recorded mutation is safe to recompute. Any recorded write
                    # makes the outcome ambiguous and therefore manual-review only.
                    cur.execute(
                        "UPDATE agent_automation_runs SET status = CASE "
                        "WHEN had_mutating_effect THEN 'review' ELSE 'brain_retry' END, "
                        "available_at = now(), claimed_by = NULL, lease_until = NULL, "
                        "last_error = CASE WHEN had_mutating_effect "
                        "THEN 'запуск прерван после потенциального внешнего действия; нужна проверка' "
                        "ELSE 'безопасное восстановление после прерывания до внешних действий' END, "
                        "finished_at = CASE WHEN had_mutating_effect THEN now() ELSE finished_at END, "
                        "updated_at = now() WHERE status = 'brain_running' AND lease_until < now() "
                        "RETURNING automation_id, status"
                    )
                    brain_rows = list(cur.fetchall())
                    recovered += len(brain_rows)
                    parent_states.update({int(r["automation_id"]): r["status"] for r in brain_rows})
                    # Sending is intentionally not retried: the process may have died after Bitrix
                    # accepted the message but before Albery stored the acknowledgement.
                    cur.execute(
                        "UPDATE agent_automation_deliveries SET status = 'review', lease_until = NULL, "
                        "last_error = 'неизвестен итог доставки после прерывания процесса', "
                        "updated_at = now() WHERE status = 'sending' AND lease_until < now() RETURNING run_id"
                    )
                    ambiguous_delivery_runs = [r["run_id"] for r in cur.fetchall()]
                    if ambiguous_delivery_runs:
                        cur.execute(
                            "UPDATE agent_automation_runs SET status = 'review', claimed_by = NULL, "
                            "lease_until = NULL, finished_at = now(), "
                            "last_error = 'итог доставки требует ручной проверки', updated_at = now() "
                            "WHERE id = ANY(%s) RETURNING automation_id, status",
                            (ambiguous_delivery_runs,),
                        )
                        parent_states.update({
                            int(r["automation_id"]): r["status"] for r in cur.fetchall()
                        })
                    cur.execute(
                        "UPDATE agent_automation_runs SET status = 'delivery_retry', available_at = now(), "
                        "claimed_by = NULL, lease_until = NULL, "
                        "last_error = 'доставка восстановлена после прерывания до отправки', "
                        "updated_at = now() WHERE status = 'delivery_running' AND lease_until < now() "
                        "AND NOT EXISTS (SELECT 1 FROM agent_automation_deliveries d "
                        "WHERE d.run_id = agent_automation_runs.id AND d.status = 'review') "
                        "RETURNING automation_id, status"
                    )
                    delivery_rows = list(cur.fetchall())
                    recovered += len(delivery_rows) + len(ambiguous_delivery_runs)
                    parent_states.update({
                        int(r["automation_id"]): r["status"] for r in delivery_rows
                    })
                    for automation_id, run_status in parent_states.items():
                        cur.execute(
                            "UPDATE agent_automations SET last_status = %s, updated_at = now() WHERE id = %s",
                            ("review" if run_status == "review" else "queued", automation_id),
                        )
    except Exception:  # noqa: BLE001
        logging.exception("agent automations: durable lease recovery failed")
    return recovered


def _recover_interrupted_runs() -> int:
    """Пометить запуски, оборванные перезапуском сервиса, как прерванные.

    Воркеры живут внутри процесса: рестарт (деплой, авария) убивает выполняющуюся
    автоматизацию вместе с её hermes-подпроцессами, и `_finish_run` уже не отработает —
    запись остаётся в «выполняется» навсегда. Так 27.07.2026 повис «Ежедневный отчёт
    собственнику»: захватил минуту 18:00, а в 18:00:40 сервис перезапустили при выкате.
    Панель показывала «выполняется» четвёртый час, хотя выполнять было уже некому,
    и по этой же причине правило «рестартовать только когда нет running-автоматизаций»
    блокировалось мёртвой записью.

    Чинятся только зависшие (старше окна таймаута с запасом) — живой долгий запуск
    из соседнего процесса не трогаем. Сбой самой починки не должен мешать планировщику:
    он логируется и работа продолжается.
    """

    cutoff = msk_now() - timedelta(seconds=_RUNNING_STALE_S)
    try:
        with pg_connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE agent_automations SET last_status = 'interrupted', "
                        "last_error = COALESCE(NULLIF(last_error, ''), %s), updated_at = now() "
                        "WHERE last_status = 'running' AND last_run_at < %s "
                        "RETURNING id, name",
                        ("запуск прерван перезапуском сервиса — результат не был доставлен",
                         cutoff),
                    )
                    rows = list(cur.fetchall())
    except Exception:  # noqa: BLE001 - починка состояния не важнее самой работы планировщика
        logging.exception("agent automations: recovery of interrupted runs failed")
        return 0
    for row in rows:
        logging.warning("agent automation %s (%s) was interrupted by a restart — marked as such",
                        row["id"], row["name"])
    return len(rows)


def _scheduler_loop() -> None:
    _recover_interrupted_runs()
    _recover_durable_runs()
    time.sleep(120)  # let the app finish booting
    last_minute = None
    while True:
        try:
            minute = msk_now().replace(second=0, microsecond=0)
            if minute != last_minute:
                last_minute = minute
                _scheduler_tick(minute)
        except Exception:  # noqa: BLE001
            logging.exception("agent automations: scheduler tick failed")
        time.sleep(15)


# Автоматизации агентов — фоновое расписание: крутится РОВНО в роли бота, иначе веб-
# и MCP-службы завели бы вторую и третью копию (двойные уведомления людям).
if os.getenv("AGENT_AUTOMATIONS", "1").strip() != "0" and background_jobs_enabled():
    threading.Thread(target=_scheduler_loop, daemon=True, name="agent-automations-scheduler").start()
    for _n in range(_AUTOMATION_WORKERS):
        threading.Thread(target=_worker_loop, daemon=True, name=f"agent-automations-worker-{_n}").start()
    # Pull hermes-cron runs/state into the system mirror rows (own gate: SYSTEM_AUTOMATION_SYNC=0).
    import system_automations as _system_automations
    _system_automations.start_sync_thread()


# --- Self-tools on the per-agent MCP connector (merged into agent_center._SELF_TOOL_SPECS) ---
# Same mechanic as self-learning: handled right in the connector endpoint with the slug
# from the URL, so an agent can only ever see/schedule/delete ITS OWN automations.

AUTOMATION_SELF_TOOL_SPECS: dict[str, dict[str, Any]] = {
    "schedule_my_automation": {
        "description": (
            "АВТОМАТИЗАЦИИ: поставь СЕБЕ регулярное ДЕЙСТВИЕ по расписанию (cron, время МСК) — отчёт, "
            "сводку, мониторинг. Каждый запуск — твой полноценный ход: ты выполнишь task своими "
            "инструментами, и результат уйдёт в явно указанный исходный канал. ⚠️ НЕ для регулярных ЗАДАЧ Bitrix: "
            "если просят «создавай задачу каждый день/неделю» — используй create_recurring_task (он "
            "создаёт задачи без хода агента и тоже виден во вкладке «Автоматизации»). ПЕРЕД созданием "
            "честно проверь, что твоих ИНСТРУМЕНТОВ хватает для задачи; если нет — НЕ создавай "
            "автоматизацию, а скажи пользователю, чего именно не хватает. schedule — 5 полей cron: "
            "«0 9 * * 1-5» = будни в 9:00, «30 18 * * 5» = пт в 18:30; чаще раза в час нельзя. "
            "delivery_channel и delivery_conversation_id задают канал и текущий диалог; "
            "никогда не угадывай канал по числовому id."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Короткое название (до 80 символов)."},
                "schedule": {"type": "string", "description": "Cron из 5 полей, время МСК."},
                "task": {"type": "string", "description": "Что делать при каждом запуске — подробная постановка."},
                "delivery_channel": {"type": "string", "enum": ["bitrix", "telegram"],
                                     "description": "Канал исходного диалога."},
                "delivery_conversation_id": {"type": "string",
                                             "description": "Идентификатор текущего диалога в этом канале."},
                "deliver_to": {"type": "string", "description": "Устаревший Bitrix dialog_id; только для совместимости."},
                "requested_by": {"type": "string", "description": "Имя сотрудника, который попросил автоматизацию (собеседник текущего диалога) — видно владельцу."},
                "description": {"type": "string", "description": "Необязательное описание для владельца."},
            },
            "required": ["name", "schedule", "task", "delivery_channel",
                         "delivery_conversation_id", "requested_by"],
        },
    },
    "list_my_automations": {
        "description": "АВТОМАТИЗАЦИИ: список твоих регулярных задач (расписание, статус последнего запуска).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "delete_my_automation": {
        "description": (
            "АВТОМАТИЗАЦИИ: удали СВОЮ автоматизацию по названию. Удалять можно только те, что ты сам "
            "поставил; автоматизации владельца и системные — только владелец в приложении."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Название автоматизации."}},
            "required": ["name"],
        },
    },
}


def _requester_name(requested_by: str, deliver_to: str) -> str:
    """Who asked for this automation — the agent's own words, else the portal directory
    name behind deliver_to (a private dialog_id IS the user's id)."""
    requested_by = (requested_by or "").strip()[:80]
    if requested_by:
        return requested_by
    target = (deliver_to or "").strip()
    if target.isdigit():
        try:
            from agent_center import _user_names
            info = _user_names().get(int(target))
            if info and info.get("name"):
                return str(info["name"])
        except Exception:  # noqa: BLE001
            logging.exception("automation requester name lookup failed")
    return ""


def automation_self_tool_call(agent: dict[str, Any], name: str, args: dict[str, Any]) -> dict[str, Any]:
    slug = agent["slug"]
    if name == "list_my_automations":
        rows = _load_rows("WHERE agent_slug = %s", (slug,))
        return {
            "automations": [
                {"name": r["name"], "schedule": r["schedule"],
                 "schedule_label": _schedule_view(r["schedule"], bool(r["is_active"]))[1],
                 "task": r["prompt"] or "", "deliver_to": r["deliver_to"] or "",
                 "delivery_channel": r.get("delivery_channel") or "bitrix",
                 "delivery_conversation_id": r.get("delivery_conversation_id") or r["deliver_to"] or "",
                 "active": bool(r["is_active"]),
                 "managed_by": ("Hermes (системная)" if r["kind"] == "system" else r["creator_label"] or r["created_by"]),
                 "last_status": r["last_status"] or "", "last_run": _when(r["last_run_at"])}
                for r in rows
            ],
            "count": len(rows),
        }
    auto_name = str(args.get("name") or "").strip()[:_NAME_MAX]
    if not auto_name:
        raise ValueError("Укажите name.")
    if name == "schedule_my_automation":
        schedule = str(args.get("schedule") or "").strip()
        task = str(args.get("task") or "").strip()
        problem = _validate(auto_name, schedule, task, _SELF_MAX_FIRES_PER_DAY)
        if problem:
            raise ValueError(problem)
        own = _load_rows("WHERE agent_slug = %s AND created_by = 'self'", (slug,))
        if len(own) >= _SELF_AUTOMATIONS_MAX and auto_name not in {r["name"] for r in own}:
            raise ValueError(f"Достигнут потолок {_SELF_AUTOMATIONS_MAX} автоматизаций у этого агента "
                             "(защита от бесконтрольного роста). Удали неактуальную "
                             "(delete_my_automation) или объедини несколько в одну.")
        channel = str(args.get("delivery_channel") or ("bitrix" if args.get("deliver_to") else "")).strip().lower()
        conversation_id = str(
            args.get("delivery_conversation_id") or args.get("deliver_to") or ""
        ).strip()
        if channel not in {"bitrix", "telegram"}:
            raise ValueError("Укажите delivery_channel: bitrix или telegram.")
        if not conversation_id:
            raise ValueError("Укажите delivery_conversation_id текущего диалога.")
        label = f"агент «{agent.get('name') or slug}» (сам)"
        requester = _requester_name(str(args.get("requested_by") or ""), conversation_id)
        if requester:
            label += f" · по просьбе: {requester}"
        with pg_connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO agent_automations (agent_slug, name, description, schedule, prompt, "
                        "deliver_to, delivery_channel, delivery_profile, delivery_conversation_id, "
                        "kind, created_by, creator_label) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'agent', 'self', %s) "
                        "ON CONFLICT (agent_slug, name) DO UPDATE SET schedule = EXCLUDED.schedule, "
                        "prompt = EXCLUDED.prompt, deliver_to = EXCLUDED.deliver_to, "
                        "delivery_channel = EXCLUDED.delivery_channel, "
                        "delivery_profile = EXCLUDED.delivery_profile, "
                        "delivery_conversation_id = EXCLUDED.delivery_conversation_id, "
                        "description = EXCLUDED.description, is_active = TRUE, updated_at = now() "
                        "WHERE agent_automations.created_by = 'self' RETURNING id",
                        (slug, auto_name, str(args.get("description") or "").strip(), schedule, task,
                         conversation_id, channel, slug, conversation_id, label),
                    )
                    saved = cur.fetchone()
        if not saved:
            raise ValueError("Такое название уже занято автоматизацией владельца — выбери другое.")
        nxt = cron_schedule.next_run(schedule, msk_now())
        return {"ok": True, "scheduled": auto_name,
                "schedule_label": cron_schedule.describe(schedule),
                "next_run": _when(nxt),
                "note": "Автоматизация видна владельцу в Центре Агента (Агенты → Автоматизации)."}
    if name == "delete_my_automation":
        rows = _load_rows("WHERE agent_slug = %s AND name = %s", (slug, auto_name))
        if not rows:
            raise ValueError("Такой автоматизации нет (list_my_automations покажет точные названия).")
        row = rows[0]
        if row["kind"] == "system" or row["created_by"] != "self":
            raise ValueError("Эту автоматизацию поставил владелец/система — удалить может только владелец в приложении.")
        with pg_connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM agent_automations WHERE id = %s", (row["id"],))
        return {"ok": True, "deleted": auto_name}
    raise ValueError(f"Неизвестный инструмент: {name}")
