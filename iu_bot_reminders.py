"""Durable reminders for the IU support dialog."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Any

from config import MSK_TZ

WAIT_MINUTES = int(os.getenv("IU_BOT_REMINDER_MINUTES", "30") or 30)
STALE_HOURS = float(os.getenv("IU_BOT_REMINDER_STALE_HOURS", "3") or 3)


@dataclass(frozen=True)
class DeliveryDecision:
    action: str  # send | wait | cancel
    retry_at: datetime | None = None


def enabled() -> bool:
    return os.getenv("IU_BOT_REMINDERS_ENABLED", "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def delivery_decision(now: datetime, due_at: datetime) -> DeliveryDecision:
    now = now.astimezone(MSK_TZ)
    due_at = due_at.astimezone(MSK_TZ)
    if now - due_at > timedelta(hours=STALE_HOURS):
        return DeliveryDecision("cancel")
    if 9 <= now.hour < 21:
        return DeliveryDecision("send")
    if now.hour < 9:
        retry = datetime.combine(now.date(), dt_time(9), tzinfo=MSK_TZ)
    else:
        retry = datetime.combine(
            now.date() + timedelta(days=1), dt_time(9), tzinfo=MSK_TZ
        )
    if retry - due_at > timedelta(hours=STALE_HOURS):
        return DeliveryDecision("cancel")
    return DeliveryDecision("wait", retry)


def _schedule(
    conversation_id: int,
    kind: str,
    *,
    anchor_message_id: int,
    now: datetime | None = None,
) -> None:
    if not enabled():
        return
    from shared.db import connect

    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    due_at = moment + timedelta(minutes=max(1, WAIT_MINUTES))
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO iu_bot_reminders (
                    conversation_id, kind, anchor_message_id, due_at, status
                )
                VALUES (%s, %s, %s, %s, 'pending')
                ON CONFLICT (conversation_id, kind)
                DO UPDATE SET anchor_message_id = EXCLUDED.anchor_message_id,
                              due_at = EXCLUDED.due_at,
                              status = 'pending',
                              locked_by = NULL,
                              locked_until = NULL,
                              attempts = 0,
                              last_error = NULL,
                              updated_at = now()
                """,
                (int(conversation_id), kind, int(anchor_message_id or 0), due_at),
            )


def schedule_waiting_question(
    conversation_id: int, *, anchor_message_id: int, now: datetime | None = None
) -> None:
    _schedule(
        conversation_id,
        "waiting_question",
        anchor_message_id=anchor_message_id,
        now=now,
    )


def schedule_after_answer(
    conversation_id: int, *, anchor_message_id: int, now: datetime | None = None
) -> None:
    _schedule(
        conversation_id,
        "after_answer",
        anchor_message_id=anchor_message_id,
        now=now,
    )


def cancel_all(conversation_id: int) -> None:
    from shared.db import connect

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE iu_bot_reminders
                   SET status = 'cancelled', locked_by = NULL, locked_until = NULL,
                       updated_at = now()
                 WHERE conversation_id = %s
                   AND status IN ('pending', 'leased')
                """,
                (int(conversation_id),),
            )


def claim_due(
    *, worker_id: str, limit: int = 20, now: datetime | None = None
) -> list[dict[str, Any]]:
    from shared.db import connect

    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    lease_until = moment + timedelta(seconds=90)
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE iu_bot_reminders
                   SET status = 'pending', locked_by = NULL, locked_until = NULL,
                       updated_at = now()
                 WHERE status = 'leased' AND locked_until <= %s
                """,
                (moment,),
            )
            cur.execute(
                """
                WITH due AS (
                    SELECT conversation_id, kind
                      FROM iu_bot_reminders
                     WHERE status = 'pending' AND due_at <= %s
                     ORDER BY due_at
                     FOR UPDATE SKIP LOCKED
                     LIMIT %s
                )
                UPDATE iu_bot_reminders r
                   SET status = 'leased', locked_by = %s, locked_until = %s,
                       attempts = attempts + 1, updated_at = now()
                  FROM due
                 WHERE r.conversation_id = due.conversation_id
                   AND r.kind = due.kind
                RETURNING r.*
                """,
                (moment, min(100, max(1, int(limit))), worker_id, lease_until),
            )
            return [dict(row) for row in cur.fetchall()]


def finish(
    row: dict[str, Any],
    *,
    worker_id: str,
    status: str,
    retry_at: datetime | None = None,
    error: str = "",
) -> None:
    from shared.db import connect

    if status not in {"pending", "sent", "cancelled"}:
        raise ValueError(status)
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE iu_bot_reminders
                   SET status = %s,
                       due_at = COALESCE(%s, due_at),
                       locked_by = NULL,
                       locked_until = NULL,
                       last_error = %s,
                       updated_at = now()
                 WHERE conversation_id = %s AND kind = %s
                   AND status = 'leased' AND locked_by = %s
                """,
                (
                    status,
                    retry_at,
                    str(error or "")[:1000] or None,
                    int(row["conversation_id"]),
                    str(row["kind"]),
                    worker_id,
                ),
            )
