"""Durable manager-response alerts for public IU Telegram conversations.

The client-facing bot may acknowledge a hand-off, but that service message is not
an operator answer.  For manager-owned conversations the worker therefore counts
only real operator replies.  For AI-owned conversations a delivered AI reply also
closes the wait; if AI stays silent for ten minutes, the lead is escalated too.
"""
from __future__ import annotations

import os
from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Any, Callable, Iterable

from config import MSK_TZ


SOURCE_KEY = "telegram_bot"
ALERT_MINUTES = (10, 30, 60)
MORNING_START = dt_time(9, 0)
# The owner explicitly included 18:00 and excluded 18:01.
EVENING_END_EXCLUSIVE = dt_time(18, 1)
AFTER_HOURS_CLIENT_REPLY = (
    "Передал ваш запрос менеджеру. Сейчас нерабочее время — "
    "менеджер ответит завтра утром!"
)
_ACTIVE_STATUSES = ("new", "open", "waiting")


def enabled() -> bool:
    return os.getenv("IU_MANAGER_RESPONSE_WATCH_ENABLED", "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _utc_moment(now: datetime | None = None) -> datetime:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def msk_moment(now: datetime | None = None) -> datetime:
    return _utc_moment(now).astimezone(MSK_TZ)


def manager_notifications_open(now: datetime | None = None) -> bool:
    local_time = msk_moment(now).timetz().replace(tzinfo=None)
    return MORNING_START <= local_time < EVENING_END_EXCLUSIVE


def next_manager_morning(now: datetime | None = None) -> datetime:
    local = msk_moment(now)
    today_morning = datetime.combine(local.date(), MORNING_START, tzinfo=MSK_TZ)
    if local < today_morning:
        return today_morning
    return datetime.combine(
        local.date() + timedelta(days=1),
        MORNING_START,
        tzinfo=MSK_TZ,
    )


def after_hours_period_key(
    conversation_id: int,
    now: datetime | None = None,
) -> str:
    morning = next_manager_morning(now)
    return f"iu-bot:after-hours:{int(conversation_id)}:{morning.date().isoformat()}"


def _connect():
    from shared.db import connect

    return connect()


def _waiting_rows(
    connection,
    *,
    source_key: str = SOURCE_KEY,
    conversation_ids: Iterable[int] | None = None,
) -> list[dict[str, Any]]:
    ids = (
        sorted({int(value) for value in conversation_ids})
        if conversation_ids is not None
        else None
    )
    if ids == []:
        return []
    id_clause = "AND c.id = ANY(%s)" if ids is not None else ""
    params: list[Any] = [source_key, list(_ACTIVE_STATUSES)]
    if ids is not None:
        params.append(ids)
    with connection.cursor() as cur:
        cur.execute(
            f"""
            SELECT c.id AS conversation_id,
                   COALESCE(
                       NULLIF(btrim(c.display_name), ''),
                       CASE
                           WHEN NULLIF(btrim(c.username), '') IS NOT NULL
                           THEN '@' || ltrim(btrim(c.username), '@')
                           ELSE 'Клиент'
                       END
                   ) AS client_name,
                   pending.id AS anchor_message_id,
                   pending.occurred_at AS anchor_occurred_at
              FROM funnel_workspace_conversations c
              JOIN LATERAL (
                    SELECT client.id, client.occurred_at
                      FROM funnel_workspace_messages client
                     WHERE client.conversation_id = c.id
                       AND client.author_type = 'client'
                       AND client.id > COALESCE((
                               SELECT max(answer.id)
                                 FROM funnel_workspace_messages answer
                                WHERE answer.conversation_id = c.id
                                  AND (
                                      answer.author_type = 'operator'
                                      OR (
                                          answer.author_type = 'agent'
                                          AND c.control_mode <> 'human'
                                          AND NOT (
                                              c.metadata
                                                  ->> 'manager_requested_at'
                                              IS NOT NULL
                                              AND (
                                                  c.metadata
                                                      ->> 'manager_request_handled_at'
                                                  IS NULL
                                                  OR c.metadata
                                                      ->> 'manager_request_handled_at'
                                                     < c.metadata
                                                      ->> 'manager_requested_at'
                                              )
                                          )
                                      )
                                  )
                                  AND answer.direction = 'outbound'
                                  AND answer.delivery_status IN ('sent', 'unknown')
                                  AND (
                                      answer.metadata ->> 'telegram_deleted'
                                  ) IS DISTINCT FROM 'true'
                           ), 0)
                     ORDER BY client.id
                     LIMIT 1
              ) pending ON TRUE
             WHERE c.source_key = %s
               AND c.status = ANY(%s)
               {id_clause}
               AND COALESCE((
                       SELECT state_message.metadata ->> 'iu_event'
                         FROM funnel_workspace_messages state_message
                        WHERE state_message.conversation_id = c.id
                          AND state_message.metadata ->> 'iu_event'
                              IN ('stop', 'start', 'menu', 'support_enter')
                        ORDER BY state_message.id DESC
                        LIMIT 1
                   ), '') <> 'stop'
             ORDER BY pending.occurred_at, c.id
            """,
            tuple(params),
        )
        return [dict(row) for row in cur.fetchall()]


def _morning_cutoff(local_now: datetime) -> datetime:
    return datetime.combine(local_now.date(), MORNING_START, tzinfo=MSK_TZ)


def due_kind(anchor_at: datetime, now: datetime) -> str | None:
    local_now = msk_moment(now)
    local_anchor = anchor_at.astimezone(MSK_TZ)
    if local_anchor < _morning_cutoff(local_now):
        return "morning"
    waited_minutes = int(
        (_utc_moment(now) - anchor_at.astimezone(timezone.utc)).total_seconds() // 60
    )
    due = [minutes for minutes in ALERT_MINUTES if waited_minutes >= minutes]
    return f"{max(due)}m" if due else None


def sync_due_alerts(
    *,
    now: datetime | None = None,
    source_key: str = SOURCE_KEY,
    connect_factory: Callable[[], Any] | None = None,
) -> int:
    """Create at most the highest currently due alert for every waiting client."""

    moment = _utc_moment(now)
    if not enabled() or not manager_notifications_open(moment):
        return 0
    connector = connect_factory or _connect
    inserted = 0
    with connector() as connection:
        rows = _waiting_rows(connection, source_key=source_key)
        current = {
            (int(row["conversation_id"]), int(row["anchor_message_id"]))
            for row in rows
        }
        with connection.cursor() as cur:
            # Pending alerts from an already answered/replaced client turn must
            # never be delivered later after a lease or service restart.
            cur.execute(
                """
                SELECT a.id, a.conversation_id, a.anchor_message_id
                  FROM iu_manager_wait_alerts a
                  JOIN funnel_workspace_conversations c
                    ON c.id = a.conversation_id
                 WHERE a.status = 'pending'
                   AND c.source_key = %s
                """,
                (source_key,),
            )
            stale_ids = [
                int(row["id"])
                for row in cur.fetchall()
                if (
                    int(row["conversation_id"]),
                    int(row["anchor_message_id"]),
                )
                not in current
            ]
            if stale_ids:
                cur.execute(
                    """
                    UPDATE iu_manager_wait_alerts
                       SET status = 'cancelled', updated_at = now()
                     WHERE id = ANY(%s)
                    """,
                    (stale_ids,),
                )

            for row in rows:
                kind = due_kind(row["anchor_occurred_at"], moment)
                if not kind:
                    continue
                conversation_id = int(row["conversation_id"])
                anchor_message_id = int(row["anchor_message_id"])
                if kind == "morning":
                    cur.execute(
                        """
                        UPDATE iu_manager_wait_alerts
                           SET status = 'cancelled', updated_at = now()
                         WHERE conversation_id = %s
                           AND anchor_message_id = %s
                           AND kind <> 'morning'
                           AND status = 'pending'
                        """,
                        (conversation_id, anchor_message_id),
                    )
                else:
                    threshold = int(kind[:-1])
                    lower_kinds = [
                        f"{minutes}m"
                        for minutes in ALERT_MINUTES
                        if minutes < threshold
                    ]
                    if lower_kinds:
                        cur.execute(
                            """
                            UPDATE iu_manager_wait_alerts
                               SET status = 'cancelled', updated_at = now()
                             WHERE conversation_id = %s
                               AND anchor_message_id = %s
                               AND kind = ANY(%s)
                               AND status = 'pending'
                            """,
                            (
                                conversation_id,
                                anchor_message_id,
                                lower_kinds,
                            ),
                        )
                cur.execute(
                    """
                    INSERT INTO iu_manager_wait_alerts (
                        conversation_id,
                        anchor_message_id,
                        anchor_occurred_at,
                        kind,
                        due_at,
                        status
                    )
                    VALUES (%s, %s, %s, %s, %s, 'pending')
                    ON CONFLICT (conversation_id, anchor_message_id, kind)
                    DO NOTHING
                    """,
                    (
                        conversation_id,
                        anchor_message_id,
                        row["anchor_occurred_at"],
                        kind,
                        moment,
                    ),
                )
                inserted += max(0, int(cur.rowcount or 0))
    return inserted


def _claim(
    *,
    worker_id: str,
    kind: str,
    limit: int,
    now: datetime,
    source_key: str,
    connect_factory: Callable[[], Any] | None,
) -> list[dict[str, Any]]:
    connector = connect_factory or _connect
    lease_until = now + timedelta(seconds=90)
    with connector() as connection:
        with connection.cursor() as cur:
            cur.execute(
                """
                UPDATE iu_manager_wait_alerts
                   SET status = 'pending',
                       locked_by = NULL,
                       locked_until = NULL,
                       updated_at = now()
                 WHERE status = 'leased'
                   AND locked_until <= %s
                """,
                (now,),
            )
            kind_clause = "a.kind = 'morning'" if kind == "morning" else "a.kind <> 'morning'"
            cur.execute(
                f"""
                WITH due AS (
                    SELECT a.id
                      FROM iu_manager_wait_alerts a
                      JOIN funnel_workspace_conversations source_conversation
                        ON source_conversation.id = a.conversation_id
                     WHERE a.status = 'pending'
                       AND a.due_at <= %s
                       AND source_conversation.source_key = %s
                       AND {kind_clause}
                     ORDER BY a.anchor_occurred_at, a.id
                     FOR UPDATE SKIP LOCKED
                     LIMIT %s
                ),
                claimed AS (
                    UPDATE iu_manager_wait_alerts a
                       SET status = 'leased',
                           locked_by = %s,
                           locked_until = %s,
                           attempts = attempts + 1,
                           updated_at = now()
                      FROM due
                     WHERE a.id = due.id
                    RETURNING a.*
                )
                SELECT claimed.*,
                       COALESCE(
                           NULLIF(btrim(c.display_name), ''),
                           CASE
                               WHEN NULLIF(btrim(c.username), '') IS NOT NULL
                               THEN '@' || ltrim(btrim(c.username), '@')
                               ELSE 'Клиент'
                           END
                       ) AS client_name
                  FROM claimed
                  JOIN funnel_workspace_conversations c
                    ON c.id = claimed.conversation_id
                 ORDER BY claimed.anchor_occurred_at, claimed.id
                """,
                (
                    now,
                    source_key,
                    min(100, max(1, int(limit))),
                    worker_id,
                    lease_until,
                ),
            )
            return [dict(row) for row in cur.fetchall()]


def _current_wait_map(
    conversation_ids: Iterable[int],
    *,
    source_key: str,
    connect_factory: Callable[[], Any] | None,
) -> dict[int, dict[str, Any]]:
    connector = connect_factory or _connect
    with connector() as connection:
        rows = _waiting_rows(
            connection,
            source_key=source_key,
            conversation_ids=conversation_ids,
        )
    return {int(row["conversation_id"]): row for row in rows}


def _finish(
    rows: Iterable[dict[str, Any]],
    *,
    worker_id: str,
    status: str,
    retry_at: datetime | None = None,
    error: str = "",
    connect_factory: Callable[[], Any] | None = None,
) -> None:
    ids = [int(row["id"]) for row in rows]
    if not ids:
        return
    if status not in {"pending", "sent", "cancelled"}:
        raise ValueError(status)
    connector = connect_factory or _connect
    with connector() as connection:
        with connection.cursor() as cur:
            cur.execute(
                """
                UPDATE iu_manager_wait_alerts
                   SET status = %s,
                       due_at = COALESCE(%s, due_at),
                       locked_by = NULL,
                       locked_until = NULL,
                       last_error = %s,
                       updated_at = now()
                 WHERE id = ANY(%s)
                   AND status = 'leased'
                   AND locked_by = %s
                """,
                (
                    status,
                    retry_at,
                    str(error or "")[:1000] or None,
                    ids,
                    worker_id,
                ),
            )


def _safe_client_name(value: Any) -> str:
    cleaned = " ".join(
        str(value or "Клиент").replace("[", "").replace("]", "").split()
    )
    return cleaned[:160] or "Клиент"


def _dialog_url(conversation_id: int) -> str:
    return f"https://www.m4s.ru/agent-funnels/{int(conversation_id)}"


def format_individual_alert(row: dict[str, Any]) -> str:
    minutes = int(str(row["kind"]).removesuffix("m"))
    name = _safe_client_name(row.get("client_name"))
    url = _dialog_url(int(row["conversation_id"]))
    return (
        f"Клиент {name} ждёт ответа уже {minutes} минут. "
        f"Ему нужно срочно ответить: [URL={url}]открыть диалог[/URL]."
    )


def _wait_label(anchor_at: datetime, now: datetime) -> str:
    total_minutes = max(
        0,
        int(
            (
                _utc_moment(now) - anchor_at.astimezone(timezone.utc)
            ).total_seconds()
            // 60
        ),
    )
    hours, minutes = divmod(total_minutes, 60)
    if hours:
        return f"{hours} ч {minutes} мин"
    return f"{minutes} мин"


def format_morning_summary(
    rows: list[dict[str, Any]],
    *,
    now: datetime,
) -> str:
    ordered = sorted(
        rows,
        key=lambda row: (
            row["anchor_occurred_at"],
            int(row["conversation_id"]),
        ),
    )
    visible = ordered[:25]
    lines = [
        "[b]Доброе утро! Сначала ответьте клиентам, которые ждут дольше всех:[/b]",
        "",
    ]
    for index, row in enumerate(visible, 1):
        name = _safe_client_name(row.get("client_name"))
        wait = _wait_label(row["anchor_occurred_at"], now)
        url = _dialog_url(int(row["conversation_id"]))
        lines.append(
            f"{index}. [URL={url}]Клиент {name}[/URL] — ждёт ответа {wait}."
        )
    hidden = len(ordered) - len(visible)
    if hidden > 0:
        lines.extend(
            [
                "",
                f"Ещё клиентов в очереди: {hidden}. "
                "[URL=https://www.m4s.ru/agent-funnels/]Открыть все обращения[/URL].",
            ]
        )
    return "\n".join(lines)


def _rows_still_current(
    rows: list[dict[str, Any]],
    *,
    source_key: str,
    connect_factory: Callable[[], Any] | None,
) -> list[dict[str, Any]]:
    current = _current_wait_map(
        (int(row["conversation_id"]) for row in rows),
        source_key=source_key,
        connect_factory=connect_factory,
    )
    return [
        row
        for row in rows
        if int(row["conversation_id"]) in current
        and int(current[int(row["conversation_id"])]["anchor_message_id"])
        == int(row["anchor_message_id"])
    ]


def process_once(
    *,
    worker_id: str,
    notify: Callable[[str], Any],
    limit: int = 50,
    now: datetime | None = None,
    source_key: str = SOURCE_KEY,
    connect_factory: Callable[[], Any] | None = None,
) -> int:
    """Create, lease, revalidate and deliver due manager alerts."""

    moment = _utc_moment(now)
    if not enabled() or not manager_notifications_open(moment):
        return 0
    sync_due_alerts(
        now=moment,
        source_key=source_key,
        connect_factory=connect_factory,
    )
    processed = 0

    morning = _claim(
        worker_id=worker_id,
        kind="morning",
        limit=limit,
        now=moment,
        source_key=source_key,
        connect_factory=connect_factory,
    )
    if morning:
        valid = _rows_still_current(
            morning,
            source_key=source_key,
            connect_factory=connect_factory,
        )
        invalid_ids = {int(row["id"]) for row in morning} - {
            int(row["id"]) for row in valid
        }
        _finish(
            [row for row in morning if int(row["id"]) in invalid_ids],
            worker_id=worker_id,
            status="cancelled",
            connect_factory=connect_factory,
        )
        if valid:
            try:
                notify(format_morning_summary(valid, now=moment))
            except Exception as exc:  # noqa: BLE001 - durable retry on integration failure
                _finish(
                    valid,
                    worker_id=worker_id,
                    status="pending",
                    retry_at=moment + timedelta(seconds=60),
                    error=str(exc),
                    connect_factory=connect_factory,
                )
            else:
                _finish(
                    valid,
                    worker_id=worker_id,
                    status="sent",
                    connect_factory=connect_factory,
                )
        processed += len(morning)

    remaining = max(1, int(limit) - processed)
    individual = _claim(
        worker_id=worker_id,
        kind="individual",
        limit=remaining,
        now=moment,
        source_key=source_key,
        connect_factory=connect_factory,
    )
    current_individual = {
        int(row["id"])
        for row in _rows_still_current(
            individual,
            source_key=source_key,
            connect_factory=connect_factory,
        )
    }
    for row in individual:
        if int(row["id"]) not in current_individual:
            _finish(
                [row],
                worker_id=worker_id,
                status="cancelled",
                connect_factory=connect_factory,
            )
            continue
        try:
            notify(format_individual_alert(row))
        except Exception as exc:  # noqa: BLE001 - durable retry on integration failure
            _finish(
                [row],
                worker_id=worker_id,
                status="pending",
                retry_at=moment + timedelta(seconds=60),
                error=str(exc),
                connect_factory=connect_factory,
            )
        else:
            _finish(
                [row],
                worker_id=worker_id,
                status="sent",
                connect_factory=connect_factory,
            )
    return processed + len(individual)
