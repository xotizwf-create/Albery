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
_SELF_AUTOMATIONS_MAX = int(os.getenv("AGENT_SELF_AUTOMATIONS_MAX", "10"))
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


def _automation_json(r: dict[str, Any]) -> dict[str, Any]:
    nxt = cron_schedule.next_run(r["schedule"], msk_now()) if r["is_active"] else None
    status = r["last_status"] or ""
    if _running_is_stale(r):
        status = "interrupted"
    return {
        "id": r["id"],
        "agent_slug": r["agent_slug"],
        "name": r["name"],
        "description": r["description"] or "",
        "schedule": r["schedule"],
        "schedule_label": cron_schedule.describe(r["schedule"]),
        "prompt": r["prompt"] or "",
        "deliver_to": r["deliver_to"] or "",
        "kind": r["kind"],
        "created_by": r["created_by"],
        "creator_label": r["creator_label"] or "",
        "is_active": bool(r["is_active"]),
        "next_run": _when(nxt),
        "last_run": _when(r["last_run_at"]),
        "last_status": status,
        "last_result": r["last_result"] or "",
        "last_error": r["last_error"] or "",
    }


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
        rows = _load_rows("WHERE agent_slug = %s", (slug,))
        return jsonify({"automations": [_automation_json(r) for r in rows]})
    except Exception:  # noqa: BLE001
        logging.exception("agent automations list failed: %s", slug)
        return jsonify({"error": "Не удалось загрузить автоматизации."}), 500


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


def _deliver(agent: dict[str, Any], row: dict[str, Any], text: str) -> tuple[bool, str | None]:
    from b24bot import _albery_bitrix_notify
    target = (row["deliver_to"] or "").strip() or os.getenv("ALBERY_BITRIX_NOTIFY_CHAT", "chat728")
    return _albery_bitrix_notify("[b]⏰ " + row["name"] + "[/b]\n" + text,
                                 dialog_id=target, bot_id=agent.get("bitrix_bot_id"))


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
        cmd = ["hermes", "-z", prompt, "-t", f"agent-{agent['slug']}", "--yolo"]
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
            "АВТОМАТИЗАЦИИ: поставь СЕБЕ регулярную задачу по расписанию (cron, время МСК). "
            "Каждый запуск — твой полноценный ход: ты выполнишь task своими инструментами, и результат "
            "уйдёт сообщением в Битрикс. ПЕРЕД созданием честно проверь, что твоих ИНСТРУМЕНТОВ хватает "
            "для задачи; если нет — НЕ создавай автоматизацию, а скажи пользователю, чего именно не "
            "хватает. schedule — 5 полей cron: «0 9 * * 1-5» = будни в 9:00, «30 18 * * 5» = пт в 18:30; "
            "чаще раза в час нельзя. deliver_to — dialog_id, куда слать результат (обычно текущий диалог)."
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
                 "schedule_label": cron_schedule.describe(r["schedule"]),
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
            raise ValueError(f"Лимит {_SELF_AUTOMATIONS_MAX} автоматизаций: удали неактуальную (delete_my_automation).")
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
