from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterator, Mapping
from uuid import uuid4

from psycopg.types.json import Jsonb

from shared.db import connect as pg_connect


# Момент, с которого клиент ждёт ответа: время самого раннего его сообщения, после
# которого ни агент, ни оператор ничего не отправили. NULL — ответ уже дан. Считается
# запросом, а не хранится в колонке: любое расхождение с перепиской здесь невозможно.
# Что считается НАШИМ ОТВЕТОМ клиенту. Ответом не является то, чего клиент не видел:
# отменённая и неудавшаяся отправка, а также сообщение, которое мы сами удалили. Из
# последнего следует требование владельца: удалили свой ответ — обращение возвращается
# в прежний статус, как будто мы ещё не отвечали.
ANSWER_IS_REAL_SQL = """(
    answer.author_type IN ('agent', 'operator')
    AND answer.delivery_status NOT IN ('cancelled', 'failed')
    AND (answer.metadata ->> 'telegram_deleted') IS DISTINCT FROM 'true'
)"""

AWAITING_REPLY_SQL = f"""(
    SELECT min(client.occurred_at)
      FROM funnel_workspace_messages client
     WHERE client.conversation_id = c.id
       AND client.author_type = 'client'
       AND client.id > COALESCE((
               SELECT max(answer.id)
                 FROM funnel_workspace_messages answer
                WHERE answer.conversation_id = c.id
                  AND {ANSWER_IS_REAL_SQL}
           ), 0)
)"""

# Отвечали ли мы в этом диалоге хоть раз.
HAS_ANSWER_SQL = f"""EXISTS (
    SELECT 1
      FROM funnel_workspace_messages answer
     WHERE answer.conversation_id = c.id
       AND {ANSWER_IS_REAL_SQL}
)"""

# Рабочие состояния обращения. Считаются по переписке, а не хранятся: любое хранимое
# поле разъедется с реальностью при первом же сообщении, пришедшем мимо интерфейса.
# Статусов ровно три (владелец, 27.07.2026): ждёт ответа клиент — ждём ответа мы — срочно.
# «Новый клиент» отсюда убран: новизна — это ЭТАП воронки (C16:NEW), и как статус она
# дублировала этап на каждой карточке, ничего не добавляя. Клиент, которому мы ещё ни разу
# не ответили, — это тот же «Клиент ждёт ответа», просто с самого начала переписки.
WORK_STATE_CLIENT_WAITING = "client_waiting"  # клиент ждёт нашего ответа
WORK_STATE_WAITING_CLIENT = "waiting_client"  # последнее слово за нами
WORK_STATE_URGENT = "urgent"           # клиент ждёт дольше порога (дополняет первый)

# Ушедший статус: старые ссылки, сохранённые фильтры и вызовы инструментов агента с ним
# приходят до сих пор — читаем как «клиент ждёт ответа», а не отвечаем ошибкой.
WORK_STATE_NEW_LEGACY = "new_client"

VALID_WORK_STATES = frozenset({
    WORK_STATE_CLIENT_WAITING,
    WORK_STATE_WAITING_CLIENT,
    WORK_STATE_URGENT,
})

WORK_STATE_LABELS = {
    WORK_STATE_CLIENT_WAITING: "Клиент ждёт ответа",
    WORK_STATE_WAITING_CLIENT: "Ждём ответа от клиента",
    WORK_STATE_URGENT: "Очень срочно",
}

# Порядок разбора очереди (владелец, 27.07.2026). Меньше число — выше в списке.
# Срочность важнее: просроченный вопрос не должен уезжать вниз под свежими.
WORK_STATE_PRIORITY = {
    WORK_STATE_URGENT: 1,
    WORK_STATE_CLIENT_WAITING: 2,
    WORK_STATE_WAITING_CLIENT: 3,
}

# Кто ведёт разговор. Третий бейдж обращения — он про исполнителя, а не про очередь хода.
CONTROL_MODE_LABELS = {
    "ai": "ИИ управляет",
    "human": "Человек управляет",
    "paused": "Ответы приостановлены",
}

VALID_STATUSES = frozenset({"new", "open", "waiting", "closed", "spam", "expired"})
VALID_CONTROL_MODES = frozenset({"ai", "human", "paused"})
VALID_AUTHOR_TYPES = frozenset({"client", "agent", "operator", "system"})
VALID_DELIVERY_RESULTS = frozenset({"sent", "failed", "unknown", "cancelled"})
# Статусы, при которых наш ответ ТОЧНО не дошёл до клиента: `failed` — Telegram отказал,
# `cancelled` — отправка отменена до вызова. `unknown` сюда не входит намеренно: там
# Telegram мог принять сообщение до сетевого таймаута.
UNDELIVERED_STATUSES = frozenset({"failed", "cancelled"})
VALID_UPDATE_LANES = frozenset({"business", "bot"})
ACTIVE_STATUSES = frozenset({"new", "open", "waiting"})
DEFAULT_SOURCE_KEY = "telegram"
MAX_MESSAGE_LENGTH = 4096
#: Telegram обрезает подпись к документу на 1024 символах — отправлять больше нечестно.
MAX_CAPTION_LENGTH = 1024
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


def urgent_after_minutes() -> int:
    """Сколько минут вопрос клиента может висеть без ответа, прежде чем станет срочным."""
    return _bounded_env_int("FUNNEL_WORKSPACE_URGENT_AFTER_MINUTES", 10, 1, 1440)


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


def is_permanent_hold(conversation: Mapping[str, Any]) -> bool:
    """Диалог забран человеком НАСОВСЕМ.

    Признак — режим человека без срока возврата: возврат аренды смотрит только на строки
    с непустым ``resume_at``, поэтому пустой срок и означает «ИИ сюда не вернётся сам».
    """
    return (
        str(conversation.get("control_mode") or "") == "human"
        and conversation.get("resume_at") in (None, "")
    )


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


def enqueue_operator_stage_change(
    conversation_id: Any,
    *,
    target_stage: str,
    expected_version: Any = None,
    operator_name: str | None = None,
    now: datetime | None = None,
    connect: ConnectFactory | None = None,
) -> dict[str, Any]:
    """Перевести сделку на другой этап по действию оператора.

    Сайт и Битрикс должны быть одним целым, поэтому этап меняется не «где-то потом»:
    в диалоге он виден сразу, а в CRM его переставляет та же durable-очередь, что и
    после доставки сообщения. Если Битрикс откажет, ежеминутная синхронизация вернёт
    в интерфейс настоящее значение — расхождение не может остаться незамеченным.
    """
    stage = _required_text(target_stage, "target_stage", 200)
    timestamp = _now(now)
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            row = _load_conversation_locked(cur, conversation_id)
            if expected_version is not None:
                _require_version(row, expected_version)
            cur.execute(
                """
                SELECT id
                  FROM funnel_workspace_messages
                 WHERE conversation_id = %s
                 ORDER BY id DESC
                 LIMIT 1
                """,
                (row["id"],),
            )
            last_message = _record(cur.fetchone())
            if last_message is None:
                raise WorkspaceValidationError(
                    "В диалоге ещё нет сообщений — этап менять не от чего.",
                    details={"conversation_id": row["id"]},
                )
            cur.execute(
                """
                INSERT INTO funnel_workspace_crm_actions (
                    conversation_id, message_id, action_type,
                    target_stage, payload, idempotency_key
                )
                VALUES (%s, %s, 'move_stage', %s, %s, %s)
             RETURNING *
                """,
                (
                    row["id"],
                    last_message["id"],
                    stage,
                    Jsonb(
                        {
                            "trigger": "operator",
                            "operator_name": _clean_optional(operator_name, 200),
                            "previous_stage": _clean_optional(row.get("stage_id"), 200),
                        }
                    ),
                    f"crm-stage:operator:{row['id']}:{stage}:{uuid4().hex}",
                ),
            )
            action = dict(cur.fetchone())
            cur.execute(
                """
                UPDATE funnel_workspace_conversations
                   SET stage_id = %s,
                       updated_at = %s
                 WHERE id = %s
             RETURNING *
                """,
                (stage, timestamp, row["id"]),
            )
            return {"conversation": dict(cur.fetchone()), "crm_action": action}


def _message_with_chat(cur: Any, message_id: int) -> dict[str, Any]:
    """Сообщение вместе с адресом чата: без него нечего править в Telegram."""
    cur.execute(
        """
        SELECT m.*, c.external_chat_id, c.business_connection_id, c.source_key,
               c.last_message_id AS conversation_last_message_id
          FROM funnel_workspace_messages m
          JOIN funnel_workspace_conversations c ON c.id = m.conversation_id
         WHERE m.id = %s
         FOR UPDATE OF m
        """,
        (message_id,),
    )
    row = _record(cur.fetchone())
    if row is None:
        raise WorkspaceNotFoundError(
            "Сообщение не найдено.",
            details={"message_id": message_id},
        )
    return row


def message_delivery_target(
    message_id: Any,
    *,
    connect: ConnectFactory | None = None,
) -> dict[str, Any]:
    """Куда и что менять в Telegram — до того, как трогать журнал."""
    item_id = _positive_int(message_id, "message_id")
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            row = _message_with_chat(cur, item_id)
    return {
        "message_id": item_id,
        "conversation_id": int(row["conversation_id"]),
        "external_chat_id": row["external_chat_id"],
        "business_connection_id": row["business_connection_id"],
        "provider_message_id": row.get("provider_message_id"),
        "author_type": row.get("author_type"),
        "delivery_status": row.get("delivery_status"),
    }


def edit_outgoing_message(
    message_id: Any,
    *,
    text: Any,
    actor_name: str | None = None,
    now: datetime | None = None,
    connect: ConnectFactory | None = None,
) -> dict[str, Any]:
    """Изменить наш уже отправленный ответ.

    Править можно только своё и только доставленное: сообщение клиента — его слова, а
    ещё не ушедший ответ надо отменять, а не редактировать, иначе клиент увидит текст,
    которого оператор уже не писал.
    """
    item_id = _positive_int(message_id, "message_id")
    clean_text = _required_text(text, "text", MAX_MESSAGE_LENGTH)
    timestamp = _now(now)
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            row = _message_with_chat(cur, item_id)
            if str(row.get("author_type")) not in {"agent", "operator"}:
                raise WorkspaceValidationError(
                    "Редактировать можно только наши сообщения.",
                    details={"author_type": row.get("author_type")},
                )
            if str(row.get("delivery_status")) != "sent":
                raise WorkspaceControlError(
                    "Сообщение ещё не доставлено — его нельзя отредактировать.",
                    details={"delivery_status": row.get("delivery_status")},
                )
            cur.execute(
                """
                UPDATE funnel_workspace_messages
                   SET text = %s,
                       metadata = metadata || %s
                 WHERE id = %s
             RETURNING *
                """,
                (
                    clean_text,
                    Jsonb(
                        {
                            "edited_at": timestamp.isoformat(),
                            "edited_by": _clean_optional(actor_name, 200),
                        }
                    ),
                    item_id,
                ),
            )
            updated = dict(cur.fetchone())
            if int(row.get("conversation_last_message_id") or 0) == item_id:
                cur.execute(
                    """
                    UPDATE funnel_workspace_conversations
                       SET last_message_text = %s,
                           updated_at = %s
                     WHERE id = %s
                    """,
                    (clean_text[:1000], timestamp, row["conversation_id"]),
                )
    return {
        "message": updated,
        "conversation_id": int(row["conversation_id"]),
        "external_chat_id": row["external_chat_id"],
        "business_connection_id": row["business_connection_id"],
        "provider_message_id": row.get("provider_message_id"),
        "text": clean_text,
    }


def delete_message_for_everyone(
    message_id: Any,
    *,
    actor_name: str | None = None,
    now: datetime | None = None,
    connect: ConnectFactory | None = None,
) -> dict[str, Any]:
    """Пометить сообщение удалённым тем же способом, что и удаление клиентом.

    Формат надгробия один на всю систему: иначе в одной переписке появятся два разных
    вида удалённых сообщений и оператор перестанет понимать, что произошло.
    """
    item_id = _positive_int(message_id, "message_id")
    timestamp = _now(now)
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            row = _message_with_chat(cur, item_id)
            cur.execute(
                """
                UPDATE funnel_workspace_messages
                   SET text = '[Сообщение удалено]',
                       metadata = metadata || %s
                 WHERE id = %s
             RETURNING *
                """,
                (
                    Jsonb(
                        {
                            "telegram_deleted": True,
                            "telegram_deleted_at": timestamp.isoformat(),
                            "deleted_by": _clean_optional(actor_name, 200),
                        }
                    ),
                    item_id,
                ),
            )
            updated = dict(cur.fetchone())
            if int(row.get("conversation_last_message_id") or 0) == item_id:
                cur.execute(
                    """
                    UPDATE funnel_workspace_conversations
                       SET last_message_text = '[Сообщение удалено]',
                           updated_at = %s
                     WHERE id = %s
                    """,
                    (timestamp, row["conversation_id"]),
                )
    return {
        "message": updated,
        "conversation_id": int(row["conversation_id"]),
        "external_chat_id": row["external_chat_id"],
        "business_connection_id": row["business_connection_id"],
        "provider_message_id": row.get("provider_message_id"),
        "author_type": row.get("author_type"),
    }


def purge_undelivered_message(
    message_id: Any,
    *,
    actor_name: str | None = None,
    now: datetime | None = None,
    connect: ConnectFactory | None = None,
) -> dict[str, Any]:
    """Убрать из журнала наш ответ, который до клиента так и не дошёл.

    Надгробие «[Сообщение удалено]» описывает то, что у клиента БЫЛО и исчезло. Для
    ответа со статусом `failed`/`cancelled` это неправда: клиент его никогда не видел, а
    в переписке оператора остаётся вечный след от несуществующего сообщения (живой
    случай 27.07.2026, диалог 69 — три ответа с `PEER_ID_INVALID`). Такую запись
    удаляем совсем.

    Доставленное и `unknown` не трогаем: у клиента текст остался (или мог остаться), и
    вычистить его у себя значит соврать оператору о состоянии переписки.
    """
    item_id = _positive_int(message_id, "message_id")
    timestamp = _now(now)
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            row = _message_with_chat(cur, item_id)
            if str(row.get("author_type")) not in {"agent", "operator"}:
                raise WorkspaceValidationError(
                    "Убрать из журнала можно только наше сообщение.",
                    details={"author_type": row.get("author_type")},
                )
            status = str(row.get("delivery_status") or "").strip().lower()
            if status not in UNDELIVERED_STATUSES:
                raise WorkspaceControlError(
                    "Сообщение дошло до клиента или могло дойти — из журнала "
                    "его убирать нельзя, только удалять у всех.",
                    details={"delivery_status": status},
                )
            cur.execute(
                """
                SELECT count(*) AS live
                  FROM funnel_workspace_outbox
                 WHERE message_id = %s
                   AND delivery_status IN ('pending', 'leased', 'sending')
                """,
                (item_id,),
            )
            live = int(dict(_record(cur.fetchone()) or {}).get("live") or 0)
            if live:
                raise WorkspaceControlError(
                    "Сообщение ещё в очереди отправки: сначала дождитесь её "
                    "завершения, иначе клиент получит текст, которого у нас уже нет.",
                    details={"message_id": item_id},
                )
            conversation_id = int(row["conversation_id"])
            # Очередь отправки и последствия доставки ссылаются на сообщение с
            # ON DELETE CASCADE, задание ИИ и запись обновления — с SET NULL:
            # отдельного прохода по ним не нужно.
            cur.execute(
                "DELETE FROM funnel_workspace_messages WHERE id = %s",
                (item_id,),
            )
            conversation = _rebuild_conversation_counters_cursor(
                cur,
                conversation_id,
                timestamp,
            )
    return {
        "deleted": True,
        "purged": True,
        "message_id": item_id,
        "conversation_id": conversation_id,
        "conversation": conversation,
        "delivery_status": status,
        "actor_name": _clean_optional(actor_name, 200),
    }


def _rebuild_conversation_counters_cursor(
    cur: Any,
    conversation_id: int,
    timestamp: datetime,
) -> dict[str, Any]:
    """Пересобрать превью, метку прочтения и счётчик непрочитанного по уцелевшей переписке.

    После удаления сообщения эти поля остались бы от записи, которой больше нет, и
    список обращений разъехался бы с самой перепиской.
    """
    cur.execute(
        """
        UPDATE funnel_workspace_conversations c
           SET last_message_id = n.id,
               last_message_at = COALESCE(n.occurred_at, c.last_message_at),
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
                      AND m.id > LEAST(c.last_read_message_id, COALESCE(n.id, 0))
               ),
               updated_at = %s
          FROM (SELECT %s::bigint AS conversation_id) AS t
          LEFT JOIN LATERAL (
                   SELECT m.id, m.occurred_at, m.text, m.author_type
                     FROM funnel_workspace_messages m
                    WHERE m.conversation_id = t.conversation_id
                    ORDER BY m.id DESC
                    LIMIT 1
               ) n ON true
         WHERE c.id = t.conversation_id
     RETURNING c.*
        """,
        (timestamp, conversation_id),
    )
    updated = _record(cur.fetchone())
    if updated is None:
        raise WorkspaceNotFoundError(
            "Диалог не найден.",
            details={"conversation_id": conversation_id},
        )
    return updated


def reopen_reply_windows(
    *,
    conversation_ids: list[int] | None = None,
    now: datetime | None = None,
    connect: ConnectFactory | None = None,
) -> int:
    """Снова разрешить отвечать в диалогах, где наше окно ответа истекло.

    Это снимает НАШУ защиту, а не ограничение Telegram: там своё окно в 24 часа с
    последнего сообщения клиента, и на просроченный диалог мессенджер ответит отказом.
    Разница в том, что теперь оператор увидит настоящую причину отказа от Telegram,
    а не наш преждевременный запрет.
    """
    timestamp = _now(now)
    deadline = timestamp + timedelta(hours=reply_window_hours())
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            if conversation_ids:
                cur.execute(
                    """
                    UPDATE funnel_workspace_conversations
                       SET reply_deadline_at = %s,
                           status = CASE WHEN status IN ('closed', 'spam', 'expired')
                                         THEN 'open' ELSE status END,
                           updated_at = %s
                     WHERE id = ANY(%s)
                    """,
                    (deadline, timestamp, [int(item) for item in conversation_ids]),
                )
            else:
                cur.execute(
                    """
                    UPDATE funnel_workspace_conversations
                       SET reply_deadline_at = %s,
                           status = CASE WHEN status = 'expired' THEN 'open' ELSE status END,
                           updated_at = %s
                     WHERE status <> 'spam'
                    """,
                    (deadline, timestamp),
                )
            return int(cur.rowcount or 0)


def delete_conversation(
    conversation_id: Any,
    *,
    connect: ConnectFactory | None = None,
) -> dict[str, Any]:
    """Удалить обращение вместе со всей его историей.

    Действие необратимое, поэтому подтверждение спрашивается в интерфейсе. Сделка в
    Битриксе НЕ трогается: обращение — это наш журнал переписки, а карточка клиента
    живёт в CRM своей жизнью, и удалять её заодно никто не просил.

    Неотправленные ответы в очереди удаляются вместе с диалогом: держать их после
    удаления бессмысленно — отправлять их станет некуда.
    """
    item_id = _positive_int(conversation_id, "conversation_id")
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id, c.display_name, c.username, c.external_chat_id, c.deal_id,
                       (SELECT count(*) FROM funnel_workspace_messages m
                         WHERE m.conversation_id = c.id) AS messages
                  FROM funnel_workspace_conversations c
                 WHERE c.id = %s
                 FOR UPDATE
                """,
                (item_id,),
            )
            row = _record(cur.fetchone())
            if row is None:
                raise WorkspaceNotFoundError(
                    "Диалог не найден.",
                    details={"conversation_id": item_id},
                )
            # Очереди ссылаются на диалог с ON DELETE CASCADE, поэтому отдельного
            # прохода по ним не нужно — но обновления Telegram ссылаются мягко и
            # остались бы висеть с пустой ссылкой, их убираем явно.
            cur.execute(
                "DELETE FROM funnel_workspace_updates WHERE conversation_id = %s",
                (item_id,),
            )
            cur.execute(
                "DELETE FROM funnel_workspace_conversations WHERE id = %s",
                (item_id,),
            )
    return {
        "deleted": True,
        "conversation_id": item_id,
        "messages": int(row.get("messages") or 0),
        "client": row.get("display_name") or row.get("username") or row.get("external_chat_id"),
    }


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
    item_id = _positive_int(conversation_id, "conversation_id")
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
                (item_id,),
            )
            row = _record(cur.fetchone())
            # Завершённое действие «создать сделку» указывает на удалённую сделку, а
            # backfill пропускает диалог, пока такая запись существует. Без её удаления
            # диалог остался бы без карточки CRM навсегда.
            cur.execute(
                """
                DELETE FROM funnel_workspace_crm_actions
                 WHERE conversation_id = %s
                   AND action_type = 'ensure_deal'
                   AND processing_status IN ('done', 'dead_letter')
                """,
                (item_id,),
            )
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


def find_conversation(
    *,
    source_key: str,
    business_connection_id: str,
    external_chat_id: Any,
    connect: ConnectFactory | None = None,
) -> dict[str, Any] | None:
    """Обращение по адресу диалога у провайдера, или None."""

    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM funnel_workspace_conversations
                 WHERE source_key = %s
                   AND business_connection_id = %s
                   AND external_chat_id = %s
                """,
                (
                    _required_text(source_key, "source_key", 100),
                    str(business_connection_id or "")[:300],
                    _required_text(external_chat_id, "external_chat_id", 200),
                ),
            )
            return _record(cur.fetchone())


def count_agent_replies(
    conversation_id: Any,
    *,
    connect: ConnectFactory | None = None,
) -> int:
    """Сколько раз ИИ уже ответил клиенту в этом обращении.

    Считается по журналу, а не отдельным счётчиком: недоставленный ответ не должен
    приближать предложение позвать человека, а собственный счётчик разошёлся бы с лентой
    при первой же ошибке доставки.
    """

    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) AS replies
                  FROM funnel_workspace_messages
                 WHERE conversation_id = %s
                   AND author_type = 'agent'
                   AND direction = 'outbound'
                   AND delivery_status = 'sent'
                """,
                (_positive_int(conversation_id, "conversation_id"),),
            )
            row = _record(cur.fetchone()) or {}
    return int(row.get("replies") or 0)


def get_conversation(
    conversation_id: Any,
    *,
    connect: ConnectFactory | None = None,
) -> dict[str, Any]:
    item_id = _positive_int(conversation_id, "conversation_id")
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT c.*, s.source_type, s.display_name AS source_name,
                       {AWAITING_REPLY_SQL} AS awaiting_reply_since,
                       {HAS_ANSWER_SQL} AS has_answer
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
    state: str = "",
    urgency: str = "",
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
    # «urgency» осталась ради инструментов агента: urgent — тот же срочный статус.
    clean_state = str(state or urgency or "").strip().lower()
    if clean_state == "working":
        clean_state = WORK_STATE_WAITING_CLIENT
    if clean_state == WORK_STATE_NEW_LEGACY:
        # Убранный статус: ссылка из старой вкладки или инструмент агента со старым
        # значением обязаны показать список, а не ошибку.
        clean_state = WORK_STATE_CLIENT_WAITING
    clean_source = str(source or "").strip()[:100]
    if clean_status and clean_status not in VALID_STATUSES:
        raise WorkspaceValidationError("Неизвестный статус.", details={"status": clean_status})
    if clean_state and clean_state not in VALID_WORK_STATES:
        raise WorkspaceValidationError(
            "Неизвестный рабочий статус обращения.",
            details={"state": clean_state},
        )
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
    if clean_state:
        # Порог считается на стороне БД от текущего времени: фильтр обязан совпадать
        # с подписью в списке, а она пересчитывается у оператора каждую секунду.
        threshold = f"now() - interval '{urgent_after_minutes()} minutes'"
        # Ход за нами — независимо от того, отвечали мы в этом диалоге раньше или нет:
        # без этого клиент, которому ещё ни разу не ответили, выпадал из обоих фильтров.
        if clean_state == WORK_STATE_CLIENT_WAITING:
            clauses.append(f"{AWAITING_REPLY_SQL} IS NOT NULL")
        elif clean_state == WORK_STATE_WAITING_CLIENT:
            clauses.append(f"{AWAITING_REPLY_SQL} IS NULL")
        else:
            clauses.append(f"{AWAITING_REPLY_SQL} <= {threshold}")
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

    urgent_threshold = f"now() - interval '{urgent_after_minutes()} minutes'"
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            # Признаки считаются во вложенном запросе, а порядок задаётся снаружи: внутри
            # выражения ORDER BY PostgreSQL псевдонимы не видит, и без вложенности пришлось
            # бы повторить оба подзапроса в каждой ветке CASE.
            cur.execute(
                f"""
                SELECT * FROM (
                    SELECT c.*, s.source_type, s.display_name AS source_name,
                           {AWAITING_REPLY_SQL} AS awaiting_reply_since,
                           {HAS_ANSWER_SQL} AS has_answer,
                           count(*) OVER () AS filtered_total
                      FROM funnel_workspace_conversations c
                      JOIN funnel_workspace_sources s ON s.source_key = c.source_key
                     WHERE {' AND '.join(clauses)}
                ) ranked
                 ORDER BY
                       -- Очередь разбора владельца: очень срочно → новый клиент →
                       -- клиент ждёт ответа → ждём ответа от клиента.
                       CASE
                           WHEN awaiting_reply_since <= {urgent_threshold} THEN 1
                           WHEN NOT has_answer THEN 2
                           WHEN awaiting_reply_since IS NOT NULL THEN 3
                           ELSE 4
                       END,
                       -- Внутри группы первым разбирают того, кто ждёт дольше.
                       awaiting_reply_since ASC NULLS LAST,
                       (unread_count > 0) DESC,
                       last_message_at DESC NULLS LAST,
                       id DESC
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
    """Снять недоставленные ответы ИИ, когда диалог перешёл к человеку.

    Служебные подтверждения нажатых кнопок исключены: они отвечают на уже случившееся
    действие клиента, и именно передача человеку их и порождает. Отменять их — значит
    оставить клиента без ответа на собственное нажатие.
    """

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
               AND COALESCE(payload->>'service_reply', 'false') <> 'true'
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
           AND COALESCE(payload->>'service_reply', 'false') <> 'true'
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
    permanent: bool = False,
    now: datetime | None = None,
    connect: ConnectFactory | None = None,
) -> dict[str, Any]:
    """Передать разговор ИИ, человеку или поставить на паузу.

    ``permanent=True`` вместе с ``human`` — это ПОЛНЫЙ перехват: диалог остаётся за
    человеком, пока он сам не вернёт его ИИ. Обычный перехват держится арендой в
    ``FUNNEL_WORKSPACE_HUMAN_LEASE_SECONDS`` и сам истекает; полный не истекает никогда,
    потому что признак «навсегда» — это ``resume_at IS NULL``, а возврат аренды смотрит
    только на строки с непустым ``resume_at``.
    """
    clean_mode = str(mode or "").strip().lower()
    clean_actor = str(actor_type or "").strip().lower()
    if clean_mode not in VALID_CONTROL_MODES:
        raise WorkspaceValidationError("Неизвестный режим управления.", details={"mode": clean_mode})
    if clean_actor not in {"agent", "operator", "system"}:
        raise WorkspaceValidationError("Неизвестный тип автора.", details={"actor_type": clean_actor})
    if permanent and clean_mode != "human":
        raise WorkspaceValidationError(
            "Полный перехват — это режим человека; для ИИ и паузы он смысла не имеет.",
            details={"mode": clean_mode},
        )
    timestamp = _now(now)
    lease = human_lease_seconds() if lease_seconds is None else max(10, min(86_400, int(lease_seconds)))
    resume_at = (
        None
        if permanent or clean_mode != "human"
        else timestamp + timedelta(seconds=lease)
    )

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
    manager_requested: bool = False,
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
                       metadata = metadata || %s,
                       state_version = %s,
                       updated_at = %s
                 WHERE id = %s
             RETURNING *
                """,
                (
                    _clean_optional(assigned_to, 200),
                    Jsonb(
                        {
                            "manager_requested_at": timestamp.isoformat(),
                            "manager_request_reason": clean_reason,
                        }
                        if manager_requested
                        else {}
                    ),
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
                to_mode="paused",
                actor_type="agent",
                actor_name="ИИ-агент",
                reason=clean_reason,
                from_version=current_version,
                to_version=next_version,
            )
            return updated


def mark_manager_request_handled(
    conversation_id: Any,
    *,
    now: datetime | None = None,
    connect: ConnectFactory | None = None,
) -> dict[str, Any]:
    """Clear the UI request badge only after a real operator reply was delivered."""

    timestamp = _now(now)
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            row = _load_conversation_locked(cur, conversation_id)
            cur.execute(
                """
                UPDATE funnel_workspace_conversations
                   SET metadata = metadata || %s,
                       updated_at = %s
                 WHERE id = %s
             RETURNING *
                """,
                (
                    Jsonb({"manager_request_handled_at": timestamp.isoformat()}),
                    timestamp,
                    row["id"],
                ),
            )
            return dict(cur.fetchone())


def flag_needs_human(
    conversation_id: Any,
    *,
    reason: str,
    now: datetime | None = None,
    connect: ConnectFactory | None = None,
) -> dict[str, Any]:
    """Пометить обращение «нужен человек», НЕ отбирая разговор у ИИ.

    Владелец 28.07.2026: агент должен отвечать на то, что знает, а не заменять весь ответ на
    «уточню у команды» из-за одного пункта без ответа. Но и терять этот пункт нельзя — иначе
    вопрос клиента не увидит никто. Поэтому обращение поднимается в очередь оператора, а
    разговор продолжает вести ИИ: клиент получил ответ по существу и вправе спрашивать дальше.

    Версия состояния НЕ увеличивается намеренно: она отменила бы уже запланированный ответ на
    следующее сообщение клиента, и разговор снова оборвался бы молчанием."""
    clean_reason = _required_text(reason, "reason", 1000)
    timestamp = _now(now)
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            row = _load_conversation_locked(cur, conversation_id)
            if str(row["status"]) not in ACTIVE_STATUSES:
                return dict(row)
            cur.execute(
                """
                UPDATE funnel_workspace_conversations
                   SET status = 'waiting',
                       metadata = metadata || %s,
                       updated_at = %s
                 WHERE id = %s
             RETURNING *
                """,
                (
                    Jsonb(
                        {
                            "manager_requested_at": timestamp.isoformat(),
                            "manager_request_reason": clean_reason,
                        }
                    ),
                    timestamp,
                    row["id"],
                ),
            )
            updated = dict(cur.fetchone())
            _insert_control_event(
                cur,
                conversation_id=int(row["id"]),
                from_mode=row["control_mode"],
                to_mode=row["control_mode"],
                actor_type="agent",
                actor_name="ИИ-агент",
                reason=clean_reason,
                from_version=int(row["state_version"]),
                to_version=int(row["state_version"]),
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
                # Владелец 28.07.2026: в боте отвечает ИИ всем, кто пишет. Передача
                # человеку ставит паузу — и следующий вопрос клиента раньше уходил в
                # никуда: ИИ выключен, человек ещё не подошёл, в переписке «ответы
                # приостановлены». Молчание — сломанная логика, поэтому новый вопрос
                # возвращает ход ИИ. Разрешение канала уже посчитано в `schedule_ai`,
                # так что переписка менеджера с выключенным ИИ остаётся на паузе.
                # Забранный человеком диалог (`human`) не трогаем: там за рулём человек.
                if schedule_ai and new_mode == "paused" and next_status in ACTIVE_STATUSES:
                    new_mode = "ai"
            if clean_author == "operator":
                held_forever = is_permanent_hold(conversation)
                new_mode = "human"
                lease = (
                    human_lease_seconds()
                    if operator_lease_seconds is None
                    else max(10, min(86_400, int(operator_lease_seconds)))
                )
                # Полный перехват ответом не сбрасывается: иначе первая же реплика
                # человека превратила бы «веду сам» в двухминутную аренду, и ИИ
                # заговорил бы в диалоге, который у него забрали насовсем.
                resume_at = None if held_forever else timestamp + timedelta(seconds=lease)
                assigned_to = (
                    conversation.get("assigned_to")
                    if held_forever
                    else _clean_optional(author_name, 200) or "Оператор"
                )
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
                       metadata = metadata || %s,
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
                    Jsonb(
                        {"manager_request_handled_at": timestamp.isoformat()}
                        if clean_author == "operator"
                        else {}
                    ),
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
            elif new_mode != old_mode:
                _insert_control_event(
                    cur,
                    conversation_id=int(conversation["id"]),
                    from_mode=old_mode,
                    to_mode=new_mode,
                    actor_type="system",
                    actor_name="Система",
                    reason="Клиент написал снова — ответ вернулся ИИ.",
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


def _clean_attachment(attachment: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Описание исходящего файла для очереди доставки: токен + то, что увидит человек."""

    if not attachment:
        return None
    token = _required_text(attachment.get("token"), "attachment.token", 200)
    return {
        "token": token,
        "file_name": _required_text(attachment.get("file_name"), "attachment.file_name", 300),
        "mime_type": _clean_optional(attachment.get("mime_type"), 200) or "application/octet-stream",
        "file_size": _optional_int(attachment.get("file_size")),
    }


def _clean_attachments(
    attachment: Mapping[str, Any] | None,
    attachments: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...] | None,
) -> list[dict[str, Any]]:
    """Validate one Telegram attachment or a native 2-10 item media group."""

    if attachment and attachments:
        raise WorkspaceValidationError(
            "Передайте один файл или группу файлов, но не оба варианта одновременно."
        )
    raw = list(attachments or ([] if attachment is None else [attachment]))
    if len(raw) == 1:
        cleaned = _clean_attachment(raw[0])
        return [cleaned] if cleaned is not None else []
    if raw and not 2 <= len(raw) <= 10:
        raise WorkspaceValidationError(
            "Группа Telegram должна содержать от 2 до 10 файлов."
        )
    cleaned_group = [_clean_attachment(item) for item in raw]
    if any(item is None for item in cleaned_group):
        raise WorkspaceValidationError("Пустой файл в группе Telegram недопустим.")
    return [item for item in cleaned_group if item is not None]


def _optional_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


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
    attachment: Mapping[str, Any] | None = None,
    attachments: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...] | None = None,
    service: bool = False,
) -> dict[str, Any]:
    clean_files = _clean_attachments(attachment, attachments)
    clean_file = clean_files[0] if clean_files else None
    if not clean_files:
        clean_text = _required_text(text, "text", MAX_MESSAGE_LENGTH)
    else:
        # Подпись к файлу в Telegram ограничена 1024 символами, и текст без файла
        # обязателен — а с файлом сообщение осмысленно и без подписи.
        clean_text = str(text or "").strip()
        if len(clean_text) > MAX_CAPTION_LENGTH:
            raise WorkspaceValidationError(
                f"Подпись к файлу длиннее допустимых {MAX_CAPTION_LENGTH} символов.",
                details={"field": "text", "max_length": MAX_CAPTION_LENGTH},
            )
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
            if clean_author == "agent" and row["control_mode"] != "ai" and not service:
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
                held_forever = is_permanent_hold(row)
                lease = (
                    human_lease_seconds()
                    if operator_lease_seconds is None
                    else max(10, min(86_400, int(operator_lease_seconds)))
                )
                next_mode = "human"
                # Полный перехват ответом не сбрасывается — см. ingest_business_message.
                next_resume_at = (
                    None if held_forever else timestamp + timedelta(seconds=lease)
                )
                if not held_forever:
                    next_assignee = _clean_optional(author_name, 200) or "Оператор"
                if next_status in {"new", "waiting"}:
                    next_status = "open"
                _cancel_queued_ai(
                    cur,
                    int(row["id"]),
                    "Оператор забрал диалог и отправляет ответ.",
                )

            message_metadata = dict(metadata or {})
            if service:
                # Подтверждение нажатой кнопки — не реплика ИИ, а ответ системы на действие
                # клиента. Оно обязано дойти, даже если диалог в этот момент забрал человек.
                message_metadata["service_reply"] = True
            if clean_file is not None:
                message_metadata["outgoing_file"] = dict(clean_file)
            if len(clean_files) > 1:
                message_metadata["outgoing_files"] = [dict(item) for item in clean_files]
            # Пустая строка в списке диалогов читается как сбой, поэтому у сообщения
            # без подписи превью — имя отправленного файла.
            preview = clean_text or (f"📎 {clean_file['file_name']}" if clean_file else "")
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
                    Jsonb(message_metadata),
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
                    preview[:1000],
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
                    Jsonb(message_metadata),
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
    attachment: Mapping[str, Any] | None = None,
    attachments: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...] | None = None,
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
        attachment=attachment,
        attachments=attachments,
    )


def enqueue_outgoing_agent(
    conversation_id: Any,
    *,
    text: Any,
    expected_version: Any,
    idempotency_key: str,
    agent_name: str = "ИИ-агент",
    metadata: Mapping[str, Any] | None = None,
    attachment: Mapping[str, Any] | None = None,
    attachments: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...] | None = None,
    service: bool = False,
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
        attachment=attachment,
        attachments=attachments,
        operator_lease_seconds=None,
        now=now,
        connect=connect,
        service=service,
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
               -- Служебные подтверждения нажатых кнопок устареть не могут: они отвечают
               -- на действие клиента, а не продолжают разговор от имени ИИ.
               AND COALESCE(o.payload->>'service_reply', 'false') <> 'true'
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
                            -- Подтверждение нажатой клиентом кнопки: его порождает сама
                            -- передача диалога человеку, поэтому режим и версия здесь не
                            -- показатель — иначе клиент останется без ответа на нажатие.
                            OR (
                                o.author_type = 'agent'
                                AND COALESCE(o.payload->>'service_reply', 'false') = 'true'
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
    payload = row.get("payload") if isinstance(row.get("payload"), Mapping) else {}
    # Служебный ответ на действие клиента (нажатую кнопку) отменять по смене режима нельзя:
    # он подтверждает то, что уже произошло. Именно передача диалога человеку и меняет режим,
    # так что обычная проверка гасила бы подтверждение «зову менеджера» — клиент оставался
    # без ответа, а кнопка выглядела нерабочей.
    if row["author_type"] == "agent" and not payload.get("service_reply"):
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
                            -- Подтверждение нажатой клиентом кнопки: его порождает сама
                            -- передача диалога человеку, поэтому режим и версия здесь не
                            -- показатель — иначе клиент останется без ответа на нажатие.
                            OR (
                                o.author_type = 'agent'
                                AND COALESCE(o.payload->>'service_reply', 'false') = 'true'
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
    provider_media: Mapping[str, Any] | None = None,
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
            delivered_media = (
                Jsonb({"telegram_media": dict(provider_media)})
                if clean_result == "sent" and provider_media
                else None
            )
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
                       -- Идентификатор доставленного файла у Telegram: с ним вложение
                       -- открывается из ленты тем же прокси, что и входящие.
                       metadata = CASE
                           WHEN %s::jsonb IS NULL THEN metadata
                           ELSE metadata || %s::jsonb
                       END,
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
                    delivered_media,
                    delivered_media,
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
    notify_manager = bool(source_payload.get("notify_manager_after_delivery"))
    if asset not in {"terms", "form"} and not (
        outbox.get("author_type") == "agent" and escalate
    ) and not notify_manager:
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
        "notify_manager_after_delivery": notify_manager,
        "manager_notification_recipient": _clean_optional(
            source_payload.get("manager_notification_recipient"),
            100,
        ),
        "manager_notification_bot_id": _optional_int(
            source_payload.get("manager_notification_bot_id")
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


# --- комментарии по лиду -------------------------------------------------------------
# Свободный текст о клиенте и общении: пишет оператор из панели и агент своим
# инструментом. Хранится у нас и зеркалится в ленту сделки Битрикса — зеркало отмечается
# флагом, потому что недоступный в этот момент Битрикс не должен стоить человеку текста,
# который он уже написал.

LEAD_NOTE_AUTHORS = frozenset({"operator", "agent"})
LEAD_NOTE_MAX_CHARS = 4000


def add_lead_note(
    conversation_id: Any,
    text: str,
    *,
    author_type: str = "operator",
    author_name: str = "",
    connect: ConnectFactory | None = None,
) -> dict[str, Any]:
    item_id = _positive_int(conversation_id, "conversation_id")
    clean_text = str(text or "").strip()
    if not clean_text:
        raise WorkspaceValidationError("Комментарий пустой.", details={"field": "text"})
    if len(clean_text) > LEAD_NOTE_MAX_CHARS:
        raise WorkspaceValidationError(
            f"Комментарий длиннее {LEAD_NOTE_MAX_CHARS} символов.",
            details={"field": "text"},
        )
    clean_author = str(author_type or "operator").strip().lower()
    if clean_author not in LEAD_NOTE_AUTHORS:
        raise WorkspaceValidationError(
            "Автор комментария может быть operator или agent.",
            details={"author_type": clean_author},
        )

    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM funnel_workspace_conversations WHERE id = %s",
                (item_id,),
            )
            if cur.fetchone() is None:
                raise WorkspaceNotFoundError(
                    "Обращение не найдено.", details={"conversation_id": item_id}
                )
            cur.execute(
                """
                INSERT INTO funnel_workspace_lead_notes
                    (conversation_id, author_type, author_name, text)
                VALUES (%s, %s, %s, %s)
                RETURNING id, conversation_id, author_type, author_name, text,
                          bitrix_mirrored, bitrix_error, created_at
                """,
                (item_id, clean_author, str(author_name or "")[:120], clean_text),
            )
            return dict(cur.fetchone())


def list_lead_notes(
    conversation_id: Any,
    *,
    limit: int = 50,
    connect: ConnectFactory | None = None,
) -> list[dict[str, Any]]:
    item_id = _positive_int(conversation_id, "conversation_id")
    clean_limit = min(200, max(1, int(limit or 50)))
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, conversation_id, author_type, author_name, text,
                       bitrix_mirrored, bitrix_error, created_at
                  FROM funnel_workspace_lead_notes
                 WHERE conversation_id = %s
                 ORDER BY id DESC
                 LIMIT %s
                """,
                (item_id, clean_limit),
            )
            return [dict(row) for row in cur.fetchall()]


def mark_lead_note_mirrored(
    note_id: Any,
    *,
    error: str = "",
    connect: ConnectFactory | None = None,
) -> None:
    """Итог зеркалирования в Битрикс: успех или причина, по которой не доехало."""
    item_id = _positive_int(note_id, "note_id")
    with _connection(connect) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE funnel_workspace_lead_notes
                   SET bitrix_mirrored = %s, bitrix_error = %s
                 WHERE id = %s
                """,
                (not error, str(error or "")[:500] or None, item_id),
            )
