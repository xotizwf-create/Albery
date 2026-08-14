"""Durable capture and stage transitions for Bitrix agent inbound work.

This module owns persistence only. Provider/model behavior stays in ``b24bot`` so the queue can be
failure-tested without importing the full Flask application. OAuth and webhook secrets must never
enter ``payload``.
"""
from __future__ import annotations

import os
import re
import socket
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from psycopg.types.json import Jsonb


TERMINAL = frozenset({"sent", "ignored", "review", "failed"})
RETRYABLE_CAPTURE = frozenset({"queued", "preparing"})
AMBIGUOUS = frozenset({"brain_running", "sending"})
SECRET_KEYS = frozenset({
    "access_token", "application_token", "refresh_token", "auth", "authorization",
    "client_secret", "secret", "password", "passwd",
})


def enabled() -> bool:
    return os.getenv("BITRIX_DURABLE_INBOUND_ENABLED", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }


def worker_id(index: int = 0) -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{index}:{uuid.uuid4().hex[:8]}"


def _is_secret_key(key: Any) -> bool:
    lowered = str(key).strip().lower()
    # Flattened webhook keys look like ``auth[access_token]``. Match credential field names,
    # not arbitrary substrings: ``author_id`` is business data and must not be mistaken for auth.
    parts = [part for part in re.split(r"[^a-z0-9_]+", lowered) if part]
    return any(part in SECRET_KEYS or part.endswith("_token") or part.endswith("_secret")
               for part in parts)


def _token_free_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _token_free_value(item)
            for key, item in value.items()
            if not _is_secret_key(key)
        }
    if isinstance(value, list):
        return [_token_free_value(item) for item in value]
    return value


def token_free_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Recursively remove credentials before a provider event becomes durable."""
    return _token_free_value(payload or {})


def _db():
    from app import pg_connect
    return pg_connect()


def enqueue(
    *,
    event_key: str,
    event_kind: str,
    scope_key: str,
    payload: dict[str, Any],
    batch_delay_seconds: float = 0,
) -> dict[str, Any]:
    """Insert once and extend the open chat batch window without rewriting immutable payload."""
    if event_kind not in {"chat_message", "task_comment"}:
        raise ValueError("unsupported Bitrix inbound event kind")
    if not event_key or not scope_key:
        raise ValueError("event_key and scope_key are required")
    safe_payload = token_free_payload(payload)
    delay = max(0.0, min(float(batch_delay_seconds or 0), 25.0))
    with _db() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO bitrix_inbound_jobs
                      (event_key, event_kind, scope_key, payload, available_at)
                    VALUES (%s, %s, %s, %s, now() + make_interval(secs => %s))
                    ON CONFLICT (event_key) DO NOTHING
                    RETURNING id, status, true AS inserted
                    """,
                    (event_key, event_kind, scope_key, Jsonb(safe_payload), delay),
                )
                row = cur.fetchone()
                if row is None:
                    cur.execute(
                        "SELECT id, status, false AS inserted FROM bitrix_inbound_jobs WHERE event_key=%s",
                        (event_key,),
                    )
                    row = cur.fetchone()
                elif event_kind == "chat_message" and delay:
                    cur.execute(
                        """
                        UPDATE bitrix_inbound_jobs
                           SET available_at = LEAST(
                                 received_at + interval '25 seconds',
                                 GREATEST(available_at, now() + make_interval(secs => %s))
                               ),
                               updated_at = now()
                         WHERE scope_key=%s AND status='queued'
                        """,
                        (delay, scope_key),
                    )
                if event_kind == "chat_message":
                    cur.execute(
                        """
                        INSERT INTO bitrix_bot_message_seen
                          (message_id, bot_id, dialog_id, from_user_id)
                        VALUES (%s,%s,%s,%s)
                        ON CONFLICT (message_id) DO NOTHING
                        """,
                        (
                            safe_payload.get("message_id"), safe_payload.get("bot_id"),
                            str(safe_payload.get("dialog_id") or ""),
                            safe_payload.get("from_user_id"),
                        ),
                    )
                return dict(row)


def recover_expired() -> dict[str, int]:
    """Retry only pre-brain preparation; ambiguous boundaries always stop for review."""
    with _db() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE bitrix_inbound_jobs
                       SET status='queued', batch_id=NULL, lease_owner=NULL, lease_until=NULL,
                           available_at=now(), error_text='preparation lease expired; safely requeued',
                           updated_at=now()
                     WHERE status='preparing' AND lease_until < now()
                    """
                )
                requeued = cur.rowcount
                cur.execute(
                    """
                    UPDATE bitrix_inbound_jobs
                       SET status='review', lease_owner=NULL, lease_until=NULL,
                           error_text=CASE status
                             WHEN 'brain_running' THEN 'brain lease expired after no-replay boundary'
                             ELSE 'provider sending lease expired; outcome ambiguous'
                           END,
                           updated_at=now(), completed_at=now()
                     WHERE status IN ('brain_running','sending') AND lease_until < now()
                    """
                )
                review = cur.rowcount
    return {"requeued": requeued, "review": review}


def claim_next(owner: str, *, lease_seconds: int = 240) -> dict[str, Any] | None:
    """Claim one stored-answer delivery or one due scope batch with SKIP LOCKED."""
    lease_seconds = max(30, min(int(lease_seconds or 240), 900))
    recover_expired()
    with _db() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, batch_id, scope_key, status
                      FROM bitrix_inbound_jobs
                     WHERE status IN ('answer_ready','delivery_retry','queued')
                       AND available_at <= now()
                       AND (status='queued' OR lease_until IS NULL OR lease_until < now())
                     ORDER BY CASE status WHEN 'answer_ready' THEN 0 WHEN 'delivery_retry' THEN 1 ELSE 2 END,
                              received_at
                     FOR UPDATE SKIP LOCKED
                     LIMIT 1
                    """
                )
                seed = cur.fetchone()
                if seed is None:
                    return None
                status = seed["status"]
                if status in {"answer_ready", "delivery_retry"}:
                    batch_id = seed["batch_id"] or seed["id"]
                    cur.execute(
                        """
                        UPDATE bitrix_inbound_jobs
                           SET batch_id=%s, lease_owner=%s,
                               lease_until=now() + make_interval(secs => %s), updated_at=now()
                         WHERE (batch_id=%s OR id=%s) AND status=%s
                           AND (lease_until IS NULL OR lease_until < now())
                        """,
                        (batch_id, owner, lease_seconds, batch_id, seed["id"], status),
                    )
                else:
                    batch_id = seed["id"]
                    cur.execute(
                        """
                        UPDATE bitrix_inbound_jobs
                           SET status='preparing', batch_id=%s, attempts=attempts+1,
                               lease_owner=%s,
                               lease_until=now() + make_interval(secs => %s), updated_at=now()
                         WHERE scope_key=%s AND status='queued' AND available_at <= now()
                        """,
                        (batch_id, owner, lease_seconds, seed["scope_key"]),
                    )
                cur.execute(
                    """
                    SELECT * FROM bitrix_inbound_jobs
                     WHERE batch_id=%s
                     ORDER BY received_at, id
                    """,
                    (batch_id,),
                )
                rows = [dict(row) for row in cur.fetchall()]
                return {
                    "batch_id": str(batch_id),
                    "status": status,
                    "rows": rows,
                    "leader": rows[0] if rows else None,
                }


def heartbeat(batch_id: str, owner: str, *, lease_seconds: int = 240) -> bool:
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE bitrix_inbound_jobs
                   SET lease_until=now() + make_interval(secs => %s), updated_at=now()
                 WHERE batch_id=%s AND lease_owner=%s
                   AND status IN ('preparing','brain_running','answer_ready','delivery_retry','sending')
                """,
                (max(30, int(lease_seconds)), batch_id, owner),
            )
            return cur.rowcount > 0


def mark_brain_running(batch_id: str, owner: str, prepared: dict[str, Any]) -> None:
    with _db() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE bitrix_inbound_jobs
                       SET status='brain_running', prepared=%s, brain_started_at=now(),
                           updated_at=now()
                     WHERE batch_id=%s AND lease_owner=%s AND status='preparing'
                    """,
                    (Jsonb(prepared), batch_id, owner),
                )
                if cur.rowcount < 1:
                    raise RuntimeError("Bitrix inbound brain boundary claim was lost")


def merge_payload(batch_id: str, owner: str, patch: dict[str, Any]) -> None:
    """Add normalized preparation metadata without replacing the immutable captured fields."""
    with _db() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE bitrix_inbound_jobs
                       SET payload=payload || %s, updated_at=now()
                     WHERE batch_id=%s AND lease_owner=%s AND status='preparing'
                    """,
                    (Jsonb(token_free_payload(patch)), batch_id, owner),
                )
                if cur.rowcount < 1:
                    raise RuntimeError("Bitrix inbound payload lease was lost")


def store_answer(
    batch_id: str,
    owner: str,
    answer: str,
    *,
    prepared: dict[str, Any],
    turn_status: str = "ok",
    error_text: str | None = None,
) -> None:
    with _db() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE bitrix_inbound_jobs
                       SET status='answer_ready', answer=%s, prepared=%s, turn_status=%s,
                           error_text=%s, brain_completed_at=now(),
                           available_at=now(), updated_at=now()
                     WHERE batch_id=%s AND lease_owner=%s AND status IN ('preparing','brain_running')
                    """,
                    (answer, Jsonb(prepared), turn_status, error_text, batch_id, owner),
                )
                if cur.rowcount < 1:
                    raise RuntimeError("Bitrix inbound answer could not be committed")


def mark_sending(batch_id: str, owner: str, *, lease_seconds: int = 120) -> None:
    with _db() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE bitrix_inbound_jobs
                       SET status='sending', delivery_attempts=delivery_attempts+1,
                           lease_owner=%s, lease_until=now() + make_interval(secs => %s),
                           delivery_started_at=now(), updated_at=now()
                     WHERE batch_id=%s AND lease_owner=%s
                       AND status IN ('answer_ready','delivery_retry')
                    """,
                    (owner, max(30, int(lease_seconds)), batch_id, owner),
                )
                if cur.rowcount < 1:
                    raise RuntimeError("Bitrix inbound delivery claim was lost")


def mark_sent(batch_id: str, owner: str, provider_message_id: Any = None) -> None:
    with _db() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE bitrix_inbound_jobs
                       SET status='sent', provider_message_id=%s, lease_owner=NULL, lease_until=NULL,
                           error_text=NULL, completed_at=now(), updated_at=now()
                     WHERE batch_id=%s AND lease_owner=%s AND status='sending'
                    """,
                    (str(provider_message_id) if provider_message_id is not None else None,
                     batch_id, owner),
                )
                if cur.rowcount < 1:
                    raise RuntimeError("Bitrix inbound sent state could not be committed")


def mark_ignored(batch_id: str, owner: str, reason: str) -> None:
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE bitrix_inbound_jobs
                   SET status='ignored', error_text=%s, lease_owner=NULL, lease_until=NULL,
                       completed_at=now(), updated_at=now()
                 WHERE batch_id=%s AND lease_owner=%s AND status='preparing'
                """,
                (reason[:500], batch_id, owner),
            )


def mark_preparation_failure(batch_id: str, owner: str, error: str) -> str:
    """Known pre-brain failures are safe to retry; they never cross the model/tool boundary."""
    with _db() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT max(attempts) AS n FROM bitrix_inbound_jobs WHERE batch_id=%s",
                    (batch_id,),
                )
                attempts = int((cur.fetchone() or {}).get("n") or 0)
                status = "failed" if attempts >= 5 else "queued"
                delay = min(300, 5 * (2 ** max(0, attempts - 1)))
                cur.execute(
                    """
                    UPDATE bitrix_inbound_jobs
                       SET status=%s, batch_id=CASE WHEN %s='queued' THEN NULL ELSE batch_id END,
                           error_text=%s, lease_owner=NULL, lease_until=NULL,
                           available_at=now() + make_interval(secs => %s),
                           completed_at=CASE WHEN %s='failed' THEN now() ELSE NULL END,
                           updated_at=now()
                     WHERE batch_id=%s AND lease_owner=%s AND status='preparing'
                    """,
                    (status, status, error[:1000], delay, status, batch_id, owner),
                )
                return status


def mark_delivery_failure(batch_id: str, owner: str, error: str, *, ambiguous: bool) -> str:
    with _db() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                if ambiguous:
                    status = "review"
                    cur.execute(
                        """
                        UPDATE bitrix_inbound_jobs
                           SET status='review', error_text=%s, lease_owner=NULL, lease_until=NULL,
                               completed_at=now(), updated_at=now()
                         WHERE batch_id=%s AND lease_owner=%s AND status='sending'
                        """,
                        (error[:1000], batch_id, owner),
                    )
                else:
                    cur.execute(
                        "SELECT max(delivery_attempts) AS n FROM bitrix_inbound_jobs WHERE batch_id=%s",
                        (batch_id,),
                    )
                    attempts = int((cur.fetchone() or {}).get("n") or 0)
                    status = "failed" if attempts >= 5 else "delivery_retry"
                    delay = min(300, 5 * (2 ** max(0, attempts - 1)))
                    cur.execute(
                        """
                        UPDATE bitrix_inbound_jobs
                           SET status=%s, error_text=%s, lease_owner=NULL, lease_until=NULL,
                               available_at=now() + make_interval(secs => %s),
                               completed_at=CASE WHEN %s='failed' THEN now() ELSE NULL END,
                               updated_at=now()
                         WHERE batch_id=%s AND lease_owner=%s AND status='sending'
                        """,
                        (status, error[:1000], delay, status, batch_id, owner),
                    )
                return status


def mark_review(batch_id: str, owner: str, error: str) -> None:
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE bitrix_inbound_jobs
                   SET status='review', error_text=%s, lease_owner=NULL, lease_until=NULL,
                       completed_at=now(), updated_at=now()
                 WHERE batch_id=%s AND lease_owner=%s
                   AND status IN ('preparing','brain_running','answer_ready','delivery_retry','sending')
                """,
                (error[:1000], batch_id, owner),
            )


def journal_inbound(batch_id: str) -> int:
    """Atomically journal every captured chat/task message once, even after safe preparation retry."""
    with _db() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, event_kind, payload
                      FROM bitrix_inbound_jobs
                     WHERE batch_id=%s AND journaled_at IS NULL
                     FOR UPDATE
                    """,
                    (batch_id,),
                )
                rows = cur.fetchall()
                for row in rows:
                    payload = row["payload"] or {}
                    is_task = row["event_kind"] == "task_comment"
                    dialog_id = (f"task-{payload.get('task_id')}" if is_task
                                 else str(payload.get("dialog_id") or ""))
                    body = str(payload.get("message_text") or payload.get("comment_text") or "").strip()
                    if not body:
                        body = "attachment" if not is_task else "task comment"
                    cur.execute(
                        """
                        INSERT INTO bitrix_bot_messages
                          (agent_slug, bot_id, dialog_id, bitrix_user_id, direction, kind,
                           text, bitrix_message_id, meta)
                        VALUES (%s,%s,%s,%s,'in',%s,%s,%s,%s)
                        """,
                        (
                            payload.get("agent_slug"), payload.get("bot_id"), dialog_id,
                            payload.get("from_user_id") or payload.get("author_id"),
                            "task_comment" if is_task else "chat", body[:20000],
                            payload.get("comment_id") if is_task else payload.get("message_id"),
                            Jsonb({"task_id": payload.get("task_id")}) if is_task else None,
                        ),
                    )
                if rows:
                    cur.execute(
                        "UPDATE bitrix_inbound_jobs SET journaled_at=now(), updated_at=now() "
                        "WHERE batch_id=%s AND journaled_at IS NULL",
                        (batch_id,),
                    )
                return len(rows)


def inspect_health(
    *,
    now: datetime | None = None,
    query: Callable[[str, tuple[Any, ...]], list[dict[str, Any]]] | None = None,
) -> list[str]:
    """Return content-free queue problems for self-check and tests."""
    now = now or datetime.now(timezone.utc)
    if query is None:
        def query(sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
            with _db() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    return [dict(row) for row in cur.fetchall()]
    rows = query(
        """
        SELECT status, count(*) AS n, min(updated_at) AS oldest
          FROM bitrix_inbound_jobs
         WHERE status NOT IN ('sent','ignored')
         GROUP BY status
        """,
        (),
    )
    limits = {
        "queued": timedelta(minutes=5),
        "preparing": timedelta(minutes=6),
        "brain_running": timedelta(minutes=8),
        "answer_ready": timedelta(minutes=3),
        "delivery_retry": timedelta(minutes=10),
        "sending": timedelta(minutes=3),
    }
    problems: list[str] = []
    for row in rows:
        status = str(row.get("status") or "")
        count = int(row.get("n") or 0)
        oldest = row.get("oldest")
        if status in {"review", "failed"} and count:
            problems.append(f"Bitrix inbound {status}: {count}")
        elif count and status in limits and oldest and now - oldest > limits[status]:
            problems.append(f"Bitrix inbound {status} overdue: {count}")
    return problems
