"""Per-agent scheduled automations (Центр Агента → Агенты → «Автоматизации»).

Each agent (the universal/main one and every subagent) keeps its own list of cron
automations in `agent_automations`. Two kinds:
  - kind='agent'  — executed HERE by a scheduler thread: one `hermes -z` turn on the
    agent's own connector (`-t agent-<slug>`), so the run is bounded by exactly the
    tools/instructions the owner enabled for that agent; the result is posted into a
    Bitrix dialog as that agent's bot. Created by the owner in the UI or by the agent
    itself from chat (schedule_my_automation self-tool).
  - kind='system' — read-only mirror rows for the legacy Hermes cron jobs that live on
    the box (`hermes cron list`: zoom-to-tasks, owner-daily, owner-weekly, leader-digest);
    shown in the UI, never executed or edited by the app.

Registers routes on the shared Flask `app` at import time (same pattern as b24bot /
agent_center); agent_center imports this module at its bottom — app.py stays frozen.
Imports from agent_center/b24bot are lazy (inside functions) per the project's
circular-import rule.
"""
from __future__ import annotations

import logging
import os
import queue
import re
import subprocess
import threading
import time

from typing import Any

from flask import jsonify, request

from app import MSK_TZ, app, msk_now, pg_connect
import cron_schedule

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

# Automations run in their OWN lane, fully independent of live employee turns: a
# dedicated worker pool draining a queue, with its own hermes subprocesses that never
# touch b24bot's _HERMES_RUN_SLOTS. Employees can't starve automations and automations
# can't eat employee slots — separate brains, separate roads. Every claimed fire is
# eventually executed (queued, never dropped); transient failures get one delayed retry.
_AUTOMATION_WORKERS = max(1, int(os.getenv("AGENT_AUTOMATION_CONCURRENCY", "1")))
_RETRY_DELAY_S = int(os.getenv("AGENT_AUTOMATION_RETRY_DELAY_S", "120"))
_work_q: "queue.Queue[tuple[dict[str, Any], int]]" = queue.Queue()


# --- Storage ---------------------------------------------------------------------------------

_COLS = ("id, agent_slug, name, description, schedule, prompt, deliver_to, kind, created_by, "
         "creator_label, is_active, last_run_at, last_status, last_result, last_error, created_at")


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


def _finish_run(auto_id: int, status: str, result: str, error: str | None) -> None:
    try:
        with pg_connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE agent_automations SET last_run_at = now(), last_status = %s, "
                        "last_result = %s, last_error = %s, updated_at = now() WHERE id = %s",
                        (status, (result or "")[:_RESULT_KEEP], error, auto_id),
                    )
    except Exception:  # noqa: BLE001
        logging.exception("agent automation %s: run status write failed", auto_id)


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
    return {
        "id": r["id"],
        "agent_slug": r["agent_slug"],
        "name": r["name"],
        "description": r["description"] or "",
        "schedule": r["schedule"],
        "schedule_label": schedule_label,
        "prompt": r["prompt"] or "",
        "deliver_to": r["deliver_to"] or "",
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
    try:
        with pg_connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO agent_automations (agent_slug, name, description, schedule, "
                        "prompt, deliver_to, kind, created_by, creator_label) "
                        "VALUES (%s, %s, %s, %s, %s, %s, 'agent', 'owner', 'владелец (панель)') "
                        "ON CONFLICT (agent_slug, name) DO NOTHING RETURNING id",
                        (slug, name, str(body.get("description") or "").strip(), schedule,
                         prompt, str(body.get("deliver_to") or "").strip()),
                    )
                    created = cur.fetchone()
        if not created:
            return jsonify({"error": "Автоматизация с таким названием уже есть у этого агента."}), 409
        return jsonify({"ok": True, "id": created["id"]})
    except Exception:  # noqa: BLE001
        logging.exception("agent automation create failed: %s", slug)
        return jsonify({"error": "Не удалось создать автоматизацию."}), 500


@app.patch("/api/agent-center/automations/<int:auto_id>")
def agent_automation_update(auto_id: int):
    row = _row_by_id(auto_id)
    if not row:
        return jsonify({"error": "Автоматизация не найдена."}), 404
    if row["kind"] == "system":
        return jsonify({"error": "Системная автоматизация управляется Hermes на сервере — здесь только витрина."}), 403
    body = request.get_json(silent=True) or {}
    name = str(body.get("name") if body.get("name") is not None else row["name"]).strip()
    schedule = str(body.get("schedule") if body.get("schedule") is not None else row["schedule"]).strip()
    prompt = str(body.get("prompt") if body.get("prompt") is not None else row["prompt"]).strip()
    problem = _validate(name, schedule, prompt, _OWNER_MAX_FIRES_PER_DAY)
    if problem:
        return jsonify({"error": problem}), 400
    description = str(body.get("description") if body.get("description") is not None else row["description"]).strip()
    deliver_to = str(body.get("deliver_to") if body.get("deliver_to") is not None else row["deliver_to"]).strip()
    is_active = bool(body.get("is_active")) if body.get("is_active") is not None else bool(row["is_active"])
    try:
        with pg_connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE agent_automations SET name = %s, description = %s, schedule = %s, "
                        "prompt = %s, deliver_to = %s, is_active = %s, updated_at = now() WHERE id = %s",
                        (name, description, schedule, prompt, deliver_to, is_active, auto_id),
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
        return jsonify({"error": "Системная автоматизация управляется Hermes на сервере."}), 403
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
        return jsonify({"error": "Системную автоматизацию запускает Hermes по своему расписанию."}), 403
    if row["last_status"] == "running" and not _running_is_stale(row):
        return jsonify({"error": "Уже выполняется."}), 409
    try:
        with pg_connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("UPDATE agent_automations SET last_status = 'running', "
                                "last_run_at = now(), updated_at = now() WHERE id = %s", (auto_id,))
    except Exception:  # noqa: BLE001
        logging.exception("agent automation %s: manual run mark failed", auto_id)
    _work_q.put((row, 1))
    return jsonify({"ok": True, "started": True})


# --- Execution -------------------------------------------------------------------------------

def _automation_prompt(agent: dict[str, Any], row: dict[str, Any]) -> str:
    role = (agent.get("role_prompt") or "").strip()
    head = (
        "[Служебный запуск по расписанию — автоматизация «" + row["name"] + "» агента «"
        + str(agent.get("name") or agent["slug"]) + "». Это НЕ сообщение пользователя: молча выполни "
        "задачу автоматизации и верни ГОТОВЫЙ текст, который будет отправлен сообщением в Битрикс "
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


def _hermes_oneshot(cmd: list, timeout_s: int, tag: str) -> tuple[Any, str | None]:
    """Run the hermes CLI in the AUTOMATION lane: two quick attempts, no shared
    semaphores with employee turns (that's the whole point — a parallel brain).
    Returns (proc, None) or (None, 'timeout'); like b24bot, an LLM error sentinel
    or empty stdout triggers the in-place second attempt."""
    from b24bot import _hermes_answer_is_error
    proc = None
    for attempt in (1, 2):
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout_s,
                cwd="/root", env={**os.environ, "HOME": "/root"},
            )
        except subprocess.TimeoutExpired:
            logging.warning("agent automation %s: hermes timed out after %ss (attempt %s/2)",
                            tag, timeout_s, attempt)
            return None, "timeout"
        if (proc.returncode == 0 and (proc.stdout or "").strip()
                and not _hermes_answer_is_error(proc.stdout.strip())):
            return proc, None
        logging.error("agent automation %s: hermes run failed (attempt %s/2): rc=%s err=%s answer=%s",
                      tag, attempt, proc.returncode, (proc.stderr or "")[:200],
                      (proc.stdout or "")[:120])
    return proc, None


def _run_automation(row: dict[str, Any], attempt: int = 1) -> None:
    """One automation run: agent turn on its own connector → deliver to Bitrix.
    A transient failure (timeout / LLM hiccup / delivery error) gets ONE delayed
    re-run so a momentary glitch never costs the owner a scheduled report."""
    status, result_text, error = "ok", "", None
    try:
        from agent_center import _agent_by_slug
        agent = _agent_by_slug(row["agent_slug"])
        if not agent:
            raise RuntimeError("агент не найден")
        if not agent.get("is_active"):
            raise RuntimeError("агент выключен")
        prompt = _automation_prompt(agent, row)
        from b24bot import _hermes_answer_is_error
        # Same default as live b24bot turns: the built-in `web` toolset rides along so
        # automations can reach the internet too (terminal/file/exec stay off).
        extra = os.getenv("B24_EXTRA_TOOLSETS", "web").strip().strip(",")
        toolsets = f"agent-{agent['slug']},{extra}" if extra else f"agent-{agent['slug']}"
        cmd = ["hermes", "-z", prompt, "-t", toolsets, "--yolo"]
        proc, run_fail = _hermes_oneshot(cmd, _AUTOMATION_TIMEOUT_S, f"{row['id']}/{row['name']}")
        if run_fail == "timeout":
            raise RuntimeError(f"таймаут {_AUTOMATION_TIMEOUT_S} с")
        answer = (proc.stdout or "").strip()
        if not answer:
            raise RuntimeError("пустой ответ мозга")
        if _hermes_answer_is_error(answer):
            raise RuntimeError("ошибка LLM: " + answer[:200])
        if _is_silent(answer):
            status = "silent"
        else:
            result_text = answer  # kept even on delivery failure so the owner can read it in the UI
            delivered, derr = _deliver(agent, row, answer)
            if not delivered:
                raise RuntimeError(f"доставка в Битрикс не удалась: {derr}")
    except Exception as exc:  # noqa: BLE001
        status, error = "error", str(exc)[:500]
        logging.warning("agent automation %s (%s) attempt %s failed: %s",
                        row["id"], row["name"], attempt, error)
        retriable = "агент не найден" not in error and "агент выключен" not in error
        if attempt == 1 and retriable:
            error += f" — повторю через {_RETRY_DELAY_S} с"
            threading.Timer(_RETRY_DELAY_S, lambda: _work_q.put((row, 2))).start()
    _finish_run(row["id"], status, result_text, error)


def _worker_loop() -> None:
    while True:
        row, attempt = _work_q.get()
        try:
            _run_automation(row, attempt)
        except Exception:  # noqa: BLE001
            logging.exception("agent automation worker crashed on row %s", row.get("id"))
        finally:
            _work_q.task_done()


# --- Scheduler thread (same pattern as the agent_center health watchdog) ---------------------

def _claim(auto_id: int, minute_start) -> bool:
    """Atomically claim this minute's fire — survives restarts, blocks double-runs."""
    try:
        with pg_connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE agent_automations SET last_run_at = %s, last_status = 'running', "
                        "updated_at = now() WHERE id = %s "
                        "AND (last_run_at IS NULL OR last_run_at < %s) RETURNING id",
                        (minute_start, auto_id, minute_start),
                    )
                    return cur.fetchone() is not None
    except Exception:  # noqa: BLE001
        logging.exception("agent automation %s: claim failed", auto_id)
        return False


def _scheduler_tick(minute_start) -> None:
    rows = _load_rows("WHERE kind = 'agent' AND is_active")
    for row in rows:
        try:
            due = cron_schedule.matches(row["schedule"], minute_start)
        except ValueError:
            continue
        if due and _claim(row["id"], minute_start):
            # Queued, never dropped: the worker lane executes every claimed fire even
            # if several automations land on the same minute.
            _work_q.put((row, 1))


def _scheduler_loop() -> None:
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


if os.getenv("AGENT_AUTOMATIONS", "1").strip() != "0":
    threading.Thread(target=_scheduler_loop, daemon=True, name="agent-automations-scheduler").start()
    for _n in range(_AUTOMATION_WORKERS):
        threading.Thread(target=_worker_loop, daemon=True, name=f"agent-automations-worker-{_n}").start()


# --- Self-tools on the per-agent MCP connector (merged into agent_center._SELF_TOOL_SPECS) ---
# Same mechanic as self-learning: handled right in the connector endpoint with the slug
# from the URL, so an agent can only ever see/schedule/delete ITS OWN automations.

AUTOMATION_SELF_TOOL_SPECS: dict[str, dict[str, Any]] = {
    "schedule_my_automation": {
        "description": (
            "АВТОМАТИЗАЦИИ: поставь СЕБЕ регулярное ДЕЙСТВИЕ по расписанию (cron, время МСК) — отчёт, "
            "сводку, мониторинг. Каждый запуск — твой полноценный ход: ты выполнишь task своими "
            "инструментами, и результат уйдёт сообщением в Битрикс. ⚠️ НЕ для регулярных ЗАДАЧ Bitrix: "
            "если просят «создавай задачу каждый день/неделю» — используй create_recurring_task (он "
            "создаёт задачи без хода агента и тоже виден во вкладке «Автоматизации»). ПЕРЕД созданием "
            "честно проверь, что твоих ИНСТРУМЕНТОВ хватает для задачи; если нет — НЕ создавай "
            "автоматизацию, а скажи пользователю, чего именно не хватает. schedule — 5 полей cron: "
            "«0 9 * * 1-5» = будни в 9:00, «30 18 * * 5» = пт в 18:30; чаще раза в час нельзя. "
            "deliver_to — dialog_id, куда слать результат (обычно текущий диалог)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Короткое название (до 80 символов)."},
                "schedule": {"type": "string", "description": "Cron из 5 полей, время МСК."},
                "task": {"type": "string", "description": "Что делать при каждом запуске — подробная постановка."},
                "deliver_to": {"type": "string", "description": "Bitrix dialog_id получателя результата (текущий диалог)."},
                "requested_by": {"type": "string", "description": "Имя сотрудника, который попросил автоматизацию (собеседник текущего диалога) — видно владельцу."},
                "description": {"type": "string", "description": "Необязательное описание для владельца."},
            },
            "required": ["name", "schedule", "task", "deliver_to", "requested_by"],
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
        label = f"агент «{agent.get('name') or slug}» (сам)"
        requester = _requester_name(str(args.get("requested_by") or ""), str(args.get("deliver_to") or ""))
        if requester:
            label += f" · по просьбе: {requester}"
        with pg_connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO agent_automations (agent_slug, name, description, schedule, prompt, "
                        "deliver_to, kind, created_by, creator_label) "
                        "VALUES (%s, %s, %s, %s, %s, %s, 'agent', 'self', %s) "
                        "ON CONFLICT (agent_slug, name) DO UPDATE SET schedule = EXCLUDED.schedule, "
                        "prompt = EXCLUDED.prompt, deliver_to = EXCLUDED.deliver_to, "
                        "description = EXCLUDED.description, is_active = TRUE, updated_at = now() "
                        "WHERE agent_automations.created_by = 'self' RETURNING id",
                        (slug, auto_name, str(args.get("description") or "").strip(), schedule, task,
                         str(args.get("deliver_to") or "").strip(), label),
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
