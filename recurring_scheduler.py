"""Agent-owned scheduler for recurring Bitrix tasks.

The portal has no paid Bitrix subscription, so Bitrix's own recurring-task templates (task.template
REPLICATE) are created but never spawn tasks ("не создаётся нормально"). Instead this thread — one
per app process, same pattern as agent_automations — ticks every minute, finds recurring tasks whose
next_run_at has passed, and creates a plain one-off Bitrix task (which works without a subscription)
via mcp.context_server.create_oneoff_task_from_spec. The schedule lives in bitrix_recurring_tasks
(migrations 045 + 046).

Isolation & safety: read-only until it fires; each fire is claimed atomically (advance next_run_at
with a conditional UPDATE) so restarts and any second process never double-create; a create failure
retries soon instead of losing the slot, and never wedges the loop. Kill-switch:
RECURRING_TASKS_SCHEDULER=0.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import timedelta
from typing import Any

from app import MSK_TZ, msk_now, pg_connect

_RETRY_DELAY_S = int(os.getenv("RECURRING_TASKS_RETRY_DELAY_S", "300"))
_BOOT_DELAY_S = int(os.getenv("RECURRING_TASKS_BOOT_DELAY_S", "90"))


def _row_spec(row: dict[str, Any]) -> dict[str, Any]:
    """The task spec used to (re)create an instance. Prefer the stored jsonb spec; fall back to the
    flat columns for legacy (pre-scheduler) rows mirrored from Bitrix templates."""
    spec = row.get("spec")
    if isinstance(spec, str):
        try:
            spec = json.loads(spec)
        except Exception:  # noqa: BLE001
            spec = None
    if isinstance(spec, dict) and spec.get("responsible_bitrix_id"):
        spec = dict(spec)
        spec.setdefault("deadline_after_seconds", row.get("deadline_after_seconds"))
        return spec
    return {  # legacy fallback from flat columns
        "title": row.get("title"),
        "description": row.get("description") or row.get("title"),
        "responsible_bitrix_id": row.get("responsible_bitrix_id"),
        "creator_bitrix_id": row.get("creator_bitrix_id"),
        "deadline_after_seconds": row.get("deadline_after_seconds"),
    }


def _compute_next(row: dict[str, Any], after):
    """Next fire time for a registry row (respects the schedule + until date). None if finished."""
    from mcp import context_server as cs
    anchor = None
    if row.get("created_at") is not None and hasattr(row["created_at"], "astimezone"):
        anchor = row["created_at"].astimezone(MSK_TZ).date()
    nxt = cs._recurring_next_run(
        row["period"], int(row.get("interval_every") or 1), list(row.get("weekdays") or []),
        row.get("day_of_month"), row.get("create_time") or "10:00", after=after, anchor=anchor)
    until_d = cs._ddmmyyyy_to_date(row.get("until_date")) if row.get("until_date") else None
    if nxt and until_d and nxt.date() > until_d:
        return None
    return nxt


def _bootstrap_missing_next_run() -> None:
    """Active rows without next_run_at (e.g. legacy rows mirrored from Bitrix templates) get one
    computed so the scheduler fires them. Best-effort: retire the dead Bitrix template for legacy
    rows so a re-enabled subscription cannot double-create."""
    now = msk_now()
    try:
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, period, interval_every, weekdays, day_of_month, create_time, "
                            "until_date, created_at, bitrix_template_id, source "
                            "FROM bitrix_recurring_tasks WHERE active AND next_run_at IS NULL")
                rows = [dict(r) for r in cur.fetchall()]
    except Exception:  # noqa: BLE001
        logging.exception("recurring bootstrap: load failed")
        return
    for row in rows:
        try:
            nxt = _compute_next(row, now)
            with pg_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE bitrix_recurring_tasks SET next_run_at=%s, updated_at=now() "
                                "WHERE id=%s AND next_run_at IS NULL", (nxt, row["id"]))
            tpl = row.get("bitrix_template_id")
            if tpl and (row.get("source") or "") != "agent_scheduler":
                try:
                    from mcp import context_server as cs
                    cs._webhook_raw("task.template.delete", {"id": int(tpl)})
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            logging.exception("recurring bootstrap row %s failed", row.get("id"))


def _due_rows(now) -> list[dict[str, Any]]:
    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, title, description, responsible_bitrix_id, creator_bitrix_id, "
                        "period, interval_every, weekdays, day_of_month, create_time, "
                        "deadline_after_seconds, until_date, spec, created_at "
                        "FROM bitrix_recurring_tasks "
                        "WHERE active AND next_run_at IS NOT NULL AND next_run_at <= %s "
                        "ORDER BY next_run_at", (now,))
            return [dict(r) for r in cur.fetchall()]


def _claim(row_id: int, now, new_next) -> bool:
    """Advance next_run_at atomically; the winner (RETURNING a row) creates the instance. A second
    process/tick that lost the race sees next_run_at already moved and does nothing."""
    try:
        with pg_connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("UPDATE bitrix_recurring_tasks SET next_run_at=%s, updated_at=now() "
                                "WHERE id=%s AND active AND next_run_at <= %s RETURNING id",
                                (new_next, row_id, now))
                    return cur.fetchone() is not None
    except Exception:  # noqa: BLE001
        logging.exception("recurring %s: claim failed", row_id)
        return False


def _fire(row: dict[str, Any], now) -> None:
    from mcp import context_server as cs
    spec = _row_spec(row)
    if not spec.get("responsible_bitrix_id"):
        logging.warning("recurring %s: no responsible in spec, skipping", row.get("id"))
        return
    dl_secs = int(spec.get("deadline_after_seconds") or row.get("deadline_after_seconds") or 0)
    deadline_iso = (now + timedelta(seconds=dl_secs if dl_secs > 0 else 24 * 3600)).isoformat()
    try:
        res = cs.create_oneoff_task_from_spec(spec, deadline_iso)
        task_id = res.get("task_id")
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE bitrix_recurring_tasks SET last_created_at=now(), last_task_id=%s, "
                            "last_error=NULL, updated_at=now() WHERE id=%s", (task_id, row["id"]))
        logging.info("recurring %s: created one-off task %s", row["id"], task_id)
    except Exception as exc:  # noqa: BLE001
        msg = repr(exc)[:300]
        logging.warning("recurring %s: create failed, will retry: %s", row.get("id"), msg)
        try:
            with pg_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE bitrix_recurring_tasks SET next_run_at=%s, last_error=%s, "
                                "updated_at=now() WHERE id=%s",
                                (now + timedelta(seconds=_RETRY_DELAY_S), msg, row["id"]))
        except Exception:  # noqa: BLE001
            logging.exception("recurring %s: retry-scheduling failed", row.get("id"))


def _tick(now) -> None:
    for row in _due_rows(now):
        try:
            new_next = _compute_next(row, now)
            if _claim(row["id"], now, new_next):
                _fire(row, now)
        except Exception:  # noqa: BLE001
            logging.exception("recurring tick row %s failed", row.get("id"))


def _loop() -> None:
    time.sleep(_BOOT_DELAY_S)  # let the app finish booting
    try:
        _bootstrap_missing_next_run()
    except Exception:  # noqa: BLE001
        logging.exception("recurring bootstrap failed")
    last_minute = None
    while True:
        try:
            now = msk_now()
            minute = now.replace(second=0, microsecond=0)
            if minute != last_minute:
                last_minute = minute
                _tick(now)
        except Exception:  # noqa: BLE001
            logging.exception("recurring scheduler tick failed")
        time.sleep(15)


if os.getenv("RECURRING_TASKS_SCHEDULER", "1").strip() != "0":
    threading.Thread(target=_loop, daemon=True, name="recurring-tasks-scheduler").start()
