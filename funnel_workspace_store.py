from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterator, Mapping

from psycopg.types.json import Jsonb

from shared.db import connect as pg_connect


VALID_STATUSES = frozenset({"new", "open", "waiting", "closed", "spam", "expired"})
VALID_CONTROL_MODES = frozenset({"ai", "human", "paused"})
VALID_AUTHOR_TYPES = frozenset({"client", "agent", "operator", "system"})
VALID_DELIVERY_RESULTS = frozenset({"sent", "failed", "unknown", "cancelled"})
VALID_UPDATE_LANES = frozenset({"business", "bot"})
ACTIVE_STATUSES = frozenset({"new", "open", "waiting"})
DEFAULT_SOURCE_KEY = "telegram"
MAX_MESSAGE_LENGTH = 4096
SCHEMA_TABLES = (
    "funnel_workspace_sources",
    "funnel_workspace_conversations",
    "funnel_workspace_messages",
    "funnel_workspace_control_events",
    "funnel_workspace_updates",
    "funnel_workspace_ai_jobs",
    "funnel_workspace_outbox",
    "funnel_workspace_crm_actions",
    "funnel_workspace_settings",
)

ConnectFactory = Callable[[], Any]


class WorkspaceStoreError(RuntimeError):
    code = "workspace_store_error"
    status_code = 400

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.details = dict(details or {})


class WorkspaceValidationError(WorkspaceStoreError):
    code = "validation_error"


class WorkspaceNotFoundError(WorkspaceStoreError):
    code = "not_found"
    status_code = 404


class WorkspaceConflictError(WorkspaceStoreError):
    code = "state_conflict"
    status_code = 409


class WorkspaceControlError(WorkspaceStoreError):
    code = "control_rejected"
    status_code = 409


class WorkspaceReplyWindowExpired(WorkspaceStoreError):
    code = "reply_window_expired"
    status_code = 409


def enabled() -> bool:
    return os.getenv("FUNNEL_WORKSPACE_ENABLED", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def human_lease_seconds() -> int:
    return _bounded_env_int("FUNNEL_WORKSPACE_HUMAN_LEASE_SECONDS", 120, 10, 86_400)


def reply_window_hours() -> int:
    return _bounded_env_int("FUNNEL_WORKSPACE_REPLY_WINDOW_HOURS", 24, 1, 48)


def ai_debounce_milliseconds() -> int:
    return _bounded_env_int("FUNNEL_WORKSPACE_AI_DEBOUNCE_MS", 1200, 0, 10_000)


def retention_days() -> int:
    return _bounded_env_int("FUNNEL_WORKSPACE_RETENTION_DAYS", 30, 7, 90)


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or str(default))
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def _now(value: datetime | None = None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _clean_optional(value: Any, limit: int = 500) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:limit]


def _required_text(value: Any, field: str, limit: int = 4096) -> str:
    text = str(value or "").strip()
    if not text:
        raise WorkspaceValidationError(f"Поле {field} обязательно.", details={"field": field})
    if len(text) > limit:
        raise WorkspaceValidationError(
            f"Поле {field} длиннее допустимых {limit} символов.",
            details={"field": field, "max_length": limit},
        )
    return text


def _positive_int(value: Any, field: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise WorkspaceValidationError(f"Поле {field} должно быть целым числом.") from exc
    if result <= 0:
        raise WorkspaceValidationError(f"Поле {field} должно быть больше нуля.")
    return result


def _record(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


@contextmanager
def _connection(connect: ConnectFactory | None = None) -> Iterator[Any]:
    factory = connect or pg_connect
    with factory() as conn:
        yield conn


def _ensure_source_cursor(
    cur: Any,
    source_key: str,
    *,
    source_type: str | None = None,
    display_name: str | None = None,
) -> None:
    cur.execute(
        """
        INSERT INTO funnel_workspace_sources
            (source_key, source_type, display_name)
        VALUES (%s, %s, %s)
        ON CONFLICT (source_key) DO NOTHING
        """,
        (
            source_key,
            source_type or ("telegram_business" if source_key == DEFAULT_SOURCE_KEY else source_key),
            display_name or ("Telegram" if source_key == DEFAULT_SOURCE_KEY else source_key),
        ),
    )


def ensure_source(
    source_key: str,
    *,
    source_type: str | None = None,
    display_name: str | None = None,
    connect: ConnectFactory | None = None,
) -> dict[str, Any]:
    key = _required_text(source_key, "source_key", 100)
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            _ensure_source_cursor(
                cur,
                key,
                source_type=_clean_optional(source_type, 100),
                display_name=_clean_optional(display_name, 200),
            )
            cur.execute(
                "SELECT * FROM funnel_workspace_sources WHERE source_key = %s",
                (key,),
            )
            return dict(cur.fetchone())


def list_sources(*, connect: ConnectFactory | None = None) -> list[dict[str, Any]]:
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT source_key, source_type, display_name, is_enabled, public_config,
                       created_at, updated_at
                  FROM funnel_workspace_sources
                 ORDER BY display_name, source_key
                """
            )
            return [dict(row) for row in cur.fetchall()]


def get_workspace_password_hash(
    *,
    connect: ConnectFactory | None = None,
) -> str:
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT setting_value
                  FROM funnel_workspace_settings
                 WHERE setting_key = 'password_hash'
                """
            )
            row = _record(cur.fetchone())
    if row is None:
        return ""
    value = row.get("setting_value")
    return value.strip() if isinstance(value, str) else ""


def set_workspace_password_hash(
    password_hash: str,
    *,
    connect: ConnectFactory | None = None,
) -> None:
    clean_hash = _required_text(password_hash, "password_hash", 1000)
    if not clean_hash.startswith("scrypt:"):
        raise WorkspaceValidationError("Поддерживается только Werkzeug scrypt hash.")
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO funnel_workspace_settings (
                    setting_key, setting_value, updated_at
                )
                VALUES ('password_hash', %s, now())
                ON CONFLICT (setting_key)
                DO UPDATE SET
                    setting_value = EXCLUDED.setting_value,
                    updated_at = now()
                """,
                (Jsonb(clean_hash),),
            )


def get_workspace_operator_name(
    *,
    connect: ConnectFactory | None = None,
) -> str:
    """Имя сотрудника, закреплённое за паролем рабочего окна.

    Вход общий для смены, поэтому имя задаётся один раз вместе с паролем, а не
    вводится руками при каждом входе: иначе в переписке появляются «Юля», «юлия»
    и пустое поле, и потом не понять, кто отвечал клиенту.
    """
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT setting_value
                  FROM funnel_workspace_settings
                 WHERE setting_key = 'operator_name'
                """
            )
            row = _record(cur.fetchone())
    if row is None:
        return ""
    value = row.get("setting_value")
    return value.strip() if isinstance(value, str) else ""


def set_workspace_operator_name(
    operator_name: Any,
    *,
    connect: ConnectFactory | None = None,
) -> str:
    clean_name = _required_text(operator_name, "operator_name", 200)
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO funnel_workspace_settings (
                    setting_key, setting_value, updated_at
                )
                VALUES ('operator_name', %s, now())
                ON CONFLICT (setting_key)
                DO UPDATE SET
                    setting_value = EXCLUDED.setting_value,
                    updated_at = now()
                """,
                (Jsonb(clean_name),),
            )
    return clean_name


def unlink_conversation_deal(
    conversation_id: Any,
    *,
    connect: ConnectFactory | None = None,
) -> dict[str, Any]:
    """Снять ссылку на сделку, которой больше нет в Битриксе.

    Держать мёртвую ссылку хуже, чем не иметь её: оператор видит этап несуществующей
    сделки, ссылка из карточки ведёт в никуда, а синхронизация каждую минуту падает.
    После снятия штатный backfill создаст связь заново — он ищет сделку по стабильному
    маркеру `[tg:<id>]`, поэтому дубль не появится, если сделка на самом деле жива.
    """
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE funnel_workspace_conversations
                   SET deal_id = NULL,
                       stage_id = NULL,
                       updated_at = now()
                 WHERE id = %s
             RETURNING *
                """,
                (_positive_int(conversation_id, "conversation_id"),),
            )
            row = _record(cur.fetchone())
    if row is None:
        raise WorkspaceNotFoundError(
            "Диалог не найден.",
            details={"conversation_id": conversation_id},
        )
    return row


def conversations_for_stage_sync(
    *,
    limit: int = 50,
    connect: ConnectFactory | None = None,
) -> list[dict[str, Any]]:
    """Активные диалоги со сделкой — у них этап в CRM могли подвинуть люди.

    Этап показывается оператору как статус обращения, поэтому он обязан догонять
    сделку сам: иначе список показывает «Новый клиент» на давно подписанном договоре.
    """
    limit = min(500, max(1, int(limit or 50)))
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, deal_id, stage_id
                  FROM funnel_workspace_conversations
                 WHERE deal_id IS NOT NULL
                   AND status IN ('new', 'open', 'waiting')
                 ORDER BY updated_at
                 LIMIT %s
                """,
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]


def ensure_conversation(
    *,
    external_chat_id: Any,
    source_key: str = DEFAULT_SOURCE_KEY,
    business_connection_id: Any = "",
    external_user_id: Any = None,
    username: Any = None,
    display_name: Any = None,
    avatar_url: Any = None,
    reply_deadline_at: datetime | None = None,
    metadata: Mapping[str, Any] | None = None,
    connect: ConnectFactory | None = None,
) -> dict[str, Any]:
    source = _required_text(source_key, "source_key", 100)
    chat_id = _required_text(external_chat_id, "external_chat_id", 200)
    business_id = str(business_connection_id or "").strip()[:300]
    user_id = int(external_user_id) if external_user_id not in (None, "") else None
    deadline = _now(reply_deadline_at) if reply_deadline_at else None
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            _ensure_source_cursor(cur, source)
            cur.execute(
                """
                INSERT INTO funnel_workspace_conversations (
                    source_key, external_chat_id, external_user_id,
                    business_connection_id, username, display_name, avatar_url,
                    reply_deadline_at, metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (source_key, business_connection_id, external_chat_id)
                DO UPDATE SET
                    external_user_id = COALESCE(EXCLUDED.external_user_id, funnel_workspace_conversations.external_user_id),
                    username = COALESCE(EXCLUDED.username, funnel_workspace_conversations.username),
                    display_name = COALESCE(EXCLUDED.display_name, funnel_workspace_conversations.display_name),
                    avatar_url = COALESCE(EXCLUDED.avatar_url, funnel_workspace_conversations.avatar_url),
                    reply_deadline_at = CASE
                        WHEN EXCLUDED.reply_deadline_at IS NULL
                            THEN funnel_workspace_conversations.reply_deadline_at
                        WHEN funnel_workspace_conversations.reply_deadline_at IS NULL
                            THEN EXCLUDED.reply_deadline_at
                        ELSE GREATEST(
                            EXCLUDED.reply_deadline_at,
                            funnel_workspace_conversations.reply_deadline_at
                        )
                    END,
                    metadata = funnel_workspace_conversations.metadata || EXCLUDED.metadata,
                    updated_at = now()
                RETURNING *
                """,
                (
                    source,
                    chat_id,
                    user_id,
                    business_id,
                    _clean_optional(username, 200),
                    _clean_optional(display_name, 300),
                    _clean_optional(avatar_url, 1000),
                    deadline,
                    Jsonb(dict(metadata or {})),
                ),
            )
            return dict(cur.fetchone())


def _load_conversation_locked(cur: Any, conversation_id: Any) -> dict[str, Any]:
    item_id = _positive_int(conversation_id, "conversation_id")
    cur.execute(
        "SELECT * FROM funnel_workspace_conversations WHERE id = %s FOR UPDATE",
        (item_id,),
    )
    row = _record(cur.fetchone())
    if row is None:
        raise WorkspaceNotFoundError("Диалог не найден.", details={"conversation_id": item_id})
    return row


def _require_version(row: Mapping[str, Any], expected_version: Any) -> int:
    expected = _positive_int(expected_version, "expected_version")
    current = int(row["state_version"])
    if current != expected:
        raise WorkspaceConflictError(
            "Диалог уже изменился. Обновите его и повторите действие.",
            details={"expected_version": expected, "current_version": current},
        )
    return current


def get_conversation(
    conversation_id: Any,
    *,
    connect: ConnectFactory | None = None,
) -> dict[str, Any]:
    item_id = _positive_int(conversation_id, "conversation_id")
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.*, s.source_type, s.display_name AS source_name
                  FROM funnel_workspace_conversations c
                  JOIN funnel_workspace_sources s ON s.source_key = c.source_key
                 WHERE c.id = %s
                """,
                (item_id,),
            )
            row = _record(cur.fetchone())
            if row is None:
                raise WorkspaceNotFoundError(
                    "Диалог не найден.",
                    details={"conversation_id": item_id},
                )
            return row


def list_conversations(
    *,
    q: str = "",
    status: str = "",
    stage: str = "",
    source: str = "",
    limit: int = 100,
    offset: int = 0,
    connect: ConnectFactory | None = None,
) -> dict[str, Any]:
    clean_q = str(q or "").strip()[:200]
    clean_status = str(status or "").strip().lower()
    # Этап воронки — это код сделки в Битриксе, а не наш перечень: проверять его по
    # белому списку нельзя, иначе новый этап у владельца перестанет фильтроваться.
    clean_stage = str(stage or "").strip()[:200]
    clean_source = str(source or "").strip()[:100]
    if clean_status and clean_status not in VALID_STATUSES:
        raise WorkspaceValidationError("Неизвестный статус.", details={"status": clean_status})
    limit = min(250, max(1, int(limit or 100)))
    offset = max(0, int(offset or 0))

    clauses = ["TRUE"]
    params: list[Any] = []
    if clean_status:
        clauses.append("c.status = %s")
        params.append(clean_status)
    if clean_stage:
        clauses.append("c.stage_id = %s")
        params.append(clean_stage)
    if clean_source:
        clauses.append("c.source_key = %s")
        params.append(clean_source)
    if clean_q:
        # ``last_message_text`` is only the cached preview of the newest message, so a
        # search restricted to it silently hides conversations whose match is deeper in
        # the retained history.  EXISTS keeps one row per conversation, which a JOIN over
        # messages would break together with LIMIT/OFFSET paging.
        clauses.append(
            """(
                COALESCE(c.display_name, '') ILIKE %s
                OR COALESCE(c.username, '') ILIKE %s
                OR c.external_chat_id ILIKE %s
                OR COALESCE(c.last_message_text, '') ILIKE %s
                OR COALESCE(c.deal_id::text, '') ILIKE %s
                OR EXISTS (
                    SELECT 1
                      FROM funnel_workspace_messages sm
                     WHERE sm.conversation_id = c.id
                       AND sm.text ILIKE %s
                )
            )"""
        )
        pattern = f"%{clean_q}%"
        params.extend([pattern] * 6)
    params.extend([limit, offset])

    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT c.*, s.source_type, s.display_name AS source_name,
                       count(*) OVER () AS filtered_total
                  FROM funnel_workspace_conversations c
                  JOIN funnel_workspace_sources s ON s.source_key = c.source_key
                 WHERE {' AND '.join(clauses)}
                 ORDER BY
                       (c.status = 'new') DESC,
                       (c.unread_count > 0) DESC,
                       c.last_message_at DESC NULLS LAST,
                       c.id DESC
                 LIMIT %s OFFSET %s
                """,
                tuple(params),
            )
            rows = [dict(row) for row in cur.fetchall()]
    total = int(rows[0].pop("filtered_total")) if rows else 0
    for row in rows[1:]:
        row.pop("filtered_total", None)
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


def list_messages(
    conversation_id: Any,
    *,
    after_id: int = 0,
    before_id: int | None = None,
    limit: int = 200,
    connect: ConnectFactory | None = None,
) -> list[dict[str, Any]]:
    item_id = _positive_int(conversation_id, "conversation_id")
    after = max(0, int(after_id or 0))
    before = int(before_id) if before_id else None
    limit = min(500, max(1, int(limit or 200)))
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM funnel_workspace_conversations WHERE id = %s",
                (item_id,),
            )
            if cur.fetchone() is None:
                raise WorkspaceNotFoundError(
                    "Диалог не найден.",
                    details={"conversation_id": item_id},
                )
            if before:
                cur.execute(
                    """
                    SELECT *
                      FROM (
                            SELECT *
                              FROM funnel_workspace_messages
                             WHERE conversation_id = %s
                               AND id > %s
                               AND id < %s
                             ORDER BY id DESC
                             LIMIT %s
                      ) recent
                     ORDER BY id
                    """,
                    (item_id, after, before, limit),
                )
            elif after:
                cur.execute(
                    """
                    SELECT *
                      FROM funnel_workspace_messages
                     WHERE conversation_id = %s
                       AND id > %s
                     ORDER BY id
                     LIMIT %s
                    """,
                    (item_id, after, limit),
                )
            else:
                cur.execute(
                    """
                    SELECT *
                      FROM (
                            SELECT *
                              FROM funnel_workspace_messages
                             WHERE conversation_id = %s
                             ORDER BY id DESC
                             LIMIT %s
                      ) recent
                     ORDER BY id
                    """,
                    (item_id, limit),
                )
            return [dict(row) for row in cur.fetchall()]


def conversation_detail(
    conversation_id: Any,
    *,
    message_limit: int = 200,
    connect: ConnectFactory | None = None,
) -> dict[str, Any]:
    return {
        "conversation": get_conversation(conversation_id, connect=connect),
        "messages": list_messages(
            conversation_id,
            limit=message_limit,
            connect=connect,
        ),
    }


def _cancel_queued_ai(cur: Any, conversation_id: int, reason: str) -> None:
    cur.execute(
        """
        WITH cancelled AS (
            UPDATE funnel_workspace_outbox
               SET delivery_status = 'cancelled',
                   cancel_requested = true,
                   last_error = %s,
                   updated_at = now()
             WHERE conversation_id = %s
               AND author_type = 'agent'
               AND delivery_status = 'pending'
         RETURNING message_id
        )
        UPDATE funnel_workspace_messages
           SET delivery_status = 'cancelled',
               error_code = 'control_changed',
               error_detail = %s
         WHERE id IN (SELECT message_id FROM cancelled)
        """,
        (reason, conversation_id, reason),
    )
    cur.execute(
        """
        UPDATE funnel_workspace_outbox
           SET cancel_requested = true,
               last_error = COALESCE(last_error, %s),
               updated_at = now()
         WHERE conversation_id = %s
           AND author_type = 'agent'
           AND delivery_status IN ('leased', 'sending')
        """,
        (reason, conversation_id),
    )
    cur.execute(
        """
        UPDATE funnel_workspace_ai_jobs
           SET processing_status = CASE
                   WHEN processing_status = 'pending' THEN 'cancelled'
                   ELSE processing_status
               END,
               cancel_requested = true,
               last_error = COALESCE(last_error, %s),
               completed_at = CASE
                   WHEN processing_status = 'pending' THEN now()
                   ELSE completed_at
               END,
               updated_at = now()
         WHERE conversation_id = %s
           AND processing_status IN ('pending', 'leased')
        """,
        (reason, conversation_id),
    )


def _reject_if_agent_send_in_progress(cur: Any, conversation_id: int) -> None:
    """Reject a human handoff while an AI provider call is already in flight.

    The caller must hold the conversation row lock. ``begin_outbox_send`` takes
    the same lock before changing an outbox item to ``sending``, so either the
    handoff wins and cancels the lease, or the provider boundary wins and the
    operator gets a retryable conflict instead of a double reply.
    """

    cur.execute(
        """
        SELECT id
          FROM funnel_workspace_outbox
         WHERE conversation_id = %s
           AND author_type = 'agent'
           AND delivery_status = 'sending'
         ORDER BY id
         LIMIT 1
        """,
        (conversation_id,),
    )
    row = _record(cur.fetchone())
    if row is not None:
        raise WorkspaceConflictError(
            "Ответ ИИ уже передаётся в Telegram. Дождитесь результата и повторите действие.",
            details={
                "conversation_id": conversation_id,
                "outbox_id": int(row["id"]),
                "reason": "ai_send_in_progress",
            },
        )


def _reconcile_business_bot_echo_cursor(
    cur: Any,
    *,
    conversation_id: int,
    external_message_id: str,
    text: str,
    provider_update_id: int | None,
    occurred_at: datetime,
    metadata: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Attach a Telegram Business bot echo to its already-journaled outbox row.

    Telegram may publish the echo before the sender thread commits the Bot API
    response, or only after that thread timed out.  The conversation row is
    already locked by ``ingest_business_message``, so selecting and completing
    one matching outbox item here is atomic with processing the raw update.
    """

    cur.execute(
        """
        SELECT o.*, row_to_json(m) AS message
          FROM funnel_workspace_outbox o
          JOIN funnel_workspace_messages m ON m.id = o.message_id
         WHERE o.conversation_id = %s
           AND (
                o.provider_message_id = %s
                OR m.provider_message_id = %s
                OR (
                    o.provider_message_id IS NULL
                    AND m.provider_message_id IS NULL
                    AND o.delivery_status IN ('leased', 'sending', 'unknown')
                    AND o.text = %s
                )
           )
         ORDER BY
               (o.provider_message_id = %s OR m.provider_message_id = %s) DESC,
               CASE o.delivery_status
                   WHEN 'sending' THEN 0
                   WHEN 'unknown' THEN 1
                   WHEN 'leased' THEN 2
                   ELSE 3
               END,
               o.id
         FOR UPDATE OF o, m
         LIMIT 1
        """,
        (
            conversation_id,
            external_message_id,
            external_message_id,
            text,
            external_message_id,
            external_message_id,
        ),
    )
    candidate = _record(cur.fetchone())
    if candidate is None:
        return None
    message = dict(candidate.pop("message"))
    cur.execute(
        """
        UPDATE funnel_workspace_outbox
           SET delivery_status = 'sent',
               provider_message_id = %s,
               locked_at = NULL,
               locked_until = NULL,
               locked_by = NULL,
               last_error = NULL,
               sent_at = COALESCE(sent_at, %s),
               updated_at = now()
         WHERE id = %s
     RETURNING *
        """,
        (external_message_id, occurred_at, candidate["id"]),
    )
    outbox = dict(cur.fetchone())
    cur.execute(
        """
        UPDATE funnel_workspace_messages
           SET external_message_id = COALESCE(external_message_id, %s),
               provider_message_id = %s,
               provider_update_id = COALESCE(provider_update_id, %s),
               delivery_status = 'sent',
               error_code = NULL,
               error_detail = NULL,
               metadata = metadata || %s,
               sent_at = COALESCE(sent_at, %s)
         WHERE id = %s
     RETURNING *
        """,
        (
            external_message_id,
            external_message_id,
            provider_update_id,
            Jsonb(dict(metadata)),
            occurred_at,
            message["id"],
        ),
    )
    updated_message = dict(cur.fetchone())
    delivery_action = _enqueue_delivery_effect_action_cursor(cur, outbox)
    crm_action = _enqueue_crm_stage_action_cursor(cur, outbox)
    return {
        "outbox": outbox,
        "message": updated_message,
        "delivery_action": delivery_action,
        "crm_action": crm_action,
    }


def _insert_control_event(
    cur: Any,
    *,
    conversation_id: int,
    from_mode: str | None,
    to_mode: str,
    actor_type: str,
    actor_name: str | None,
    reason: str | None,
    from_version: int | None,
    to_version: int,
) -> None:
    cur.execute(
        """
        INSERT INTO funnel_workspace_control_events (
            conversation_id, from_mode, to_mode, actor_type, actor_name,
            reason, from_version, to_version
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            conversation_id,
            from_mode,
            to_mode,
            actor_type,
            actor_name,
            reason,
            from_version,
            to_version,
        ),
    )


def _latest_unanswered_client_message_id(cur: Any, conversation_id: int) -> int | None:
    cur.execute(
        """
        SELECT client.id
          FROM funnel_workspace_messages client
         WHERE client.conversation_id = %s
           AND client.author_type = 'client'
           AND NOT EXISTS (
                SELECT 1
                  FROM funnel_workspace_messages answer
                 WHERE answer.conversation_id = client.conversation_id
                   AND answer.id > client.id
                   AND answer.author_type IN ('agent', 'operator')
                   AND answer.delivery_status IN ('pending', 'sent', 'unknown')
                   AND NOT (
                        answer.author_type = 'agent'
                        AND EXISTS (
                            SELECT 1
                              FROM funnel_workspace_outbox cancelled_outbox
                             WHERE cancelled_outbox.message_id = answer.id
                               AND cancelled_outbox.cancel_requested = true
                        )
                   )
           )
         ORDER BY client.id DESC
         LIMIT 1
        """,
        (conversation_id,),
    )
    row = cur.fetchone()
    return int(row["id"]) if row else None


def transition_control(
    conversation_id: Any,
    *,
    mode: str,
    expected_version: Any,
    actor_type: str = "operator",
    actor_name: str | None = None,
    reason: str | None = None,
    lease_seconds: int | None = None,
    now: datetime | None = None,
    connect: ConnectFactory | None = None,
) -> dict[str, Any]:
    clean_mode = str(mode or "").strip().lower()
    clean_actor = str(actor_type or "").strip().lower()
    if clean_mode not in VALID_CONTROL_MODES:
        raise WorkspaceValidationError("Неизвестный режим управления.", details={"mode": clean_mode})
    if clean_actor not in {"agent", "operator", "system"}:
        raise WorkspaceValidationError("Неизвестный тип автора.", details={"actor_type": clean_actor})
    timestamp = _now(now)
    lease = human_lease_seconds() if lease_seconds is None else max(10, min(86_400, int(lease_seconds)))
    resume_at = timestamp + timedelta(seconds=lease) if clean_mode == "human" else None

    with _connection(connect) as conn:
        with conn.cursor() as cur:
            row = _load_conversation_locked(cur, conversation_id)
            current_version = _require_version(row, expected_version)
            if clean_mode == "ai" and row["status"] not in ACTIVE_STATUSES:
                raise WorkspaceControlError(
                    "ИИ нельзя включить в закрытом или просроченном диалоге.",
                    details={"status": row["status"]},
                )
            if clean_mode == "ai" and row.get("reply_deadline_at"):
                if _now(row["reply_deadline_at"]) <= timestamp:
                    raise WorkspaceReplyWindowExpired(
                        "Окно ответа Telegram истекло. Дождитесь нового сообщения клиента.",
                        details={"reply_deadline_at": row["reply_deadline_at"]},
                    )
            if clean_mode in {"human", "paused"}:
                _reject_if_agent_send_in_progress(cur, int(row["id"]))
                _cancel_queued_ai(cur, int(row["id"]), reason or "Управление передано человеку.")
            next_version = current_version + 1
            assigned_to = _clean_optional(actor_name, 200) if clean_mode == "human" else None
            cur.execute(
                """
                UPDATE funnel_workspace_conversations
                   SET control_mode = %s,
                       resume_at = %s,
                       assigned_to = %s,
                       state_version = %s,
                       updated_at = %s
                 WHERE id = %s
             RETURNING *
                """,
                (
                    clean_mode,
                    resume_at,
                    assigned_to,
                    next_version,
                    timestamp,
                    row["id"],
                ),
            )
            updated = dict(cur.fetchone())
            _insert_control_event(
                cur,
                conversation_id=int(row["id"]),
                from_mode=row["control_mode"],
                to_mode=clean_mode,
                actor_type=clean_actor,
                actor_name=_clean_optional(actor_name, 200),
                reason=_clean_optional(reason, 1000),
                from_version=current_version,
                to_version=next_version,
            )
            if clean_mode == "ai":
                trigger_message_id = _latest_unanswered_client_message_id(
                    cur,
                    int(row["id"]),
                )
                if trigger_message_id is not None:
                    _schedule_ai_job_cursor(
                        cur,
                        conversation_id=int(row["id"]),
                        trigger_message_id=trigger_message_id,
                        expected_version=next_version,
                        available_at=timestamp
                        + timedelta(milliseconds=ai_debounce_milliseconds()),
                    )
            return updated


def mark_waiting_human(
    conversation_id: Any,
    *,
    expected_version: Any,
    reason: str,
    assigned_to: str | None = None,
    now: datetime | None = None,
    connect: ConnectFactory | None = None,
) -> dict[str, Any]:
    timestamp = _now(now)
    clean_reason = _required_text(reason, "reason", 1000)
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            row = _load_conversation_locked(cur, conversation_id)
            current_version = _require_version(row, expected_version)
            _cancel_queued_ai(cur, int(row["id"]), clean_reason)
            next_version = current_version + 1
            cur.execute(
                """
                UPDATE funnel_workspace_conversations
                   SET status = 'waiting',
                       control_mode = 'paused',
                       resume_at = NULL,
                       assigned_to = %s,
                       state_version = %s,
                       updated_at = %s
                 WHERE id = %s
             RETURNING *
                """,
                (_clean_optional(assigned_to, 200), next_version, timestamp, row["id"]),
            )
            updated = dict(cur.fetchone())
            _insert_control_event(
                cur,
                conversation_id=int(row["id"]),
                from_mode=row["control_mode"],
                to_mode="paused",
                actor_type="agent",
                actor_name="ИИ-агент",
                reason=clean_reason,
                from_version=current_version,
                to_version=next_version,
            )
            return updated


def mark_read(
    conversation_id: Any,
    *,
    through_message_id: Any,
    connect: ConnectFactory | None = None,
) -> dict[str, Any]:
    item_id = _positive_int(conversation_id, "conversation_id")
    through_id = _positive_int(through_message_id, "through_message_id")
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            row = _load_conversation_locked(cur, item_id)
            cur.execute(
                """
                SELECT COALESCE(max(id), 0) AS max_message_id
                  FROM funnel_workspace_messages
                 WHERE conversation_id = %s
                """,
                (item_id,),
            )
            maximum = int(dict(cur.fetchone()).get("max_message_id") or 0)
            read_cursor = max(
                int(row.get("last_read_message_id") or 0),
                min(through_id, maximum),
            )
            cur.execute(
                """
                UPDATE funnel_workspace_conversations
                   SET last_read_message_id = %s,
                       unread_count = (
                           SELECT count(*)
                             FROM funnel_workspace_messages
                            WHERE conversation_id = %s
                              AND author_type = 'client'
                              AND id > %s
                       ),
                       updated_at = now()
                 WHERE id = %s
             RETURNING *
                """,
                (read_cursor, item_id, read_cursor, item_id),
            )
            return dict(cur.fetchone())


def update_conversation_status(
    conversation_id: Any,
    *,
    status: str,
    expected_version: Any,
    actor_name: str | None = None,
    now: datetime | None = None,
    connect: ConnectFactory | None = None,
) -> dict[str, Any]:
    clean_status = str(status or "").strip().lower()
    if clean_status not in VALID_STATUSES:
        raise WorkspaceValidationError("Неизвестный статус.", details={"status": clean_status})
    timestamp = _now(now)
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            row = _load_conversation_locked(cur, conversation_id)
            current_version = _require_version(row, expected_version)
            _reject_if_agent_send_in_progress(cur, int(row["id"]))
            next_version = current_version + 1
            next_mode = row["control_mode"]
            resume_at = row.get("resume_at")
            assigned_to = row.get("assigned_to")
            if clean_status in {"closed", "spam", "expired"}:
                next_mode = "paused"
                resume_at = None
                assigned_to = None
                _cancel_queued_ai(cur, int(row["id"]), f"Статус изменён на {clean_status}.")
            cur.execute(
                """
                UPDATE funnel_workspace_conversations
                   SET status = %s,
                       control_mode = %s,
                       resume_at = %s,
                       assigned_to = %s,
                       closed_at = CASE WHEN %s = 'closed' THEN %s ELSE NULL END,
                       state_version = %s,
                       updated_at = %s
                 WHERE id = %s
             RETURNING *
                """,
                (
                    clean_status,
                    next_mode,
                    resume_at,
                    assigned_to,
                    clean_status,
                    timestamp,
                    next_version,
                    timestamp,
                    row["id"],
                ),
            )
            updated = dict(cur.fetchone())
            if next_mode != row["control_mode"]:
                _insert_control_event(
                    cur,
                    conversation_id=int(row["id"]),
                    from_mode=row["control_mode"],
                    to_mode=next_mode,
                    actor_type="operator",
                    actor_name=_clean_optional(actor_name, 200),
                    reason=f"Статус изменён на {clean_status}.",
                    from_version=current_version,
                    to_version=next_version,
                )
            return updated


def update_crm_link(
    conversation_id: Any,
    *,
    deal_id: int | None = None,
    funnel_id: int | None = None,
    stage_id: str | None = None,
    expected_version: Any | None = None,
    connect: ConnectFactory | None = None,
) -> dict[str, Any]:
    # CRM linkage is display/context metadata.  It must not invalidate an AI job
    # that was scheduled from the same inbound message.  The locked row is also
    # the cross-worker winner election: once deal_id is set, a late CRM create
    # may be reported as an orphan but can never replace the committed link.
    del expected_version
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            row = _load_conversation_locked(cur, conversation_id)
            requested_deal_id = int(deal_id) if deal_id is not None else None
            current_deal_id = (
                int(row["deal_id"])
                if row.get("deal_id") not in (None, "")
                else None
            )
            if (
                current_deal_id is not None
                and requested_deal_id is not None
                and current_deal_id != requested_deal_id
            ):
                return row
            cur.execute(
                """
                UPDATE funnel_workspace_conversations
                   SET deal_id = COALESCE(deal_id, %s),
                       funnel_id = COALESCE(%s, funnel_id),
                       stage_id = COALESCE(%s, stage_id),
                       updated_at = now()
                 WHERE id = %s
             RETURNING *
                """,
                (
                    requested_deal_id,
                    int(funnel_id) if funnel_id is not None else None,
                    _clean_optional(stage_id, 200),
                    row["id"],
                ),
            )
            return dict(cur.fetchone())


def _schedule_ai_job_cursor(
    cur: Any,
    *,
    conversation_id: int,
    trigger_message_id: int | None,
    expected_version: int,
    available_at: datetime,
) -> dict[str, Any]:
    cur.execute(
        """
        UPDATE funnel_workspace_ai_jobs
           SET cancel_requested = true,
               last_error = COALESCE(last_error, 'A newer scheduled turn superseded this job.'),
               updated_at = now()
         WHERE conversation_id = %s
           AND processing_status = 'leased'
        """,
        (conversation_id,),
    )
    cur.execute(
        """
        INSERT INTO funnel_workspace_ai_jobs (
            conversation_id, trigger_message_id, expected_version, available_at
        )
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (conversation_id) WHERE processing_status = 'pending'
        DO UPDATE SET
            trigger_message_id = EXCLUDED.trigger_message_id,
            expected_version = EXCLUDED.expected_version,
            available_at = EXCLUDED.available_at,
            attempts = 0,
            cancel_requested = false,
            last_error = NULL,
            updated_at = now()
        RETURNING *
        """,
        (conversation_id, trigger_message_id, expected_version, available_at),
    )
    return dict(cur.fetchone())


def schedule_ai_job(
    conversation_id: Any,
    *,
    trigger_message_id: int | None,
    expected_version: Any,
    debounce_milliseconds: int | None = None,
    now: datetime | None = None,
    connect: ConnectFactory | None = None,
) -> dict[str, Any]:
    timestamp = _now(now)
    debounce = (
        ai_debounce_milliseconds()
        if debounce_milliseconds is None
        else min(10_000, max(0, int(debounce_milliseconds)))
    )
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            row = _load_conversation_locked(cur, conversation_id)
            current_version = _require_version(row, expected_version)
            if row["control_mode"] != "ai" or row["status"] not in ACTIVE_STATUSES:
                raise WorkspaceControlError(
                    "ИИ сейчас не управляет этим диалогом.",
                    details={
                        "control_mode": row["control_mode"],
                        "status": row["status"],
                    },
                )
            deadline = row.get("reply_deadline_at")
            if deadline and _now(deadline) <= timestamp:
                raise WorkspaceReplyWindowExpired(
                    "Окно ответа Telegram истекло. Дождитесь нового сообщения клиента.",
                    details={"reply_deadline_at": deadline},
                )
            return _schedule_ai_job_cursor(
                cur,
                conversation_id=int(row["id"]),
                trigger_message_id=(
                    int(trigger_message_id) if trigger_message_id is not None else None
                ),
                expected_version=current_version,
                available_at=timestamp + timedelta(milliseconds=debounce),
            )


def ingest_business_message(
    *,
    external_chat_id: Any,
    external_message_id: Any,
    text: Any,
    author_type: str,
    source_key: str = DEFAULT_SOURCE_KEY,
    business_connection_id: Any = "",
    external_user_id: Any = None,
    username: Any = None,
    display_name: Any = None,
    author_name: Any = None,
    provider_update_id: int | None = None,
    occurred_at: datetime | None = None,
    metadata: Mapping[str, Any] | None = None,
    operator_lease_seconds: int | None = None,
    schedule_ai: bool = False,
    is_edit: bool = False,
    connect: ConnectFactory | None = None,
) -> dict[str, Any]:
    """Journal one Telegram Business message and update its conversation atomically.

    `author_type=client` is an inbound private message.  A message sent by the
    business account itself must be classified as `operator`; recording it takes
    the same human lease as a reply from the web UI.  Replays are harmless because
    `(conversation_id, external_message_id)` is unique.
    """

    source = _required_text(source_key, "source_key", 100)
    chat_id = _required_text(external_chat_id, "external_chat_id", 200)
    external_id = _required_text(external_message_id, "external_message_id", 300)
    clean_author = str(author_type or "").strip().lower()
    if clean_author not in VALID_AUTHOR_TYPES:
        raise WorkspaceValidationError(
            "Неизвестный тип автора сообщения.",
            details={"author_type": clean_author},
        )
    clean_text = str(text or "")
    if len(clean_text) > 100_000:
        raise WorkspaceValidationError(
            "Сообщение слишком длинное для журнала.",
            details={"max_length": 100_000},
        )
    timestamp = _now(occurred_at)
    business_id = str(business_connection_id or "").strip()[:300]
    user_id = int(external_user_id) if external_user_id not in (None, "") else None
    clean_metadata = dict(metadata or {})
    deadline = (
        timestamp + timedelta(hours=reply_window_hours())
        if clean_author == "client" and not is_edit
        else None
    )
    direction = "inbound" if clean_author == "client" else (
        "system" if clean_author == "system" else "outbound"
    )

    with _connection(connect) as conn:
        with conn.cursor() as cur:
            _ensure_source_cursor(cur, source)
            cur.execute(
                """
                INSERT INTO funnel_workspace_conversations (
                    source_key, external_chat_id, external_user_id,
                    business_connection_id, username, display_name,
                    reply_deadline_at, metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (source_key, business_connection_id, external_chat_id)
                DO UPDATE SET
                    external_user_id = COALESCE(EXCLUDED.external_user_id, funnel_workspace_conversations.external_user_id),
                    username = COALESCE(EXCLUDED.username, funnel_workspace_conversations.username),
                    display_name = COALESCE(EXCLUDED.display_name, funnel_workspace_conversations.display_name),
                    reply_deadline_at = CASE
                        WHEN %s <> 'client' THEN funnel_workspace_conversations.reply_deadline_at
                        WHEN funnel_workspace_conversations.reply_deadline_at IS NULL
                            THEN EXCLUDED.reply_deadline_at
                        ELSE GREATEST(
                            EXCLUDED.reply_deadline_at,
                            funnel_workspace_conversations.reply_deadline_at
                        )
                    END,
                    metadata = funnel_workspace_conversations.metadata || EXCLUDED.metadata,
                    updated_at = now()
                RETURNING *
                """,
                (
                    source,
                    chat_id,
                    user_id,
                    business_id,
                    _clean_optional(username, 200),
                    _clean_optional(display_name, 300),
                    deadline,
                    Jsonb(clean_metadata),
                    clean_author,
                ),
            )
            conversation = dict(cur.fetchone())
            if clean_metadata.get("sent_via_business_bot") and not is_edit:
                reconciled = _reconcile_business_bot_echo_cursor(
                    cur,
                    conversation_id=int(conversation["id"]),
                    external_message_id=external_id,
                    text=clean_text,
                    provider_update_id=provider_update_id,
                    occurred_at=timestamp,
                    metadata=clean_metadata,
                )
                if reconciled is not None:
                    return {
                        "conversation": conversation,
                        "message": reconciled["message"],
                        "outbox": reconciled["outbox"],
                        "delivery_action": reconciled.get("delivery_action"),
                        "crm_action": reconciled.get("crm_action"),
                        "duplicate": True,
                        "reconciled_echo": True,
                        "ai_job": None,
                    }
            cur.execute(
                """
                SELECT *
                  FROM funnel_workspace_messages
                 WHERE conversation_id = %s
                   AND (
                        external_message_id = %s
                        OR (
                            provider_message_id = %s
                            AND author_type IN ('agent', 'operator')
                        )
                   )
                 ORDER BY (external_message_id = %s) DESC, id
                 LIMIT 1
                """,
                (
                    conversation["id"],
                    external_id,
                    external_id,
                    external_id,
                ),
            )
            existing = _record(cur.fetchone())
            if existing is not None:
                if existing.get("external_message_id") is None:
                    cur.execute(
                        """
                        UPDATE funnel_workspace_messages
                           SET external_message_id = %s,
                               occurred_at = LEAST(occurred_at, %s)
                         WHERE id = %s
                     RETURNING *
                        """,
                        (external_id, timestamp, existing["id"]),
                    )
                    existing = dict(cur.fetchone())
                if is_edit:
                    cur.execute(
                        """
                        UPDATE funnel_workspace_messages
                           SET text = %s,
                               provider_update_id = COALESCE(%s, provider_update_id),
                               metadata = metadata || %s
                         WHERE id = %s
                     RETURNING *
                        """,
                        (
                            clean_text,
                            provider_update_id,
                            Jsonb(clean_metadata),
                            existing["id"],
                        ),
                    )
                    existing = dict(cur.fetchone())
                    ai_job = None
                    if existing["author_type"] == "client":
                        _cancel_queued_ai(
                            cur,
                            int(conversation["id"]),
                            "Клиент изменил сообщение до завершения ответа ИИ.",
                        )
                        current_version = int(conversation["state_version"])
                        next_version = current_version + 1
                        cur.execute(
                            """
                            UPDATE funnel_workspace_conversations
                               SET state_version = %s,
                                   last_message_text = CASE
                                       WHEN last_message_id = %s THEN %s
                                       ELSE last_message_text
                                   END,
                                   updated_at = now()
                             WHERE id = %s
                         RETURNING *
                            """,
                            (
                                next_version,
                                existing["id"],
                                clean_text[:1000],
                                conversation["id"],
                            ),
                        )
                        conversation = dict(cur.fetchone())
                        if (
                            schedule_ai
                            and conversation["control_mode"] == "ai"
                            and conversation["status"] in ACTIVE_STATUSES
                        ):
                            trigger_message_id = _latest_unanswered_client_message_id(
                                cur,
                                int(conversation["id"]),
                            )
                            if trigger_message_id is not None:
                                ai_job = _schedule_ai_job_cursor(
                                    cur,
                                    conversation_id=int(conversation["id"]),
                                    trigger_message_id=trigger_message_id,
                                    expected_version=int(conversation["state_version"]),
                                    available_at=timestamp
                                    + timedelta(milliseconds=ai_debounce_milliseconds()),
                                )
                    crm_ensure_action = (
                        _enqueue_ensure_deal_action_cursor(
                            cur,
                            conversation_id=conversation["id"],
                            message_id=existing["id"],
                        )
                        if existing["author_type"] == "client"
                        and not conversation.get("deal_id")
                        else None
                    )
                    return {
                        "conversation": conversation,
                        "message": existing,
                        "duplicate": False,
                        "edited": True,
                        "ai_job": ai_job,
                        "crm_ensure_action": crm_ensure_action,
                    }
                crm_ensure_action = (
                    _enqueue_ensure_deal_action_cursor(
                        cur,
                        conversation_id=conversation["id"],
                        message_id=existing["id"],
                    )
                    if existing["author_type"] == "client"
                    and not conversation.get("deal_id")
                    else None
                )
                return {
                    "conversation": conversation,
                    "message": existing,
                    "duplicate": True,
                    "ai_job": None,
                    "crm_ensure_action": crm_ensure_action,
                }

            cur.execute(
                """
                INSERT INTO funnel_workspace_messages (
                    conversation_id, external_message_id, provider_update_id,
                    provider_message_id, author_type, author_name, direction,
                    text, delivery_status, metadata, occurred_at, sent_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'sent', %s, %s, %s)
                RETURNING *
                """,
                (
                    conversation["id"],
                    external_id,
                    provider_update_id,
                    external_id,
                    clean_author,
                    _clean_optional(author_name, 200),
                    direction,
                    clean_text,
                    Jsonb(clean_metadata),
                    timestamp,
                    timestamp if direction == "outbound" else None,
                ),
            )
            message = dict(cur.fetchone())

            old_mode = str(conversation["control_mode"])
            new_mode = old_mode
            resume_at = conversation.get("resume_at")
            assigned_to = conversation.get("assigned_to")
            unread_increment = 1 if clean_author == "client" else 0
            next_status = str(conversation["status"])
            if clean_author == "client" and next_status in {"closed", "expired"}:
                next_status = "open"
            if clean_author == "client":
                _cancel_queued_ai(
                    cur,
                    int(conversation["id"]),
                    "Новое сообщение клиента отменило незавершённый ответ ИИ.",
                )
            if clean_author == "operator":
                new_mode = "human"
                lease = (
                    human_lease_seconds()
                    if operator_lease_seconds is None
                    else max(10, min(86_400, int(operator_lease_seconds)))
                )
                resume_at = timestamp + timedelta(seconds=lease)
                assigned_to = _clean_optional(author_name, 200) or "Оператор"
                if next_status in {"closed", "expired", "waiting"}:
                    next_status = "open"
                _cancel_queued_ai(
                    cur,
                    int(conversation["id"]),
                    "Оператор ответил напрямую в Telegram.",
                )

            current_version = int(conversation["state_version"])
            next_version = current_version + 1
            cur.execute(
                """
                UPDATE funnel_workspace_conversations
                   SET status = %s,
                       control_mode = %s,
                       resume_at = %s,
                       assigned_to = %s,
                       unread_count = unread_count + %s,
                       state_version = %s,
                       reply_deadline_at = CASE
                           WHEN %s <> 'client' THEN reply_deadline_at
                           WHEN reply_deadline_at IS NULL THEN %s
                           ELSE GREATEST(reply_deadline_at, %s)
                       END,
                       last_message_id = %s,
                       last_message_at = %s,
                       last_message_text = %s,
                       last_author_type = %s,
                       closed_at = CASE WHEN %s IN ('closed', 'expired') THEN closed_at ELSE NULL END,
                       updated_at = %s
                 WHERE id = %s
             RETURNING *
                """,
                (
                    next_status,
                    new_mode,
                    resume_at,
                    assigned_to,
                    unread_increment,
                    next_version,
                    clean_author,
                    deadline,
                    deadline,
                    message["id"],
                    timestamp,
                    clean_text[:1000],
                    clean_author,
                    next_status,
                    timestamp,
                    conversation["id"],
                ),
            )
            updated = dict(cur.fetchone())
            if clean_author == "operator":
                _insert_control_event(
                    cur,
                    conversation_id=int(conversation["id"]),
                    from_mode=old_mode,
                    to_mode="human",
                    actor_type="operator",
                    actor_name=_clean_optional(author_name, 200),
                    reason="Ответ отправлен из Telegram.",
                    from_version=current_version,
                    to_version=next_version,
                )
            ai_job = None
            if (
                clean_author == "client"
                and schedule_ai
                and updated["control_mode"] == "ai"
                and updated["status"] in ACTIVE_STATUSES
            ):
                ai_job = _schedule_ai_job_cursor(
                    cur,
                    conversation_id=int(updated["id"]),
                    trigger_message_id=int(message["id"]),
                    expected_version=int(updated["state_version"]),
                    available_at=timestamp
                    + timedelta(milliseconds=ai_debounce_milliseconds()),
                )
            crm_ensure_action = (
                _enqueue_ensure_deal_action_cursor(
                    cur,
                    conversation_id=updated["id"],
                    message_id=message["id"],
                )
                if clean_author == "client" and not updated.get("deal_id")
                else None
            )
            return {
                "conversation": updated,
                "message": message,
                "duplicate": False,
                "ai_job": ai_job,
                "crm_ensure_action": crm_ensure_action,
            }


def tombstone_business_messages(
    *,
    external_chat_id: Any,
    external_message_ids: list[Any] | tuple[Any, ...],
    business_connection_id: Any,
    source_key: str = DEFAULT_SOURCE_KEY,
    provider_update_id: int | None = None,
    occurred_at: datetime | None = None,
    connect: ConnectFactory | None = None,
) -> dict[str, Any]:
    """Replace deleted Telegram messages with tombstones and invalidate stale AI work."""

    source = _required_text(source_key, "source_key", 100)
    chat_id = _required_text(external_chat_id, "external_chat_id", 200)
    business_id = _required_text(
        business_connection_id,
        "business_connection_id",
        300,
    )
    message_ids = list(
        dict.fromkeys(
            _required_text(value, "external_message_id", 300)
            for value in list(external_message_ids or [])[:1000]
        )
    )
    if not message_ids:
        return {
            "conversation": None,
            "messages": [],
            "message_id": None,
        }
    timestamp = _now(occurred_at)
    placeholders = ", ".join(["%s"] * len(message_ids))
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                  FROM funnel_workspace_conversations
                 WHERE source_key = %s
                   AND business_connection_id = %s
                   AND external_chat_id = %s
                 FOR UPDATE
                """,
                (source, business_id, chat_id),
            )
            conversation = _record(cur.fetchone())
            if conversation is None:
                return {
                    "conversation": None,
                    "messages": [],
                    "message_id": None,
                }
            cur.execute(
                f"""
                SELECT *
                  FROM funnel_workspace_messages
                 WHERE conversation_id = %s
                   AND external_message_id IN ({placeholders})
                 FOR UPDATE
                """,
                (conversation["id"], *message_ids),
            )
            found = [dict(row) for row in cur.fetchall()]
            if not found:
                return {
                    "conversation": conversation,
                    "messages": [],
                    "message_id": None,
                }
            found_ids = [int(row["id"]) for row in found]
            row_placeholders = ", ".join(["%s"] * len(found_ids))
            tombstone_metadata = {
                "telegram_deleted": True,
                "telegram_deleted_at": timestamp.isoformat(),
            }
            if provider_update_id is not None:
                tombstone_metadata["telegram_delete_update_id"] = provider_update_id
            cur.execute(
                f"""
                UPDATE funnel_workspace_messages
                   SET text = '[Сообщение удалено]',
                       metadata = metadata || %s
                 WHERE id IN ({row_placeholders})
             RETURNING *
                """,
                (Jsonb(tombstone_metadata), *found_ids),
            )
            messages = [dict(row) for row in cur.fetchall()]
            if any(row.get("author_type") == "client" for row in found):
                _cancel_queued_ai(
                    cur,
                    int(conversation["id"]),
                    "Клиент удалил сообщение до завершения ответа ИИ.",
                )
                next_version = int(conversation["state_version"]) + 1
                cur.execute(
                    f"""
                    UPDATE funnel_workspace_conversations
                       SET state_version = %s,
                           last_message_text = CASE
                               WHEN last_message_id IN ({row_placeholders})
                                   THEN '[Сообщение удалено]'
                               ELSE last_message_text
                           END,
                           updated_at = %s
                     WHERE id = %s
                 RETURNING *
                    """,
                    (
                        next_version,
                        *found_ids,
                        timestamp,
                        conversation["id"],
                    ),
                )
                conversation = dict(cur.fetchone())
            return {
                "conversation": conversation,
                "messages": messages,
                "message_id": max(found_ids),
            }


def ai_turn_guard(
    conversation_id: Any,
    *,
    expected_version: Any | None = None,
    now: datetime | None = None,
    connect: ConnectFactory | None = None,
) -> dict[str, Any]:
    """Return a non-throwing snapshot used before starting an expensive AI turn."""

    timestamp = _now(now)
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            row = _load_conversation_locked(cur, conversation_id)
            current_version = int(row["state_version"])
            if expected_version is not None and int(expected_version) != current_version:
                return {
                    "allowed": False,
                    "reason": "stale_version",
                    "version": current_version,
                    "conversation": row,
                }
            if row["control_mode"] != "ai":
                return {
                    "allowed": False,
                    "reason": f"control_{row['control_mode']}",
                    "version": current_version,
                    "conversation": row,
                }
            if row["status"] not in ACTIVE_STATUSES:
                return {
                    "allowed": False,
                    "reason": f"status_{row['status']}",
                    "version": current_version,
                    "conversation": row,
                }
            deadline = row.get("reply_deadline_at")
            if deadline and _now(deadline) <= timestamp:
                return {
                    "allowed": False,
                    "reason": "reply_window_expired",
                    "version": current_version,
                    "conversation": row,
                }
            return {
                "allowed": True,
                "reason": None,
                "version": current_version,
                "conversation": row,
            }


def _find_idempotent_outgoing(
    cur: Any,
    idempotency_key: str,
) -> dict[str, Any] | None:
    cur.execute(
        """
        SELECT o.*, row_to_json(m) AS message, row_to_json(c) AS conversation
          FROM funnel_workspace_outbox o
          JOIN funnel_workspace_messages m ON m.id = o.message_id
          JOIN funnel_workspace_conversations c ON c.id = o.conversation_id
         WHERE o.idempotency_key = %s
        """,
        (idempotency_key,),
    )
    row = _record(cur.fetchone())
    if row is None:
        return None
    message = row.pop("message")
    conversation = row.pop("conversation")
    return {
        "outbox": row,
        "message": dict(message),
        "conversation": dict(conversation),
        "duplicate": True,
    }


def _enqueue_outgoing(
    conversation_id: Any,
    *,
    text: Any,
    expected_version: Any,
    author_type: str,
    author_name: str | None,
    idempotency_key: str,
    metadata: Mapping[str, Any] | None,
    operator_lease_seconds: int | None,
    now: datetime | None,
    connect: ConnectFactory | None,
) -> dict[str, Any]:
    clean_text = _required_text(text, "text", MAX_MESSAGE_LENGTH)
    clean_key = _required_text(idempotency_key, "idempotency_key", 300)
    clean_author = str(author_type).lower()
    if clean_author not in {"agent", "operator"}:
        raise WorkspaceValidationError("Исходящее сообщение может отправить только ИИ или оператор.")
    timestamp = _now(now)

    with _connection(connect) as conn:
        with conn.cursor() as cur:
            # The outbox key is globally unique, not just per conversation.
            # Serialize the rare same-key/different-dialog race before inserts.
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (clean_key,),
            )
            row = _load_conversation_locked(cur, conversation_id)
            duplicate = _find_idempotent_outgoing(cur, clean_key)
            if duplicate is not None:
                existing_outbox = duplicate["outbox"]
                if (
                    int(existing_outbox["conversation_id"]) != int(row["id"])
                    or str(existing_outbox.get("text") or "") != clean_text
                    or str(existing_outbox.get("author_type") or "") != clean_author
                ):
                    raise WorkspaceConflictError(
                        "Ключ идемпотентности уже использован для другого сообщения.",
                        details={"idempotency_key": clean_key},
                    )
                return duplicate
            current_version = _require_version(row, expected_version)
            if row["status"] not in ACTIVE_STATUSES:
                raise WorkspaceControlError(
                    "Нельзя отвечать в закрытом диалоге.",
                    details={"status": row["status"]},
                )
            deadline = row.get("reply_deadline_at")
            if deadline and _now(deadline) <= timestamp:
                raise WorkspaceReplyWindowExpired(
                    "Окно ответа Telegram истекло. Дождитесь нового сообщения клиента.",
                    details={"reply_deadline_at": deadline},
                )
            if clean_author == "agent" and row["control_mode"] != "ai":
                raise WorkspaceControlError(
                    "ИИ больше не управляет этим диалогом.",
                    details={"control_mode": row["control_mode"]},
                )

            next_mode = str(row["control_mode"])
            next_resume_at = row.get("resume_at")
            next_assignee = row.get("assigned_to")
            next_status = str(row["status"])
            if clean_author == "operator":
                _reject_if_agent_send_in_progress(cur, int(row["id"]))
                lease = (
                    human_lease_seconds()
                    if operator_lease_seconds is None
                    else max(10, min(86_400, int(operator_lease_seconds)))
                )
                next_mode = "human"
                next_resume_at = timestamp + timedelta(seconds=lease)
                next_assignee = _clean_optional(author_name, 200) or "Оператор"
                if next_status in {"new", "waiting"}:
                    next_status = "open"
                _cancel_queued_ai(
                    cur,
                    int(row["id"]),
                    "Оператор забрал диалог и отправляет ответ.",
                )

            cur.execute(
                """
                INSERT INTO funnel_workspace_messages (
                    conversation_id, idempotency_key, author_type, author_name,
                    direction, text, delivery_status, metadata, occurred_at
                )
                VALUES (%s, %s, %s, %s, 'outbound', %s, 'pending', %s, %s)
                RETURNING *
                """,
                (
                    row["id"],
                    clean_key,
                    clean_author,
                    _clean_optional(author_name, 200),
                    clean_text,
                    Jsonb(dict(metadata or {})),
                    timestamp,
                ),
            )
            message = dict(cur.fetchone())
            next_version = current_version + 1
            cur.execute(
                """
                UPDATE funnel_workspace_conversations
                   SET status = %s,
                       control_mode = %s,
                       resume_at = %s,
                       assigned_to = %s,
                       state_version = %s,
                       last_message_id = %s,
                       last_message_at = %s,
                       last_message_text = %s,
                       last_author_type = %s,
                       updated_at = %s
                 WHERE id = %s
             RETURNING *
                """,
                (
                    next_status,
                    next_mode,
                    next_resume_at,
                    next_assignee,
                    next_version,
                    message["id"],
                    timestamp,
                    clean_text[:1000],
                    clean_author,
                    timestamp,
                    row["id"],
                ),
            )
            updated = dict(cur.fetchone())
            cur.execute(
                """
                INSERT INTO funnel_workspace_outbox (
                    conversation_id, message_id, source_key, external_chat_id,
                    business_connection_id, author_type, text, payload,
                    idempotency_key, conversation_version
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    row["id"],
                    message["id"],
                    row["source_key"],
                    row["external_chat_id"],
                    row["business_connection_id"],
                    clean_author,
                    clean_text,
                    Jsonb(dict(metadata or {})),
                    clean_key,
                    next_version,
                ),
            )
            outbox = dict(cur.fetchone())
            if clean_author == "operator":
                _insert_control_event(
                    cur,
                    conversation_id=int(row["id"]),
                    from_mode=row["control_mode"],
                    to_mode="human",
                    actor_type="operator",
                    actor_name=_clean_optional(author_name, 200),
                    reason="Оператор отправил ответ из рабочего окна.",
                    from_version=current_version,
                    to_version=next_version,
                )
            return {
                "conversation": updated,
                "message": message,
                "outbox": outbox,
                "duplicate": False,
            }


def enqueue_outgoing_operator(
    conversation_id: Any,
    *,
    text: Any,
    expected_version: Any,
    operator_name: str,
    idempotency_key: str,
    metadata: Mapping[str, Any] | None = None,
    lease_seconds: int | None = None,
    now: datetime | None = None,
    connect: ConnectFactory | None = None,
) -> dict[str, Any]:
    return _enqueue_outgoing(
        conversation_id,
        text=text,
        expected_version=expected_version,
        author_type="operator",
        author_name=operator_name,
        idempotency_key=idempotency_key,
        metadata=metadata,
        operator_lease_seconds=lease_seconds,
        now=now,
        connect=connect,
    )


def enqueue_outgoing_agent(
    conversation_id: Any,
    *,
    text: Any,
    expected_version: Any,
    idempotency_key: str,
    agent_name: str = "ИИ-агент",
    metadata: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    connect: ConnectFactory | None = None,
) -> dict[str, Any]:
    return _enqueue_outgoing(
        conversation_id,
        text=text,
        expected_version=expected_version,
        author_type="agent",
        author_name=agent_name,
        idempotency_key=idempotency_key,
        metadata=metadata,
        operator_lease_seconds=None,
        now=now,
        connect=connect,
    )


def recover_ai_jobs(
    *,
    now: datetime | None = None,
    connect: ConnectFactory | None = None,
) -> int:
    """AI generation is local and has no visible side effect, so an expired lease is retryable."""

    timestamp = _now(now)
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE funnel_workspace_ai_jobs
                   SET processing_status = CASE
                           WHEN cancel_requested THEN 'cancelled'
                           ELSE 'pending'
                       END,
                       available_at = %s,
                       locked_at = NULL,
                       locked_until = NULL,
                       locked_by = NULL,
                       last_error = COALESCE(last_error, 'AI worker lease expired.'),
                       completed_at = CASE WHEN cancel_requested THEN %s ELSE NULL END,
                       updated_at = %s
                 WHERE processing_status = 'leased'
                   AND locked_until IS NOT NULL
                   AND locked_until <= %s
                """,
                (timestamp, timestamp, timestamp, timestamp),
            )
            return int(cur.rowcount or 0)


def claim_ai_jobs(
    *,
    worker_id: str,
    limit: int = 5,
    lease_seconds: int = 240,
    now: datetime | None = None,
    connect: ConnectFactory | None = None,
) -> list[dict[str, Any]]:
    worker = _required_text(worker_id, "worker_id", 200)
    limit = min(25, max(1, int(limit or 5)))
    lease_seconds = min(900, max(30, int(lease_seconds or 240)))
    timestamp = _now(now)
    locked_until = timestamp + timedelta(seconds=lease_seconds)
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE funnel_workspace_ai_jobs
                   SET processing_status = CASE
                           WHEN cancel_requested THEN 'cancelled'
                           ELSE 'pending'
                       END,
                       available_at = %s,
                       locked_at = NULL,
                       locked_until = NULL,
                       locked_by = NULL,
                       last_error = COALESCE(last_error, 'AI worker lease expired.'),
                       completed_at = CASE WHEN cancel_requested THEN %s ELSE NULL END,
                       updated_at = %s
                 WHERE processing_status = 'leased'
                   AND locked_until IS NOT NULL
                   AND locked_until <= %s
                """,
                (timestamp, timestamp, timestamp, timestamp),
            )
            cur.execute(
                """
                UPDATE funnel_workspace_ai_jobs j
                   SET processing_status = 'cancelled',
                       cancel_requested = true,
                       last_error = COALESCE(last_error, 'Conversation changed before AI job started.'),
                       completed_at = %s,
                       updated_at = %s
                  FROM funnel_workspace_conversations c
                 WHERE j.conversation_id = c.id
                   AND j.processing_status = 'pending'
                   AND (
                        j.expected_version <> c.state_version
                        OR c.control_mode <> 'ai'
                        OR c.status NOT IN ('new', 'open', 'waiting')
                        OR (c.reply_deadline_at IS NOT NULL AND c.reply_deadline_at <= %s)
                   )
                """,
                (timestamp, timestamp, timestamp),
            )
            cur.execute(
                """
                WITH candidates AS (
                    SELECT j.id
                      FROM funnel_workspace_ai_jobs j
                      JOIN funnel_workspace_conversations c ON c.id = j.conversation_id
                     WHERE j.processing_status = 'pending'
                       AND j.available_at <= %s
                       AND j.cancel_requested = false
                       AND j.expected_version = c.state_version
                       AND c.control_mode = 'ai'
                       AND c.status IN ('new', 'open', 'waiting')
                       AND (c.reply_deadline_at IS NULL OR c.reply_deadline_at > %s)
                     ORDER BY j.available_at, j.id
                     FOR UPDATE OF j SKIP LOCKED
                     LIMIT %s
                )
                UPDATE funnel_workspace_ai_jobs j
                   SET processing_status = 'leased',
                       attempts = attempts + 1,
                       locked_at = %s,
                       locked_until = %s,
                       locked_by = %s,
                       updated_at = %s
                  FROM candidates c
                 WHERE j.id = c.id
             RETURNING j.*
                """,
                (
                    timestamp,
                    timestamp,
                    limit,
                    timestamp,
                    locked_until,
                    worker,
                    timestamp,
                ),
            )
            return [dict(row) for row in cur.fetchall()]


def ai_job_guard(
    job_id: Any,
    *,
    worker_id: str,
    connect: ConnectFactory | None = None,
) -> dict[str, Any]:
    item_id = _positive_int(job_id, "job_id")
    worker = _required_text(worker_id, "worker_id", 200)
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT j.*, c.control_mode, c.state_version AS current_version,
                       c.status AS conversation_status, c.reply_deadline_at
                  FROM funnel_workspace_ai_jobs j
                  JOIN funnel_workspace_conversations c ON c.id = j.conversation_id
                 WHERE j.id = %s
                """,
                (item_id,),
            )
            row = _record(cur.fetchone())
    if row is None:
        return {"allowed": False, "reason": "not_found", "job": None}
    if row["processing_status"] != "leased" or row.get("locked_by") != worker:
        return {"allowed": False, "reason": "lease_lost", "job": row}
    if row.get("cancel_requested"):
        return {"allowed": False, "reason": "cancel_requested", "job": row}
    if int(row["expected_version"]) != int(row["current_version"]):
        return {"allowed": False, "reason": "stale_version", "job": row}
    if row["control_mode"] != "ai":
        return {"allowed": False, "reason": "control_changed", "job": row}
    if row["conversation_status"] not in ACTIVE_STATUSES:
        return {"allowed": False, "reason": "conversation_inactive", "job": row}
    deadline = row.get("reply_deadline_at")
    if deadline and _now(deadline) <= _now():
        return {"allowed": False, "reason": "reply_window_expired", "job": row}
    return {"allowed": True, "reason": None, "job": row}


def complete_ai_job(
    job_id: Any,
    *,
    worker_id: str,
    outbox_id: int | None = None,
    now: datetime | None = None,
    connect: ConnectFactory | None = None,
) -> dict[str, Any]:
    item_id = _positive_int(job_id, "job_id")
    worker = _required_text(worker_id, "worker_id", 200)
    timestamp = _now(now)
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE funnel_workspace_ai_jobs
                   SET processing_status = 'done',
                       outbox_id = COALESCE(%s, outbox_id),
                       locked_at = NULL,
                       locked_until = NULL,
                       locked_by = NULL,
                       completed_at = %s,
                       updated_at = %s
                 WHERE id = %s
                   AND processing_status = 'leased'
                   AND locked_by = %s
             RETURNING *
                """,
                (outbox_id, timestamp, timestamp, item_id, worker),
            )
            row = _record(cur.fetchone())
            if row is None:
                raise WorkspaceConflictError(
                    "Задание ИИ больше не принадлежит этому обработчику.",
                    details={"job_id": item_id},
                )
            return row


def retry_ai_job(
    job_id: Any,
    *,
    worker_id: str,
    error: str,
    delay_seconds: int = 5,
    max_attempts: int = 4,
    now: datetime | None = None,
    connect: ConnectFactory | None = None,
) -> dict[str, Any]:
    item_id = _positive_int(job_id, "job_id")
    worker = _required_text(worker_id, "worker_id", 200)
    clean_error = _required_text(error, "error", 4000)
    delay_seconds = min(3600, max(0, int(delay_seconds or 0)))
    max_attempts = min(20, max(1, int(max_attempts or 4)))
    timestamp = _now(now)
    available_at = timestamp + timedelta(seconds=delay_seconds)
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE funnel_workspace_ai_jobs
                   SET processing_status = CASE
                           WHEN cancel_requested THEN 'cancelled'
                           WHEN attempts >= %s THEN 'failed'
                           ELSE 'pending'
                       END,
                       available_at = %s,
                       locked_at = NULL,
                       locked_until = NULL,
                       locked_by = NULL,
                       last_error = %s,
                       completed_at = CASE
                           WHEN cancel_requested OR attempts >= %s THEN %s
                           ELSE NULL
                       END,
                       updated_at = %s
                 WHERE id = %s
                   AND processing_status = 'leased'
                   AND locked_by = %s
             RETURNING *
                """,
                (
                    max_attempts,
                    available_at,
                    clean_error,
                    max_attempts,
                    timestamp,
                    timestamp,
                    item_id,
                    worker,
                ),
            )
            row = _record(cur.fetchone())
            if row is None:
                raise WorkspaceConflictError(
                    "Задание ИИ больше не принадлежит этому обработчику.",
                    details={"job_id": item_id},
                )
            return row


def cancel_ai_job(
    job_id: Any,
    *,
    worker_id: str | None = None,
    reason: str = "AI job cancelled.",
    now: datetime | None = None,
    connect: ConnectFactory | None = None,
) -> dict[str, Any]:
    item_id = _positive_int(job_id, "job_id")
    timestamp = _now(now)
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            clauses = ["id = %s", "processing_status IN ('pending', 'leased')"]
            params: list[Any] = [
                _required_text(reason, "reason", 4000),
                timestamp,
                timestamp,
                item_id,
            ]
            if worker_id:
                clauses.append("(processing_status = 'pending' OR locked_by = %s)")
                params.append(_required_text(worker_id, "worker_id", 200))
            cur.execute(
                f"""
                UPDATE funnel_workspace_ai_jobs
                   SET processing_status = 'cancelled',
                       cancel_requested = true,
                       last_error = %s,
                       locked_at = NULL,
                       locked_until = NULL,
                       locked_by = NULL,
                       completed_at = %s,
                       updated_at = %s
                 WHERE {' AND '.join(clauses)}
             RETURNING *
                """,
                tuple(params),
            )
            row = _record(cur.fetchone())
            if row is None:
                raise WorkspaceConflictError(
                    "Задание ИИ уже завершено или его аренда потеряна.",
                    details={"job_id": item_id},
                )
            return row


def list_pending_ai_jobs(
    *,
    limit: int = 100,
    connect: ConnectFactory | None = None,
) -> list[dict[str, Any]]:
    limit = min(500, max(1, int(limit or 100)))
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                  FROM funnel_workspace_ai_jobs
                 WHERE processing_status IN ('pending', 'leased', 'failed')
                 ORDER BY id
                 LIMIT %s
                """,
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]


def capture_update(
    *,
    external_update_id: Any,
    payload: Mapping[str, Any],
    source_key: str = DEFAULT_SOURCE_KEY,
    available_at: datetime | None = None,
    connect: ConnectFactory | None = None,
) -> dict[str, Any]:
    source = _required_text(source_key, "source_key", 100)
    update_id = _required_text(external_update_id, "external_update_id", 300)
    if not isinstance(payload, Mapping):
        raise WorkspaceValidationError("payload должен быть JSON-объектом.")
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            _ensure_source_cursor(cur, source)
            cur.execute(
                """
                INSERT INTO funnel_workspace_updates (
                    source_key, external_update_id, payload, available_at
                )
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (source_key, external_update_id)
                DO UPDATE SET updated_at = now()
                RETURNING *
                """,
                (
                    source,
                    update_id,
                    Jsonb(dict(payload)),
                    _now(available_at),
                ),
            )
            return dict(cur.fetchone())


def recover_updates(
    *,
    now: datetime | None = None,
    connect: ConnectFactory | None = None,
) -> int:
    timestamp = _now(now)
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE funnel_workspace_updates
                   SET processing_status = 'retry',
                       available_at = %s,
                       locked_at = NULL,
                       locked_until = NULL,
                       locked_by = NULL,
                       last_error = COALESCE(last_error, 'Worker lease expired.'),
                       updated_at = %s
                 WHERE processing_status = 'processing'
                   AND locked_until IS NOT NULL
                   AND locked_until <= %s
                """,
                (timestamp, timestamp, timestamp),
            )
            return int(cur.rowcount or 0)


def claim_updates(
    *,
    worker_id: str,
    lane: str = "business",
    source_key: str = DEFAULT_SOURCE_KEY,
    limit: int = 25,
    lease_seconds: int = 60,
    now: datetime | None = None,
    connect: ConnectFactory | None = None,
) -> list[dict[str, Any]]:
    worker = _required_text(worker_id, "worker_id", 200)
    clean_lane = str(lane or "").strip().lower()
    if clean_lane not in VALID_UPDATE_LANES:
        raise WorkspaceValidationError(
            "Неизвестная очередь Telegram-обновлений.",
            details={"lane": clean_lane},
        )
    source = _required_text(source_key, "source_key", 100)
    limit = min(250, max(1, int(limit or 25)))
    lease_seconds = min(900, max(10, int(lease_seconds or 60)))
    timestamp = _now(now)
    locked_until = timestamp + timedelta(seconds=lease_seconds)
    lane_predicate = (
        "(payload ? 'message')"
        if clean_lane == "bot"
        else "NOT (payload ? 'message')"
    )
    aliased_lane_predicate = (
        "(u.payload ? 'message')"
        if clean_lane == "bot"
        else "NOT (u.payload ? 'message')"
    )
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE funnel_workspace_updates
                   SET processing_status = 'retry',
                       available_at = %s,
                       locked_at = NULL,
                       locked_until = NULL,
                       locked_by = NULL,
                       last_error = COALESCE(last_error, 'Worker lease expired.'),
                       updated_at = %s
                 WHERE processing_status = 'processing'
                   AND source_key = %s
                   AND {lane_predicate}
                   AND locked_until IS NOT NULL
                   AND locked_until <= %s
                """,
                (timestamp, timestamp, source, timestamp),
            )
            cur.execute(
                f"""
                WITH head AS MATERIALIZED (
                    SELECT u.id
                      FROM funnel_workspace_updates u
                     WHERE u.source_key = %s
                       AND u.processing_status IN ('pending', 'processing', 'retry')
                       AND {aliased_lane_predicate}
                     ORDER BY u.id
                     LIMIT 1
                ),
                candidate AS (
                    SELECT u.id
                      FROM funnel_workspace_updates u
                      JOIN head h ON h.id = u.id
                     WHERE u.processing_status IN ('pending', 'retry')
                       AND u.available_at <= %s
                     FOR UPDATE OF u
                )
                UPDATE funnel_workspace_updates u
                   SET processing_status = 'processing',
                       attempts = attempts + 1,
                       locked_at = %s,
                       locked_until = %s,
                       locked_by = %s,
                       updated_at = %s
                  FROM candidate c
                 WHERE u.id = c.id
             RETURNING u.*
                """,
                (
                    source,
                    timestamp,
                    timestamp,
                    locked_until,
                    worker,
                    timestamp,
                ),
            )
            rows = [dict(row) for row in cur.fetchall()]
            return rows[:limit]


def complete_update(
    update_id: Any,
    *,
    worker_id: str,
    conversation_id: int | None = None,
    message_id: int | None = None,
    now: datetime | None = None,
    connect: ConnectFactory | None = None,
) -> dict[str, Any]:
    item_id = _positive_int(update_id, "update_id")
    worker = _required_text(worker_id, "worker_id", 200)
    timestamp = _now(now)
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE funnel_workspace_updates
                   SET processing_status = 'done',
                       conversation_id = COALESCE(%s, conversation_id),
                       message_id = COALESCE(%s, message_id),
                       locked_at = NULL,
                       locked_until = NULL,
                       locked_by = NULL,
                       completed_at = %s,
                       updated_at = %s
                 WHERE id = %s
                   AND processing_status = 'processing'
                   AND locked_by = %s
             RETURNING *
                """,
                (conversation_id, message_id, timestamp, timestamp, item_id, worker),
            )
            row = _record(cur.fetchone())
            if row is None:
                raise WorkspaceConflictError(
                    "Событие больше не принадлежит этому обработчику.",
                    details={"update_id": item_id},
                )
            return row


def retry_update(
    update_id: Any,
    *,
    worker_id: str,
    error: str,
    delay_seconds: int = 5,
    max_attempts: int = 10,
    now: datetime | None = None,
    connect: ConnectFactory | None = None,
) -> dict[str, Any]:
    item_id = _positive_int(update_id, "update_id")
    worker = _required_text(worker_id, "worker_id", 200)
    clean_error = _required_text(error, "error", 4000)
    delay_seconds = min(86_400, max(0, int(delay_seconds or 0)))
    max_attempts = min(100, max(1, int(max_attempts or 10)))
    timestamp = _now(now)
    available_at = timestamp + timedelta(seconds=delay_seconds)
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE funnel_workspace_updates
                   SET processing_status = CASE
                           WHEN attempts >= %s THEN 'dead_letter'
                           ELSE 'retry'
                       END,
                       available_at = %s,
                       locked_at = NULL,
                       locked_until = NULL,
                       locked_by = NULL,
                       last_error = %s,
                       completed_at = CASE WHEN attempts >= %s THEN %s ELSE NULL END,
                       updated_at = %s
                 WHERE id = %s
                   AND processing_status = 'processing'
                   AND locked_by = %s
             RETURNING *
                """,
                (
                    max_attempts,
                    available_at,
                    clean_error,
                    max_attempts,
                    timestamp,
                    timestamp,
                    item_id,
                    worker,
                ),
            )
            row = _record(cur.fetchone())
            if row is None:
                raise WorkspaceConflictError(
                    "Событие больше не принадлежит этому обработчику.",
                    details={"update_id": item_id},
                )
            return row


def list_pending_updates(
    *,
    limit: int = 100,
    connect: ConnectFactory | None = None,
) -> list[dict[str, Any]]:
    limit = min(500, max(1, int(limit or 100)))
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                  FROM funnel_workspace_updates
                 WHERE processing_status IN ('pending', 'processing', 'retry', 'dead_letter')
                 ORDER BY id
                 LIMIT %s
                """,
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]


def _recover_outbox_cursor(cur: Any, timestamp: datetime) -> int:
    """Recover reservations without confusing them with provider-side calls."""

    cur.execute(
        """
        WITH retryable AS (
            UPDATE funnel_workspace_outbox
               SET delivery_status = 'pending',
                   available_at = %s,
                   locked_at = NULL,
                   locked_until = NULL,
                   locked_by = NULL,
                   last_error = COALESCE(last_error, 'Worker lease expired before provider send started.'),
                   updated_at = %s
             WHERE delivery_status = 'leased'
               AND locked_until IS NOT NULL
               AND locked_until <= %s
         RETURNING message_id
        )
        UPDATE funnel_workspace_messages
           SET delivery_status = 'pending',
               error_code = NULL,
               error_detail = NULL
         WHERE id IN (SELECT message_id FROM retryable)
        """,
        (timestamp, timestamp, timestamp),
    )
    retryable_count = int(cur.rowcount or 0)
    cur.execute(
        """
        WITH uncertain AS (
            UPDATE funnel_workspace_outbox
               SET delivery_status = 'unknown',
                   locked_at = NULL,
                   locked_until = NULL,
                   locked_by = NULL,
                   last_error = COALESCE(last_error, 'Provider call lease expired; delivery is unknown.'),
                   updated_at = %s
             WHERE delivery_status = 'sending'
               AND locked_until IS NOT NULL
               AND locked_until <= %s
         RETURNING message_id
        )
        UPDATE funnel_workspace_messages
           SET delivery_status = 'unknown',
               error_code = 'delivery_unknown',
               error_detail = COALESCE(error_detail, 'Не удалось достоверно подтвердить доставку.')
         WHERE id IN (SELECT message_id FROM uncertain)
        """,
        (timestamp, timestamp),
    )
    return retryable_count + int(cur.rowcount or 0)


def recover_outbox(
    *,
    now: datetime | None = None,
    connect: ConnectFactory | None = None,
) -> int:
    """Retry pre-call crashes and mark only in-flight provider calls unknown."""

    timestamp = _now(now)
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            return _recover_outbox_cursor(cur, timestamp)


def _cancel_stale_agent_outbox(cur: Any, timestamp: datetime) -> None:
    cur.execute(
        """
        WITH cancelled AS (
            UPDATE funnel_workspace_outbox o
               SET delivery_status = 'cancelled',
                   cancel_requested = true,
                   last_error = COALESCE(last_error, 'AI control/version changed before delivery.'),
                   updated_at = %s
              FROM funnel_workspace_conversations c
             WHERE o.conversation_id = c.id
               AND o.author_type = 'agent'
               AND o.delivery_status = 'pending'
               AND (
                    c.control_mode <> 'ai'
                    OR c.state_version <> o.conversation_version
                    OR c.status NOT IN ('new', 'open', 'waiting')
               )
         RETURNING o.message_id
        )
        UPDATE funnel_workspace_messages
           SET delivery_status = 'cancelled',
               error_code = 'stale_ai_answer',
               error_detail = 'Ответ ИИ отменён: диалог изменился.'
         WHERE id IN (SELECT message_id FROM cancelled)
        """,
        (timestamp,),
    )


def claim_outbox(
    *,
    worker_id: str,
    limit: int = 25,
    lease_seconds: int = 45,
    now: datetime | None = None,
    connect: ConnectFactory | None = None,
) -> list[dict[str, Any]]:
    worker = _required_text(worker_id, "worker_id", 200)
    limit = min(100, max(1, int(limit or 25)))
    lease_seconds = min(300, max(10, int(lease_seconds or 45)))
    timestamp = _now(now)
    locked_until = timestamp + timedelta(seconds=lease_seconds)
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            _recover_outbox_cursor(cur, timestamp)
            _cancel_stale_agent_outbox(cur, timestamp)
            cur.execute(
                """
                WITH candidates AS (
                    SELECT o.id
                      FROM funnel_workspace_outbox o
                      JOIN funnel_workspace_conversations c ON c.id = o.conversation_id
                     WHERE o.delivery_status = 'pending'
                       AND o.available_at <= %s
                       AND o.cancel_requested = false
                       AND (
                            o.author_type = 'operator'
                            OR (
                                o.author_type = 'agent'
                                AND c.control_mode = 'ai'
                                AND c.state_version = o.conversation_version
                                AND c.status IN ('new', 'open', 'waiting')
                            )
                       )
                     ORDER BY o.id
                     FOR UPDATE OF o SKIP LOCKED
                     LIMIT %s
                )
                UPDATE funnel_workspace_outbox o
                   SET delivery_status = 'leased',
                       attempts = attempts + 1,
                       locked_at = %s,
                       locked_until = %s,
                       locked_by = %s,
                       updated_at = %s
                  FROM candidates c
                 WHERE o.id = c.id
             RETURNING o.*
                """,
                (timestamp, limit, timestamp, locked_until, worker, timestamp),
            )
            return [dict(row) for row in cur.fetchall()]


def outbox_send_guard(
    outbox_id: Any,
    *,
    worker_id: str,
    connect: ConnectFactory | None = None,
) -> dict[str, Any]:
    item_id = _positive_int(outbox_id, "outbox_id")
    worker = _required_text(worker_id, "worker_id", 200)
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT o.*, c.control_mode, c.state_version AS current_version,
                       c.status AS conversation_status
                  FROM funnel_workspace_outbox o
                  JOIN funnel_workspace_conversations c ON c.id = o.conversation_id
                 WHERE o.id = %s
                """,
                (item_id,),
            )
            row = _record(cur.fetchone())
    if row is None:
        return {"allowed": False, "reason": "not_found", "outbox": None}
    if row["delivery_status"] != "leased" or row.get("locked_by") != worker:
        return {"allowed": False, "reason": "lease_lost", "outbox": row}
    if row.get("cancel_requested"):
        return {"allowed": False, "reason": "cancel_requested", "outbox": row}
    if row["author_type"] == "agent":
        if row["control_mode"] != "ai":
            return {"allowed": False, "reason": "control_changed", "outbox": row}
        if int(row["conversation_version"]) != int(row["current_version"]):
            return {"allowed": False, "reason": "stale_version", "outbox": row}
        if row["conversation_status"] not in ACTIVE_STATUSES:
            return {"allowed": False, "reason": "conversation_inactive", "outbox": row}
    return {"allowed": True, "reason": None, "outbox": row}


def begin_outbox_send(
    outbox_id: Any,
    *,
    worker_id: str,
    lease_seconds: int = 90,
    now: datetime | None = None,
    connect: ConnectFactory | None = None,
) -> dict[str, Any]:
    """Cross the durable side-effect boundary immediately before the Bot API call."""

    item_id = _positive_int(outbox_id, "outbox_id")
    worker = _required_text(worker_id, "worker_id", 200)
    lease_seconds = min(300, max(10, int(lease_seconds or 90)))
    timestamp = _now(now)
    locked_until = timestamp + timedelta(seconds=lease_seconds)
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT conversation_id
                  FROM funnel_workspace_outbox
                 WHERE id = %s
                """,
                (item_id,),
            )
            target = _record(cur.fetchone())
            if target is None:
                row = None
            else:
                # Serialize the provider boundary with human takeover/operator
                # enqueue. All three paths lock conversation before outbox.
                _load_conversation_locked(cur, int(target["conversation_id"]))
                cur.execute(
                    """
                    UPDATE funnel_workspace_outbox o
                       SET delivery_status = 'sending',
                           locked_at = %s,
                           locked_until = %s,
                           updated_at = %s
                      FROM funnel_workspace_conversations c
                     WHERE o.id = %s
                       AND o.conversation_id = c.id
                       AND o.delivery_status = 'leased'
                       AND o.locked_by = %s
                       AND o.cancel_requested = false
                       AND (
                            o.author_type = 'operator'
                            OR (
                                o.author_type = 'agent'
                                AND c.control_mode = 'ai'
                                AND c.state_version = o.conversation_version
                                AND c.status IN ('new', 'open', 'waiting')
                            )
                       )
                 RETURNING o.*
                    """,
                    (
                        timestamp,
                        locked_until,
                        timestamp,
                        item_id,
                        worker,
                    ),
                )
                row = _record(cur.fetchone())
    if row is not None:
        return {"allowed": True, "reason": None, "outbox": row}
    return outbox_send_guard(
        item_id,
        worker_id=worker,
        connect=connect,
    )


def finish_outbox(
    outbox_id: Any,
    *,
    worker_id: str,
    result: str,
    provider_message_id: Any = None,
    error: Any = None,
    retry_at: datetime | None = None,
    now: datetime | None = None,
    connect: ConnectFactory | None = None,
) -> dict[str, Any]:
    item_id = _positive_int(outbox_id, "outbox_id")
    worker = _required_text(worker_id, "worker_id", 200)
    clean_result = str(result or "").strip().lower()
    if clean_result not in VALID_DELIVERY_RESULTS:
        raise WorkspaceValidationError(
            "Неизвестный результат доставки.",
            details={"result": clean_result},
        )
    timestamp = _now(now)
    next_status = clean_result
    available_at = None
    if clean_result == "failed" and retry_at is not None:
        next_status = "pending"
        available_at = _now(retry_at)
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE funnel_workspace_outbox
                   SET delivery_status = %s,
                       available_at = COALESCE(%s, available_at),
                       locked_at = NULL,
                       locked_until = NULL,
                       locked_by = NULL,
                       provider_message_id = COALESCE(%s, provider_message_id),
                       last_error = %s,
                       sent_at = CASE WHEN %s = 'sent' THEN %s ELSE sent_at END,
                       updated_at = %s
                 WHERE id = %s
                   AND delivery_status IN ('leased', 'sending')
                   AND locked_by = %s
             RETURNING *
                """,
                (
                    next_status,
                    available_at,
                    _clean_optional(provider_message_id, 300),
                    _clean_optional(error, 4000),
                    clean_result,
                    timestamp,
                    timestamp,
                    item_id,
                    worker,
                ),
            )
            outbox = _record(cur.fetchone())
            if outbox is None:
                cur.execute(
                    """
                    SELECT o.*, row_to_json(m) AS message
                      FROM funnel_workspace_outbox o
                      JOIN funnel_workspace_messages m ON m.id = o.message_id
                     WHERE o.id = %s
                    """,
                    (item_id,),
                )
                existing = _record(cur.fetchone())
                if existing is not None and (
                    existing.get("delivery_status") == "sent"
                    or existing.get("delivery_status") == next_status
                ):
                    message = dict(existing.pop("message"))
                    delivery_action = (
                        _enqueue_delivery_effect_action_cursor(cur, existing)
                        if existing.get("delivery_status") == "sent"
                        else None
                    )
                    crm_action = (
                        _enqueue_crm_stage_action_cursor(cur, existing)
                        if existing.get("delivery_status") == "sent"
                        else None
                    )
                    return {
                        "outbox": existing,
                        "message": message,
                        "delivery_action": delivery_action,
                        "crm_action": crm_action,
                    }
                raise WorkspaceConflictError(
                    "Задание доставки больше не принадлежит этому обработчику.",
                    details={"outbox_id": item_id},
                )
            message_status = "pending" if next_status == "pending" else next_status
            cur.execute(
                """
                UPDATE funnel_workspace_messages
                   SET delivery_status = %s,
                       provider_message_id = COALESCE(%s, provider_message_id),
                       error_code = CASE
                           WHEN %s = 'sent' THEN NULL
                           WHEN %s = 'unknown' THEN 'delivery_unknown'
                           WHEN %s = 'cancelled' THEN 'delivery_cancelled'
                           ELSE 'delivery_failed'
                       END,
                       error_detail = %s,
                       sent_at = CASE WHEN %s = 'sent' THEN %s ELSE sent_at END
                 WHERE id = %s
             RETURNING *
                """,
                (
                    message_status,
                    _clean_optional(provider_message_id, 300),
                    clean_result,
                    clean_result,
                    clean_result,
                    _clean_optional(error, 4000),
                    clean_result,
                    timestamp,
                    outbox["message_id"],
                ),
            )
            message = dict(cur.fetchone())
            delivery_action = (
                _enqueue_delivery_effect_action_cursor(cur, outbox)
                if outbox.get("delivery_status") == "sent"
                else None
            )
            crm_action = (
                _enqueue_crm_stage_action_cursor(cur, outbox)
                if outbox.get("delivery_status") == "sent"
                else None
            )
            return {
                "outbox": outbox,
                "message": message,
                "delivery_action": delivery_action,
                "crm_action": crm_action,
            }


def _outbox_target_stage(outbox: Mapping[str, Any]) -> str | None:
    payload = outbox.get("payload") or {}
    if not isinstance(payload, Mapping):
        return None
    target = str(payload.get("stage_move") or "").strip()
    if not target:
        return None
    if len(target) > 200:
        raise WorkspaceValidationError(
            "CRM stage_move длиннее допустимых 200 символов.",
            details={"outbox_id": outbox.get("id")},
        )
    return target


def _enqueue_delivery_effect_action_cursor(
    cur: Any,
    outbox: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Persist idempotent local facts/escalation for a confirmed delivery."""

    if str(outbox.get("delivery_status") or "") != "sent":
        return None
    source_payload = outbox.get("payload") or {}
    if not isinstance(source_payload, Mapping):
        source_payload = {}
    asset = str(source_payload.get("asset") or "").strip()
    escalate = bool(source_payload.get("escalate_after_delivery"))
    if asset not in {"terms", "form"} and not (
        outbox.get("author_type") == "agent" and escalate
    ):
        return None
    outbox_id = _positive_int(outbox.get("id"), "outbox_id")
    conversation_id = _positive_int(
        outbox.get("conversation_id"),
        "conversation_id",
    )
    message_id = _positive_int(outbox.get("message_id"), "message_id")
    idempotency_key = f"delivery-effects:outbox:{outbox_id}"
    action_payload = {
        "trigger": "telegram_delivery",
        "asset": asset if asset in {"terms", "form"} else "",
        "telegram_id": _clean_optional(outbox.get("external_chat_id"), 200),
        "author_type": str(outbox.get("author_type") or ""),
        "conversation_version": int(outbox.get("conversation_version") or 0),
        "escalate_after_delivery": escalate,
        "escalation_reason": _clean_optional(
            source_payload.get("escalation_reason"),
            1000,
        ),
        "provider_message_id": _clean_optional(
            outbox.get("provider_message_id"),
            300,
        ),
    }
    cur.execute(
        """
        INSERT INTO funnel_workspace_crm_actions (
            conversation_id, message_id, outbox_id, action_type,
            target_stage, payload, idempotency_key
        )
        VALUES (%s, %s, %s, 'delivery_effects', NULL, %s, %s)
        ON CONFLICT (outbox_id, action_type) DO NOTHING
        RETURNING *
        """,
        (
            conversation_id,
            message_id,
            outbox_id,
            Jsonb(action_payload),
            idempotency_key,
        ),
    )
    action = _record(cur.fetchone())
    if action is not None:
        return action
    cur.execute(
        """
        SELECT *
          FROM funnel_workspace_crm_actions
         WHERE outbox_id = %s
           AND action_type = 'delivery_effects'
        """,
        (outbox_id,),
    )
    existing = _record(cur.fetchone())
    if existing is None:
        raise WorkspaceConflictError(
            "Post-delivery действие не удалось поставить в очередь.",
            details={"outbox_id": outbox_id},
        )
    if (
        str(existing.get("idempotency_key") or "") != idempotency_key
        or int(existing.get("conversation_id") or 0) != conversation_id
        or int(existing.get("message_id") or 0) != message_id
    ):
        raise WorkspaceConflictError(
            "Исходящее сообщение уже связано с другими post-delivery эффектами.",
            details={"outbox_id": outbox_id},
        )
    return existing


def _enqueue_crm_stage_action_cursor(
    cur: Any,
    outbox: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Create the post-delivery CRM action in the caller's DB transaction."""

    if str(outbox.get("delivery_status") or "") != "sent":
        return None
    target_stage = _outbox_target_stage(outbox)
    if not target_stage:
        return None
    outbox_id = _positive_int(outbox.get("id"), "outbox_id")
    conversation_id = _positive_int(
        outbox.get("conversation_id"),
        "conversation_id",
    )
    message_id = _positive_int(outbox.get("message_id"), "message_id")
    idempotency_key = f"crm-stage:outbox:{outbox_id}:{target_stage}"
    action_payload = {
        "trigger": "telegram_delivery",
        "provider_message_id": _clean_optional(
            outbox.get("provider_message_id"),
            300,
        ),
    }
    cur.execute(
        """
        INSERT INTO funnel_workspace_crm_actions (
            conversation_id, message_id, outbox_id, action_type,
            target_stage, payload, idempotency_key
        )
        VALUES (%s, %s, %s, 'move_stage', %s, %s, %s)
        ON CONFLICT (outbox_id, action_type) DO NOTHING
        RETURNING *
        """,
        (
            conversation_id,
            message_id,
            outbox_id,
            target_stage,
            Jsonb(action_payload),
            idempotency_key,
        ),
    )
    action = _record(cur.fetchone())
    if action is not None:
        return action
    cur.execute(
        """
        SELECT *
          FROM funnel_workspace_crm_actions
         WHERE outbox_id = %s
           AND action_type = 'move_stage'
        """,
        (outbox_id,),
    )
    existing = _record(cur.fetchone())
    if existing is None:
        raise WorkspaceConflictError(
            "CRM-действие не удалось поставить в очередь.",
            details={"outbox_id": outbox_id},
        )
    if (
        str(existing.get("target_stage") or "") != target_stage
        or str(existing.get("idempotency_key") or "") != idempotency_key
        or int(existing.get("conversation_id") or 0) != conversation_id
        or int(existing.get("message_id") or 0) != message_id
    ):
        raise WorkspaceConflictError(
            "Исходящее сообщение уже связано с другим CRM-действием.",
            details={"outbox_id": outbox_id},
        )
    return existing


def _enqueue_ensure_deal_action_cursor(
    cur: Any,
    *,
    conversation_id: Any,
    message_id: Any,
) -> dict[str, Any]:
    """Schedule one bounded, deduplicated CRM-link action per conversation."""

    item_id = _positive_int(conversation_id, "conversation_id")
    trigger_message_id = _positive_int(message_id, "message_id")
    idempotency_key = f"crm-ensure:conversation:{item_id}"
    cur.execute(
        """
        INSERT INTO funnel_workspace_crm_actions (
            conversation_id, message_id, outbox_id, action_type,
            target_stage, payload, idempotency_key
        )
        VALUES (%s, %s, NULL, 'ensure_deal', NULL, %s, %s)
        ON CONFLICT (conversation_id, action_type)
            WHERE action_type = 'ensure_deal'
        DO NOTHING
        RETURNING *
        """,
        (
            item_id,
            trigger_message_id,
            Jsonb({"trigger": "inbound_message"}),
            idempotency_key,
        ),
    )
    action = _record(cur.fetchone())
    if action is not None:
        return action
    cur.execute(
        """
        SELECT *
          FROM funnel_workspace_crm_actions
         WHERE conversation_id = %s
           AND action_type = 'ensure_deal'
        """,
        (item_id,),
    )
    existing = _record(cur.fetchone())
    if existing is None:
        raise WorkspaceConflictError(
            "Создание CRM-сделки не удалось поставить в очередь.",
            details={"conversation_id": item_id},
        )
    if str(existing.get("idempotency_key") or "") != idempotency_key:
        raise WorkspaceConflictError(
            "Диалог уже связан с другим CRM ensure-действием.",
            details={"conversation_id": item_id},
        )
    return existing


def ensure_deal_action(
    conversation_id: Any,
    *,
    message_id: Any | None = None,
    connect: ConnectFactory | None = None,
) -> dict[str, Any]:
    """Idempotently create/inspect the asynchronous CRM-link action."""

    item_id = _positive_int(conversation_id, "conversation_id")
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                  FROM funnel_workspace_conversations
                 WHERE id = %s
                 FOR UPDATE
                """,
                (item_id,),
            )
            conversation = _record(cur.fetchone())
            if conversation is None:
                raise WorkspaceNotFoundError(
                    "Диалог не найден.",
                    details={"conversation_id": item_id},
                )
            if conversation.get("deal_id"):
                cur.execute(
                    """
                    SELECT *
                      FROM funnel_workspace_crm_actions
                     WHERE conversation_id = %s
                       AND action_type = 'ensure_deal'
                    """,
                    (item_id,),
                )
                return _record(cur.fetchone()) or {
                    "conversation_id": item_id,
                    "processing_status": "done",
                    "result": {
                        "status": "already_linked",
                        "deal_id": int(conversation["deal_id"]),
                    },
                }
            trigger_message_id = (
                _positive_int(message_id, "message_id")
                if message_id is not None
                else None
            )
            if trigger_message_id is None:
                cur.execute(
                    """
                    SELECT id
                      FROM funnel_workspace_messages
                     WHERE conversation_id = %s
                       AND author_type = 'client'
                     ORDER BY id DESC
                     LIMIT 1
                    """,
                    (item_id,),
                )
                trigger = _record(cur.fetchone())
                if trigger is None:
                    raise WorkspaceValidationError(
                        "У диалога нет входящего сообщения для CRM ensure-действия.",
                        details={"conversation_id": item_id},
                    )
                trigger_message_id = int(trigger["id"])
            return _enqueue_ensure_deal_action_cursor(
                cur,
                conversation_id=item_id,
                message_id=trigger_message_id,
            )


def _backfill_missing_deal_actions_cursor(cur: Any, limit: int) -> int:
    cur.execute(
        """
        SELECT c.id AS conversation_id, trigger.id AS message_id
          FROM funnel_workspace_conversations c
          JOIN LATERAL (
                SELECT m.id
                  FROM funnel_workspace_messages m
                 WHERE m.conversation_id = c.id
                   AND m.author_type = 'client'
                 ORDER BY m.id DESC
                 LIMIT 1
          ) trigger ON true
         WHERE c.deal_id IS NULL
           AND NOT EXISTS (
                SELECT 1
                  FROM funnel_workspace_crm_actions a
                 WHERE a.conversation_id = c.id
                   AND a.action_type = 'ensure_deal'
           )
         ORDER BY c.id
         FOR UPDATE OF c SKIP LOCKED
         LIMIT %s
        """,
        (limit,),
    )
    rows = [dict(row) for row in cur.fetchall()]
    for row in rows:
        _enqueue_ensure_deal_action_cursor(
            cur,
            conversation_id=row["conversation_id"],
            message_id=row["message_id"],
        )
    return len(rows)


def backfill_missing_deal_actions(
    *,
    limit: int = 100,
    connect: ConnectFactory | None = None,
) -> int:
    """Boundedly repair conversations created before async CRM ensure existed."""

    row_limit = min(1000, max(1, int(limit or 100)))
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            return _backfill_missing_deal_actions_cursor(cur, row_limit)


def ensure_crm_action_for_sent_outbox(
    outbox_id: Any,
    *,
    connect: ConnectFactory | None = None,
) -> dict[str, Any] | None:
    """Idempotently repair/inspect the CRM action for one delivered outbox row."""

    item_id = _positive_int(outbox_id, "outbox_id")
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                  FROM funnel_workspace_outbox
                 WHERE id = %s
                 FOR UPDATE
                """,
                (item_id,),
            )
            outbox = _record(cur.fetchone())
            if outbox is None:
                raise WorkspaceNotFoundError(
                    "Исходящее сообщение не найдено.",
                    details={"outbox_id": item_id},
                )
            return _enqueue_crm_stage_action_cursor(cur, outbox)


def _backfill_sent_crm_actions_cursor(cur: Any, limit: int) -> int:
    """Bounded repair for deliveries committed by an older gateway version."""

    cur.execute(
        """
        SELECT o.*
          FROM funnel_workspace_outbox o
         WHERE o.delivery_status = 'sent'
           AND (
                (
                    (
                        COALESCE(btrim(o.payload ->> 'asset'), '')
                            IN ('terms', 'form')
                        OR (
                            o.author_type = 'agent'
                            AND lower(COALESCE(
                                o.payload ->> 'escalate_after_delivery',
                                'false'
                            )) = 'true'
                        )
                    )
                    AND NOT EXISTS (
                        SELECT 1
                          FROM funnel_workspace_crm_actions effects
                         WHERE effects.outbox_id = o.id
                           AND effects.action_type = 'delivery_effects'
                    )
                )
                OR (
                    COALESCE(btrim(o.payload ->> 'stage_move'), '') <> ''
                    AND NOT EXISTS (
                        SELECT 1
                          FROM funnel_workspace_crm_actions stage_action
                         WHERE stage_action.outbox_id = o.id
                           AND stage_action.action_type = 'move_stage'
                    )
                )
           )
         ORDER BY o.id
         FOR UPDATE OF o SKIP LOCKED
         LIMIT %s
        """,
        (limit,),
    )
    rows = [dict(row) for row in cur.fetchall()]
    for outbox in rows:
        _enqueue_delivery_effect_action_cursor(cur, outbox)
        _enqueue_crm_stage_action_cursor(cur, outbox)
    return len(rows)


def _recover_crm_actions_cursor(cur: Any, timestamp: datetime) -> int:
    cur.execute(
        """
        UPDATE funnel_workspace_crm_actions
           SET processing_status = CASE
                   WHEN attempts >= max_attempts THEN 'dead_letter'
                   ELSE 'retry'
               END,
               available_at = %s,
               locked_at = NULL,
               locked_until = NULL,
               locked_by = NULL,
               last_error = COALESCE(
                   last_error,
                   'CRM worker lease expired; target stage will be read before retry.'
               ),
               completed_at = CASE
                   WHEN attempts >= max_attempts THEN %s
                   ELSE NULL
               END,
               updated_at = %s
         WHERE processing_status = 'leased'
           AND locked_until IS NOT NULL
           AND locked_until <= %s
        """,
        (timestamp, timestamp, timestamp, timestamp),
    )
    recovered = int(cur.rowcount or 0)
    cur.execute(
        """
        UPDATE funnel_workspace_crm_actions
           SET processing_status = 'dead_letter',
               last_error = COALESCE(
                   last_error,
                   'CRM action reached its bounded attempt limit.'
               ),
               completed_at = COALESCE(completed_at, %s),
               updated_at = %s
         WHERE processing_status IN ('pending', 'retry')
           AND attempts >= max_attempts
        """,
        (timestamp, timestamp),
    )
    return recovered + int(cur.rowcount or 0)


def recover_crm_actions(
    *,
    now: datetime | None = None,
    backfill_limit: int = 500,
    connect: ConnectFactory | None = None,
) -> int:
    """Recover expired leases and older sent outbox rows, both in bounded batches."""

    timestamp = _now(now)
    limit = min(2000, max(1, int(backfill_limit or 500)))
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            recovered = _recover_crm_actions_cursor(cur, timestamp)
            missing_deals = _backfill_missing_deal_actions_cursor(cur, limit)
            post_delivery = _backfill_sent_crm_actions_cursor(cur, limit)
            return recovered + missing_deals + post_delivery


def claim_crm_actions(
    *,
    worker_id: str,
    limit: int = 10,
    lease_seconds: int = 600,
    now: datetime | None = None,
    connect: ConnectFactory | None = None,
) -> list[dict[str, Any]]:
    worker = _required_text(worker_id, "worker_id", 200)
    limit = min(100, max(1, int(limit or 10)))
    lease_seconds = min(900, max(30, int(lease_seconds or 600)))
    timestamp = _now(now)
    locked_until = timestamp + timedelta(seconds=lease_seconds)
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            _recover_crm_actions_cursor(cur, timestamp)
            # Repair both pre-queue CRM links and the narrow deployment window
            # from the older synchronous post-delivery handlers.
            _backfill_missing_deal_actions_cursor(cur, limit)
            _backfill_sent_crm_actions_cursor(cur, limit)
            cur.execute(
                """
                WITH candidates AS (
                    SELECT a.id
                      FROM funnel_workspace_crm_actions a
                     WHERE a.processing_status IN ('pending', 'retry')
                       AND a.available_at <= %s
                       AND a.attempts < a.max_attempts
                       AND (
                            a.action_type = 'delivery_effects'
                            OR NOT EXISTS (
                                SELECT 1
                                  FROM funnel_workspace_crm_actions earlier
                                 WHERE earlier.conversation_id = a.conversation_id
                                   AND earlier.id < a.id
                                   AND earlier.action_type IN (
                                       'ensure_deal', 'move_stage'
                                   )
                                   AND earlier.processing_status IN (
                                       'pending', 'leased', 'retry'
                                   )
                            )
                       )
                     ORDER BY
                           CASE WHEN a.action_type = 'delivery_effects' THEN 0 ELSE 1 END,
                           a.available_at,
                           a.id
                     FOR UPDATE OF a SKIP LOCKED
                     LIMIT %s
                )
                UPDATE funnel_workspace_crm_actions a
                   SET processing_status = 'leased',
                       attempts = attempts + 1,
                       locked_at = %s,
                       locked_until = %s,
                       locked_by = %s,
                       updated_at = %s
                  FROM candidates c
                 WHERE a.id = c.id
             RETURNING a.*
                """,
                (
                    timestamp,
                    limit,
                    timestamp,
                    locked_until,
                    worker,
                    timestamp,
                ),
            )
            return [dict(row) for row in cur.fetchall()]


def complete_crm_action(
    action_id: Any,
    *,
    worker_id: str,
    result: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    connect: ConnectFactory | None = None,
) -> dict[str, Any]:
    item_id = _positive_int(action_id, "action_id")
    worker = _required_text(worker_id, "worker_id", 200)
    timestamp = _now(now)
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE funnel_workspace_crm_actions
                   SET processing_status = 'done',
                       locked_at = NULL,
                       locked_until = NULL,
                       locked_by = NULL,
                       last_error = NULL,
                       result = %s,
                       completed_at = %s,
                       updated_at = %s
                 WHERE id = %s
                   AND processing_status = 'leased'
                   AND locked_by = %s
             RETURNING *
                """,
                (
                    Jsonb(dict(result or {})),
                    timestamp,
                    timestamp,
                    item_id,
                    worker,
                ),
            )
            action = _record(cur.fetchone())
            if action is None:
                raise WorkspaceConflictError(
                    "CRM-действие больше не принадлежит этому обработчику.",
                    details={"action_id": item_id},
                )
            if action.get("action_type") == "move_stage":
                cur.execute(
                    """
                    UPDATE funnel_workspace_conversations
                       SET stage_id = %s
                     WHERE id = %s
                    """,
                    (action["target_stage"], action["conversation_id"]),
                )
            return action


def retry_crm_action(
    action_id: Any,
    *,
    worker_id: str,
    error: Any,
    delay_seconds: int,
    now: datetime | None = None,
    connect: ConnectFactory | None = None,
) -> dict[str, Any]:
    item_id = _positive_int(action_id, "action_id")
    worker = _required_text(worker_id, "worker_id", 200)
    clean_error = _required_text(error, "error", 4000)
    delay = min(86_400, max(1, int(delay_seconds or 1)))
    timestamp = _now(now)
    available_at = timestamp + timedelta(seconds=delay)
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE funnel_workspace_crm_actions
                   SET processing_status = CASE
                           WHEN attempts >= max_attempts THEN 'dead_letter'
                           ELSE 'retry'
                       END,
                       available_at = %s,
                       locked_at = NULL,
                       locked_until = NULL,
                       locked_by = NULL,
                       last_error = %s,
                       completed_at = CASE
                           WHEN attempts >= max_attempts THEN %s
                           ELSE NULL
                       END,
                       updated_at = %s
                 WHERE id = %s
                   AND processing_status = 'leased'
                   AND locked_by = %s
             RETURNING *
                """,
                (
                    available_at,
                    clean_error,
                    timestamp,
                    timestamp,
                    item_id,
                    worker,
                ),
            )
            action = _record(cur.fetchone())
            if action is None:
                raise WorkspaceConflictError(
                    "CRM-действие больше не принадлежит этому обработчику.",
                    details={"action_id": item_id},
                )
            return action


def list_crm_actions(
    *,
    conversation_id: Any | None = None,
    processing_status: str | None = None,
    limit: int = 100,
    connect: ConnectFactory | None = None,
) -> list[dict[str, Any]]:
    """Inspectable queue view for smoke checks and operational diagnostics."""

    clauses: list[str] = []
    params: list[Any] = []
    if conversation_id is not None:
        clauses.append("conversation_id = %s")
        params.append(_positive_int(conversation_id, "conversation_id"))
    if processing_status is not None:
        clean_status = str(processing_status or "").strip().lower()
        allowed = {"pending", "leased", "retry", "done", "dead_letter"}
        if clean_status not in allowed:
            raise WorkspaceValidationError(
                "Неизвестный статус CRM-действия.",
                details={"processing_status": clean_status},
            )
        clauses.append("processing_status = %s")
        params.append(clean_status)
    row_limit = min(500, max(1, int(limit or 100)))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(row_limit)
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT *
                  FROM funnel_workspace_crm_actions
                  {where}
                 ORDER BY id DESC
                 LIMIT %s
                """,
                tuple(params),
            )
            return [dict(row) for row in cur.fetchall()]


def list_pending_outbox(
    *,
    limit: int = 100,
    connect: ConnectFactory | None = None,
) -> list[dict[str, Any]]:
    limit = min(500, max(1, int(limit or 100)))
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                  FROM funnel_workspace_outbox
                 WHERE delivery_status IN ('pending', 'leased', 'sending', 'unknown', 'failed')
                 ORDER BY id
                 LIMIT %s
                """,
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]


def release_expired_human_leases(
    *,
    limit: int = 100,
    now: datetime | None = None,
    connect: ConnectFactory | None = None,
) -> list[dict[str, Any]]:
    """Return due two-minute human leases to AI and audit each transition."""

    limit = min(500, max(1, int(limit or 100)))
    timestamp = _now(now)
    released: list[dict[str, Any]] = []
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                  FROM funnel_workspace_conversations
                 WHERE control_mode = 'human'
                   AND resume_at IS NOT NULL
                   AND resume_at <= %s
                   AND status IN ('new', 'open', 'waiting')
                 ORDER BY resume_at, id
                 FOR UPDATE SKIP LOCKED
                 LIMIT %s
                """,
                (timestamp, limit),
            )
            rows = [dict(row) for row in cur.fetchall()]
            for row in rows:
                current_version = int(row["state_version"])
                next_version = current_version + 1
                deadline = row.get("reply_deadline_at")
                if deadline and _now(deadline) <= timestamp:
                    next_mode = "paused"
                    next_status = "expired"
                    reason = "Окно ответа Telegram истекло во время паузы оператора."
                else:
                    next_mode = "ai"
                    next_status = row["status"]
                    reason = "Двухминутная пауза оператора истекла; управление возвращено ИИ."
                cur.execute(
                    """
                    UPDATE funnel_workspace_conversations
                       SET control_mode = %s,
                           status = %s,
                           resume_at = NULL,
                           assigned_to = NULL,
                           state_version = %s,
                           updated_at = %s
                     WHERE id = %s
                 RETURNING *
                    """,
                    (next_mode, next_status, next_version, timestamp, row["id"]),
                )
                updated = dict(cur.fetchone())
                _insert_control_event(
                    cur,
                    conversation_id=int(row["id"]),
                    from_mode="human",
                    to_mode=next_mode,
                    actor_type="system",
                    actor_name="Система",
                    reason=reason,
                    from_version=current_version,
                    to_version=next_version,
                )
                if next_mode == "ai":
                    trigger_message_id = _latest_unanswered_client_message_id(
                        cur,
                        int(row["id"]),
                    )
                    if trigger_message_id is not None:
                        _schedule_ai_job_cursor(
                            cur,
                            conversation_id=int(row["id"]),
                            trigger_message_id=trigger_message_id,
                            expected_version=next_version,
                            available_at=timestamp
                            + timedelta(milliseconds=ai_debounce_milliseconds()),
                        )
                released.append(updated)
    return released


def expire_reply_windows(
    *,
    limit: int = 500,
    now: datetime | None = None,
    connect: ConnectFactory | None = None,
) -> list[dict[str, Any]]:
    limit = min(2000, max(1, int(limit or 500)))
    timestamp = _now(now)
    expired: list[dict[str, Any]] = []
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                  FROM funnel_workspace_conversations
                 WHERE status IN ('new', 'open', 'waiting')
                   AND reply_deadline_at IS NOT NULL
                   AND reply_deadline_at <= %s
                 ORDER BY reply_deadline_at, id
                 FOR UPDATE SKIP LOCKED
                 LIMIT %s
                """,
                (timestamp, limit),
            )
            rows = [dict(row) for row in cur.fetchall()]
            for row in rows:
                current_version = int(row["state_version"])
                next_version = current_version + 1
                _cancel_queued_ai(
                    cur,
                    int(row["id"]),
                    "Окно ответа Telegram истекло.",
                )
                cur.execute(
                    """
                    UPDATE funnel_workspace_conversations
                       SET status = 'expired',
                           control_mode = 'paused',
                           resume_at = NULL,
                           assigned_to = NULL,
                           state_version = %s,
                           updated_at = %s
                     WHERE id = %s
                 RETURNING *
                    """,
                    (next_version, timestamp, row["id"]),
                )
                updated = dict(cur.fetchone())
                if row["control_mode"] != "paused":
                    _insert_control_event(
                        cur,
                        conversation_id=int(row["id"]),
                        from_mode=row["control_mode"],
                        to_mode="paused",
                        actor_type="system",
                        actor_name="Система",
                        reason="Окно ответа Telegram истекло.",
                        from_version=current_version,
                        to_version=next_version,
                    )
                expired.append(updated)
    return expired


def list_control_events(
    conversation_id: Any,
    *,
    limit: int = 100,
    connect: ConnectFactory | None = None,
) -> list[dict[str, Any]]:
    item_id = _positive_int(conversation_id, "conversation_id")
    limit = min(500, max(1, int(limit or 100)))
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                  FROM funnel_workspace_control_events
                 WHERE conversation_id = %s
                 ORDER BY id DESC
                 LIMIT %s
                """,
                (item_id, limit),
            )
            return [dict(row) for row in cur.fetchall()]


def retention_cleanup(
    *,
    days: int | None = None,
    batch_size: int = 1000,
    max_batches: int = 50,
    now: datetime | None = None,
    connect: ConnectFactory | None = None,
) -> dict[str, int]:
    """Удалить историю старше срока хранения, не тронув живую работу.

    Чистка идёт партиями: одна партия за запуск не разгребает накопленное, поэтому
    цикл повторяется, пока партия не окажется неполной, но не больше ``max_batches``
    раз — чтобы ночная чистка не держала таблицы часами. Сообщение, на которое ещё
    ссылается неотправленная очередь или незавершённое CRM-действие, не удаляется:
    внешние ключи стоят на ``ON DELETE CASCADE`` и унесли бы эту работу с собой.
    """
    keep_days = retention_days() if days is None else min(90, max(7, int(days)))
    batch_size = min(10_000, max(1, int(batch_size or 1000)))
    max_batches = min(1000, max(1, int(max_batches or 50)))
    cutoff = _now(now) - timedelta(days=keep_days)
    deleted: dict[str, int] = {}
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            statements = (
                (
                    "updates",
                    """
                    WITH doomed AS (
                        SELECT id
                          FROM funnel_workspace_updates
                         WHERE received_at < %s
                           AND processing_status IN ('done', 'dead_letter')
                         ORDER BY id
                         LIMIT %s
                    )
                    DELETE FROM funnel_workspace_updates
                     WHERE id IN (SELECT id FROM doomed)
                    """,
                ),
                (
                    "ai_jobs",
                    """
                    WITH doomed AS (
                        SELECT id
                          FROM funnel_workspace_ai_jobs
                         WHERE created_at < %s
                           AND processing_status IN ('done', 'failed', 'cancelled')
                         ORDER BY id
                         LIMIT %s
                    )
                    DELETE FROM funnel_workspace_ai_jobs
                     WHERE id IN (SELECT id FROM doomed)
                    """,
                ),
                (
                    "control_events",
                    """
                    WITH doomed AS (
                        SELECT id
                          FROM funnel_workspace_control_events
                         WHERE created_at < %s
                         ORDER BY id
                         LIMIT %s
                    )
                    DELETE FROM funnel_workspace_control_events
                     WHERE id IN (SELECT id FROM doomed)
                    """,
                ),
            )
            commit = getattr(conn, "commit", None)
            for name, sql in statements:
                removed_total = 0
                for _ in range(max_batches):
                    cur.execute(sql, (cutoff, batch_size))
                    removed = int(cur.rowcount or 0)
                    removed_total += removed
                    # Партия имеет смысл только как отдельная транзакция: иначе
                    # блокировки держатся до конца всей чистки.
                    if callable(commit):
                        commit()
                    if removed < batch_size:
                        break
                deleted[name] = removed_total

            # Сообщения удаляются последними: очереди выше уже освободили свои
            # завершённые строки, а те, что остались живыми, держат своё сообщение.
            touched: set[int] = set()
            removed_total = 0
            for _ in range(max_batches):
                cur.execute(
                    """
                    WITH doomed AS (
                        SELECT m.id
                          FROM funnel_workspace_messages m
                         WHERE m.occurred_at < %s
                           AND NOT EXISTS (
                                   SELECT 1
                                     FROM funnel_workspace_outbox o
                                    WHERE o.message_id = m.id
                                      AND o.delivery_status NOT IN ('sent', 'cancelled')
                               )
                           AND NOT EXISTS (
                                   SELECT 1
                                     FROM funnel_workspace_crm_actions a
                                    WHERE a.message_id = m.id
                                      AND a.processing_status IN ('pending', 'leased', 'retry')
                               )
                           AND NOT EXISTS (
                                   SELECT 1
                                     FROM funnel_workspace_ai_jobs j
                                    WHERE j.trigger_message_id = m.id
                                      AND j.processing_status IN ('pending', 'leased')
                               )
                         ORDER BY m.id
                         LIMIT %s
                    )
                    DELETE FROM funnel_workspace_messages
                     WHERE id IN (SELECT id FROM doomed)
                 RETURNING conversation_id
                    """,
                    (cutoff, batch_size),
                )
                rows = cur.fetchall()
                removed = len(rows)
                removed_total += removed
                touched.update(int(dict(row)["conversation_id"]) for row in rows)
                if callable(commit):
                    commit()
                if removed < batch_size:
                    break
            deleted["messages"] = removed_total

            if touched:
                # Превью последнего сообщения, счётчик непрочитанного и метка
                # прочтения остались бы от удалённой истории и разъехались бы со
                # списком диалогов.
                cur.execute(
                    """
                    UPDATE funnel_workspace_conversations c
                       SET last_message_id = n.id,
                           last_message_at = n.occurred_at,
                           last_message_text = n.text,
                           last_author_type = n.author_type,
                           last_read_message_id = LEAST(
                               c.last_read_message_id, COALESCE(n.id, 0)
                           ),
                           unread_count = (
                               SELECT count(*)
                                 FROM funnel_workspace_messages m
                                WHERE m.conversation_id = c.id
                                  AND m.author_type = 'client'
                                  AND m.id > LEAST(
                                          c.last_read_message_id, COALESCE(n.id, 0)
                                      )
                           ),
                           updated_at = %s
                      FROM unnest(%s::bigint[]) AS t(conversation_id)
                      LEFT JOIN LATERAL (
                               SELECT m.id, m.occurred_at, m.text, m.author_type
                                 FROM funnel_workspace_messages m
                                WHERE m.conversation_id = t.conversation_id
                                ORDER BY m.id DESC
                                LIMIT 1
                           ) n ON true
                     WHERE c.id = t.conversation_id
                    """,
                    (_now(now), sorted(touched)),
                )
    deleted["retention_days"] = keep_days
    return deleted


def message_export_rows(
    *,
    q: str = "",
    status: str = "",
    stage: str = "",
    source: str = "",
    author_type: str = "",
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = 50_000,
    connect: ConnectFactory | None = None,
) -> list[dict[str, Any]]:
    clean_status = str(status or "").strip().lower()
    clean_stage = str(stage or "").strip()[:200]
    clean_author = str(author_type or "").strip().lower()
    clean_source = str(source or "").strip()[:100]
    clean_q = str(q or "").strip()[:200]
    if clean_status and clean_status not in VALID_STATUSES:
        raise WorkspaceValidationError("Неизвестный статус.", details={"status": clean_status})
    if clean_author and clean_author not in VALID_AUTHOR_TYPES:
        raise WorkspaceValidationError(
            "Неизвестный тип автора.",
            details={"author_type": clean_author},
        )
    limit = min(100_000, max(1, int(limit or 50_000)))
    clauses = ["TRUE"]
    params: list[Any] = []
    if clean_status:
        clauses.append("c.status = %s")
        params.append(clean_status)
    if clean_stage:
        clauses.append("c.stage_id = %s")
        params.append(clean_stage)
    if clean_source:
        clauses.append("c.source_key = %s")
        params.append(clean_source)
    if clean_author:
        clauses.append("m.author_type = %s")
        params.append(clean_author)
    if date_from:
        clauses.append("m.occurred_at >= %s")
        params.append(_now(date_from))
    if date_to:
        clauses.append("m.occurred_at < %s")
        params.append(_now(date_to))
    if clean_q:
        clauses.append(
            """(
                m.text ILIKE %s
                OR COALESCE(c.display_name, '') ILIKE %s
                OR COALESCE(c.username, '') ILIKE %s
                OR c.external_chat_id ILIKE %s
            )"""
        )
        params.extend([f"%{clean_q}%"] * 4)
    params.append(limit)
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT m.id AS message_id, m.occurred_at, m.author_type,
                       m.author_name, m.direction, m.text, m.delivery_status,
                       c.id AS conversation_id, c.source_key, c.external_chat_id,
                       c.external_user_id, c.username, c.display_name, c.deal_id,
                       c.funnel_id, c.stage_id, c.status, c.control_mode
                  FROM funnel_workspace_messages m
                  JOIN funnel_workspace_conversations c ON c.id = m.conversation_id
                 WHERE {' AND '.join(clauses)}
                 ORDER BY m.id
                 LIMIT %s
                """,
                tuple(params),
            )
            return [dict(row) for row in cur.fetchall()]
