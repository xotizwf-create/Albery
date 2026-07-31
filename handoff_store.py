"""Durable, idempotent handoffs for customer-facing AI conversations.

A notification in a group is only a delivery attempt.  The operational promise lives in
``ai_handoffs`` until a human answer is actually delivered to the customer.  Event rows keep
the two delivery outcomes (customer receipt and internal dispatch) independently idempotent.

This module deliberately stores no copied client message or username.  The canonical text stays
in ``telegram_bot_messages``; the handoff keeps only routing identifiers, a bounded reason code
and non-PII operational metadata.
"""
from __future__ import annotations

import json
from typing import Any, Callable


OPEN_STATUSES = ("pending", "accepted")
DELIVERY_STATUSES = ("pending", "sending", "sent", "failed")
PRIORITIES = ("normal", "high", "urgent")
REASON_CODES = {
    "model_failure",
    "knowledge_gap",
    "crm_unavailable",
    "asset_unavailable",
    "delivery_failure",
    "unexpected_failure",
    "other",
}
DEFAULT_SLA_SECONDS = 300


def _row_dict(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    if isinstance(row, dict):
        return dict(row)
    if isinstance(row, (tuple, list)) and row:
        return {"id": row[0]}
    return {}


def _reason_code(value: str) -> str:
    value = str(value or "").strip().lower()
    return value if value in REASON_CODES else "other"


def open_handoff_event(
    db: Callable,
    *,
    bot: str,
    dialog_id: str | int,
    event_key: str,
    reason_code: str,
    deal_id: int | None = None,
    source_message_id: int | None = None,
    priority: str = "normal",
    owner_id: str = "iu-group",
    owner_name: str = "Группа «Работа с ИУ»",
    sla_seconds: int = DEFAULT_SLA_SECONDS,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Open/refresh one dialog handoff and register one idempotent source event.

    Repeated events update urgency and routing facts, but never reset ``created_at`` or
    ``due_at``.  ``event_created=False`` tells the caller to reuse recorded delivery outcomes
    instead of sending the same receipt/card again.
    """
    priority = priority if priority in PRIORITIES else "normal"
    reason_code = _reason_code(reason_code)
    sla_seconds = max(30, min(int(sla_seconds or DEFAULT_SLA_SECONDS), 86_400))
    event_key = str(event_key or "").strip()[:128]
    if not event_key:
        raise ValueError("event_key is required")
    payload = json.dumps(meta or {}, ensure_ascii=False)

    with db() as conn:
        with conn.cursor() as cur:
            # Serialize only identical source events. This closes the narrow race where two
            # workers both miss the preflight SELECT and one could otherwise leave an orphan
            # aggregate after losing the event-key conflict.
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (event_key,),
            )
            # Event idempotency is global, not only "while this dialog has an open handoff".
            # Telegram may replay an old update after the original handoff was resolved.  Looking
            # up the event first prevents that replay from opening a fresh aggregate.
            cur.execute(
                """
                SELECT h.id AS handoff_id, h.status, h.priority, h.due_at,
                       h.owner_id, h.owner_name, h.customer_notified,
                       h.first_dispatched_at,
                       e.id AS event_id, e.customer_delivery_status,
                       e.internal_delivery_status
                  FROM ai_handoff_events e
                  JOIN ai_handoffs h ON h.id = e.handoff_id
                 WHERE e.event_key = %s
                """,
                (event_key,),
            )
            existing = _row_dict(cur.fetchone())
            if existing:
                return {
                    **existing,
                    "handoff_id": int(existing.get("handoff_id") or 0),
                    "event_id": int(existing.get("event_id") or 0),
                    "event_created": False,
                    "customer_delivery_status": str(
                        existing.get("customer_delivery_status") or "pending"
                    ),
                    "internal_delivery_status": str(
                        existing.get("internal_delivery_status") or "pending"
                    ),
                }

            cur.execute(
                """
                INSERT INTO ai_handoffs
                    (bot, dialog_id, deal_id, priority, reason_code, owner_id, owner_name,
                     due_at, meta)
                VALUES
                    (%s, %s, %s, %s, %s, %s, %s,
                     now() + (%s * interval '1 second'), %s::jsonb)
                ON CONFLICT (bot, dialog_id) WHERE status IN ('pending', 'accepted')
                DO UPDATE SET
                    updated_at = now(),
                    deal_id = COALESCE(EXCLUDED.deal_id, ai_handoffs.deal_id),
                    priority = CASE
                        WHEN ai_handoffs.priority = 'urgent' OR EXCLUDED.priority = 'urgent'
                            THEN 'urgent'
                        WHEN ai_handoffs.priority = 'high' OR EXCLUDED.priority = 'high'
                            THEN 'high'
                        ELSE 'normal'
                    END,
                    reason_code = EXCLUDED.reason_code,
                    owner_id = CASE WHEN EXCLUDED.owner_id <> '' THEN EXCLUDED.owner_id
                                    ELSE ai_handoffs.owner_id END,
                    owner_name = CASE WHEN EXCLUDED.owner_name <> '' THEN EXCLUDED.owner_name
                                      ELSE ai_handoffs.owner_name END,
                    meta = ai_handoffs.meta || EXCLUDED.meta
                RETURNING id, status, priority, due_at, owner_id, owner_name,
                          customer_notified, first_dispatched_at
                """,
                (
                    str(bot),
                    str(dialog_id),
                    int(deal_id) if deal_id is not None else None,
                    priority,
                    reason_code,
                    str(owner_id or "")[:255],
                    str(owner_name or "")[:255],
                    sla_seconds,
                    payload,
                ),
            )
            handoff = _row_dict(cur.fetchone())
            handoff_id = int(handoff.get("id") or 0)
            if not handoff_id:
                raise RuntimeError("handoff upsert returned no id")

            cur.execute(
                """
                INSERT INTO ai_handoff_events
                    (handoff_id, event_key, source_message_id, reason_code, meta)
                VALUES (%s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (event_key) DO NOTHING
                RETURNING id, customer_delivery_status, internal_delivery_status
                """,
                (
                    handoff_id,
                    event_key,
                    int(source_message_id) if source_message_id is not None else None,
                    reason_code,
                    payload,
                ),
            )
            event = _row_dict(cur.fetchone())
            event_created = bool(event)
            if event_created:
                cur.execute(
                    "UPDATE ai_handoffs SET event_count = event_count + 1 WHERE id = %s",
                    (handoff_id,),
                )
            else:
                # A concurrent worker inserted the same event between the lookup above and our
                # INSERT.  Reuse its aggregate and outcomes rather than trusting our upsert row.
                cur.execute(
                    """
                    SELECT h.id AS handoff_id, h.status, h.priority, h.due_at,
                           h.owner_id, h.owner_name, h.customer_notified,
                           h.first_dispatched_at,
                           e.id AS event_id, e.customer_delivery_status,
                           e.internal_delivery_status
                      FROM ai_handoff_events e
                      JOIN ai_handoffs h ON h.id = e.handoff_id
                     WHERE e.event_key = %s
                    """,
                    (event_key,),
                )
                event = _row_dict(cur.fetchone())
                handoff = event
                handoff_id = int(event.get("handoff_id") or 0)
            event_id = int(event.get("id") or event.get("event_id") or 0)
            if not event_id:
                raise RuntimeError("handoff event upsert returned no id")

    return {
        **handoff,
        "handoff_id": handoff_id,
        "event_id": event_id,
        "event_created": event_created,
        "customer_delivery_status": str(
            event.get("customer_delivery_status") or "pending"
        ),
        "internal_delivery_status": str(
            event.get("internal_delivery_status") or "pending"
        ),
    }


def claim_delivery(db: Callable, event_id: int, *, target: str) -> bool:
    """Atomically claim the event's only automatic delivery attempt.

    A deterministic replay must not retry a failed/ambiguous external send: Telegram may have
    accepted it even when our client saw a timeout.  Follow-up attempts are explicit human/SLA
    reconciliation, not another execution of the source event.
    """
    if target not in {"customer", "internal"}:
        raise ValueError("target must be customer or internal")
    status_col = f"{target}_delivery_status"
    attempts_col = f"{target}_delivery_attempts"
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE ai_handoff_events
                   SET {status_col} = 'sending',
                       {attempts_col} = {attempts_col} + 1,
                       {target}_delivery_started_at = now(),
                       updated_at = now()
                 WHERE id = %s
                   AND {status_col} = 'pending'
                RETURNING id
                """,
                (int(event_id),),
            )
            return bool(cur.fetchone())


def complete_delivery(
    db: Callable,
    event_id: int,
    *,
    target: str,
    sent: bool,
    destination: str = "",
    external_message_id: str | int = "",
    error_code: str = "",
) -> None:
    """Record a delivery postcondition and update the aggregate handoff."""
    if target not in {"customer", "internal"}:
        raise ValueError("target must be customer or internal")
    status_col = f"{target}_delivery_status"
    error_col = f"{target}_error_code"
    completed_col = f"{target}_delivered_at"
    status = "sent" if sent else "failed"
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE ai_handoff_events
                   SET {status_col} = %s,
                       {error_col} = %s,
                       {completed_col} = CASE WHEN %s THEN now() ELSE NULL END,
                       updated_at = now(),
                       internal_destination = CASE
                           WHEN %s = 'internal' AND %s <> '' THEN %s
                           ELSE internal_destination END,
                       internal_message_id = CASE
                           WHEN %s = 'internal' AND %s <> '' THEN %s
                           ELSE internal_message_id END
                 WHERE id = %s
                """,
                (
                    status,
                    str(error_code or "")[:100],
                    bool(sent),
                    target,
                    str(destination or ""),
                    str(destination or "")[:255],
                    target,
                    str(external_message_id or ""),
                    str(external_message_id or "")[:255],
                    int(event_id),
                ),
            )
            if target == "customer" and sent:
                cur.execute(
                    """
                    UPDATE ai_handoffs
                       SET updated_at = now(), customer_notified = TRUE
                     WHERE id = (
                         SELECT handoff_id FROM ai_handoff_events WHERE id = %s
                     )
                    """,
                    (int(event_id),),
                )
            if target == "internal" and sent:
                cur.execute(
                    """
                    UPDATE ai_handoffs
                       SET updated_at = now(),
                           first_dispatched_at = COALESCE(first_dispatched_at, now()),
                           destination = %s,
                           external_message_id = %s
                     WHERE id = (
                         SELECT handoff_id FROM ai_handoff_events WHERE id = %s
                     )
                    """,
                    (
                        str(destination or "")[:255],
                        str(external_message_id or "")[:255],
                        int(event_id),
                    ),
                )


def resolve_for_dialog(
    db: Callable,
    *,
    bot: str,
    dialog_id: str | int,
    resolution_code: str = "human_reply_delivered",
) -> int:
    """Resolve every open record after a visible human reply reached the customer."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE ai_handoffs
                   SET status = 'resolved', updated_at = now(), resolved_at = now(),
                       resolution_code = %s
                 WHERE bot = %s AND dialog_id = %s
                   AND status IN ('pending', 'accepted')
                """,
                (
                    str(resolution_code or "")[:100],
                    str(bot),
                    str(dialog_id),
                ),
            )
            return int(getattr(cur, "rowcount", 0) or 0)


def overdue_handoffs(
    db: Callable,
    *,
    limit: int = 20,
    reminder_interval_seconds: int = 300,
) -> list[dict[str, Any]]:
    """Atomically claim overdue items eligible for another owner reminder.

    ``last_reminded_at`` and ``reminder_count`` are advanced before the external send. This is a
    short lease: another worker cannot select the same row until ``reminder_interval_seconds``
    elapses. A crash may delay the next reminder, but cannot create an immediate duplicate.
    """
    limit = max(1, min(int(limit or 20), 100))
    reminder_interval_seconds = max(
        60, min(int(reminder_interval_seconds or 300), 86_400)
    )
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH due AS (
                    SELECT id
                      FROM ai_handoffs
                     WHERE status IN ('pending', 'accepted')
                       AND due_at <= now()
                       AND (last_reminded_at IS NULL
                            OR last_reminded_at <=
                               now() - (%s * interval '1 second'))
                     ORDER BY
                           CASE priority WHEN 'urgent' THEN 0
                                         WHEN 'high' THEN 1 ELSE 2 END,
                           due_at, id
                     FOR UPDATE SKIP LOCKED
                     LIMIT %s
                ),
                claimed AS (
                    UPDATE ai_handoffs h
                       SET updated_at = now(), last_reminded_at = now(),
                           reminder_count = reminder_count + 1,
                           last_error_code = 'reminder_sending'
                      FROM due
                     WHERE h.id = due.id
                    RETURNING h.*
                )
                SELECT c.id, c.bot, c.dialog_id, c.deal_id, c.status, c.priority,
                       c.reason_code, c.owner_id, c.owner_name, c.due_at,
                       c.destination, c.external_message_id, c.customer_notified,
                       c.event_count, c.reminder_count,
                       NOT EXISTS (
                           SELECT 1 FROM ai_handoff_events e
                            WHERE e.handoff_id = c.id
                              AND e.customer_delivery_status <> 'sent'
                       ) AS all_customer_deliveries_sent,
                       EXISTS (
                           SELECT 1 FROM ai_handoff_events e
                            WHERE e.handoff_id = c.id
                              AND e.customer_delivery_status = 'sending'
                       ) AS customer_delivery_ambiguous,
                       EXISTS (
                           SELECT 1 FROM ai_handoff_events e
                            WHERE e.handoff_id = c.id
                              AND e.customer_delivery_status = 'failed'
                       ) AS customer_delivery_failed
                  FROM claimed c
                 ORDER BY
                       CASE c.priority WHEN 'urgent' THEN 0
                                       WHEN 'high' THEN 1 ELSE 2 END,
                       c.due_at, c.id
                """,
                (reminder_interval_seconds, limit),
            )
            return [_row_dict(row) for row in (cur.fetchall() or [])]


def record_reminder_result(
    db: Callable,
    handoff_id: int,
    *,
    sent: bool,
    destination: str = "",
    external_message_id: str | int = "",
    error_code: str = "",
) -> None:
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE ai_handoffs
                   SET updated_at = now(),
                       destination = CASE WHEN %s THEN %s ELSE destination END,
                       external_message_id = CASE WHEN %s THEN %s
                                                  ELSE external_message_id END,
                       last_error_code = %s
                 WHERE id = %s AND status IN ('pending', 'accepted')
                """,
                (
                    bool(sent),
                    str(destination or "")[:255],
                    bool(sent),
                    str(external_message_id or "")[:255],
                    str(error_code or "")[:100],
                    int(handoff_id),
                ),
            )
