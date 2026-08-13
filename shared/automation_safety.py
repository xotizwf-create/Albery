"""Conflict and idempotency boundary for MCP writes made by agent automations.

The semantic read/write decision comes from the exhaustive versioned MCP policy.  Unknown tools
are deliberately treated as mutating, so a missed policy review can never bypass serialization.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from shared.db import _session_options, database_url


OBJECT_ID_KEYS = (
    "bitrix_task_id", "task_id", "parent_task_id", "deal_id", "lead_id", "contact_id",
    "company_id", "pipeline_id", "category_id", "conversation_id", "dialog_id", "chat_id",
    "message_id", "bitrix_message_id", "comment_id", "activity_id", "call_id", "recording_id",
    "folder_id", "file_id", "attachment_id", "document_id", "doc_id", "sheet_id",
    "spreadsheet_id", "row_id", "automation_id", "recurring_id", "recommendation_id",
    "interaction_id", "report_id", "script_id", "field_id", "item_id", "id",
)
_LOCK_TIMEOUT_S = float(os.getenv("MCP_BUSINESS_LOCK_TIMEOUT_S", "30"))


class BusinessObjectBusy(RuntimeError):
    pass


class AutomationEffectAmbiguous(RuntimeError):
    pass


def is_mutating_tool(name: str) -> bool:
    from mcp.tool_policy import policy_for

    try:
        return policy_for(name).effect != "read"
    except KeyError:
        return True


def business_object_key(name: str, args: dict[str, Any]) -> str:
    """Return a stable, non-secret lock key; fall back to one coarse lock per tool."""
    lowered = {str(k).lower(): v for k, v in (args or {}).items()}
    for key in OBJECT_ID_KEYS:
        value = lowered.get(key)
        if value is not None and str(value).strip() != "":
            family = key[:-3] if key.endswith("_id") else key
            family = {
                "bitrix_task": "task",
                "parent_task": "task",
                "category": "pipeline",
                "bitrix_message": "message",
            }.get(family, family)
            if key == "id":
                family = str(name).strip().lower().split("_", 1)[-1]
            return f"{family}:{str(value).strip()}"
    return f"tool:{str(name).strip().lower()}"


def effect_fingerprint(name: str, args: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"tool": str(name), "arguments": args or {}},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _signed_lock_key(value: str) -> int:
    raw = int.from_bytes(hashlib.sha256(("albery-business:" + value).encode()).digest()[:8], "big")
    return raw - (1 << 64) if raw >= (1 << 63) else raw


@contextmanager
def business_lock(object_key: str, timeout_s: float = _LOCK_TIMEOUT_S) -> Iterator[None]:
    """Hold a cross-process PostgreSQL advisory lock for one external mutation."""
    conn = psycopg.connect(
        database_url(), row_factory=dict_row, options=_session_options(), autocommit=True
    )
    lock_key = _signed_lock_key(object_key)
    acquired = False
    deadline = time.monotonic() + max(0.0, timeout_s)
    try:
        while True:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(%s) AS taken", (lock_key,))
                acquired = bool((cur.fetchone() or {}).get("taken"))
            if acquired:
                yield
                return
            if time.monotonic() >= deadline:
                raise BusinessObjectBusy(
                    f"business object is busy; retry after another operation finishes ({object_key})"
                )
            time.sleep(0.1)
    finally:
        if acquired:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(%s)", (lock_key,))
            except Exception:  # noqa: BLE001
                logging.warning("business lock explicit unlock failed", exc_info=True)
        conn.close()


def _effect_begin(
    run_id: int, name: str, args: dict[str, Any], object_key: str
) -> tuple[str, bool, Any]:
    """Claim an effect fingerprint, or return its already completed result."""
    from app import pg_connect

    fingerprint = effect_fingerprint(name, args)
    with pg_connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO agent_automation_tool_effects "
                    "(run_id, fingerprint, tool_name, object_key, status) "
                    "VALUES (%s, %s, %s, %s, 'started') "
                    "ON CONFLICT (run_id, fingerprint) DO NOTHING RETURNING status, result_json",
                    (run_id, fingerprint, name, object_key),
                )
                row = cur.fetchone()
                inserted = row is not None
                if not inserted:
                    cur.execute(
                        "SELECT status, result_json FROM agent_automation_tool_effects "
                        "WHERE run_id = %s AND fingerprint = %s FOR UPDATE",
                        (run_id, fingerprint),
                    )
                    row = cur.fetchone() or {}
                if row.get("status") == "done":
                    return fingerprint, True, row.get("result_json")
                if not inserted or row.get("status") != "started":
                    raise AutomationEffectAmbiguous(
                        f"mutating action {name} has an ambiguous prior outcome in run {run_id}"
                    )
                cur.execute(
                    "UPDATE agent_automation_runs SET had_mutating_effect = TRUE, updated_at = now() "
                    "WHERE id = %s",
                    (run_id,),
                )
    return fingerprint, False, None


def _effect_existing(run_id: int, fingerprint: str) -> dict[str, Any] | None:
    from app import pg_connect

    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, result_json FROM agent_automation_tool_effects "
                "WHERE run_id = %s AND fingerprint = %s",
                (run_id, fingerprint),
            )
            return cur.fetchone()


def _effect_finish(run_id: int, fingerprint: str, *, result: Any = None, error: str | None = None) -> None:
    from app import pg_connect

    status = "error" if error else "done"
    with pg_connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE agent_automation_tool_effects SET status = %s, result_json = %s, "
                    "last_error = %s, finished_at = now(), updated_at = now() "
                    "WHERE run_id = %s AND fingerprint = %s",
                    (status,
                     Jsonb(json.loads(json.dumps(result, ensure_ascii=False, default=str)))
                     if not error else None,
                     (error or "")[:500] or None,
                     run_id, fingerprint),
                )


def guarded_tool_call(
    name: str,
    args: dict[str, Any],
    handler: Callable[[dict[str, Any]], Any],
    *,
    automation_run_id: int | None = None,
) -> Any:
    """Execute one tool under write serialization and optional per-run deduplication."""
    if not is_mutating_tool(name):
        return handler(args)

    object_key = business_object_key(name, args)
    fingerprint = effect_fingerprint(name, args)
    if automation_run_id is not None:
        existing = _effect_existing(automation_run_id, fingerprint)
        if existing:
            if existing.get("status") == "done":
                return existing.get("result_json")
            raise AutomationEffectAmbiguous(
                f"mutating action {name} has an ambiguous prior outcome in run {automation_run_id}"
            )

    with business_lock(object_key):
        if automation_run_id is not None:
            fingerprint, cached_found, cached = _effect_begin(
                automation_run_id, name, args, object_key
            )
            if cached_found:
                return cached
        try:
            result = handler(args)
        except Exception as exc:
            if automation_run_id is not None:
                _effect_finish(automation_run_id, fingerprint, error=str(exc))
            raise
        if automation_run_id is not None:
            _effect_finish(automation_run_id, fingerprint, result=result)
        return result
