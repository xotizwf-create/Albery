"""Канал Авито: аккаунты, зеркало переписок и отправка через общий durable outbox.

Устройство и почему так.

Переписки, сообщения, очереди и перехват диалога человеком НЕ пишутся заново: канал живёт в
таблицах ``funnel_workspace_*`` (миграция 070), у которых транспорт с самого начала вынесен в
отдельную строку источника. Здесь только то, чего у Telegram нет: аккаунты Авито с состоянием
веб-сессии и своя страница ``/avito`` со своим списком.

Что этот модуль НЕ делает: он никуда не ходит в интернет. Транспорт Авито (браузерная сессия)
живёт отдельным воркером и общается с системой через те же durable-таблицы: входящее сообщение
он кладёт в ``funnel_workspace_updates``, исходящее забирает из ``funnel_workspace_outbox``.
Поэтому веб-процесс не может ни разлогинить сессию, ни отправить дубль.

Граница ответа оператору: пока транспорта нет или сессия аккаунта не жива, отправка ОТКАЗЫВАЕТ
с настоящей причиной. Положить строку в очередь, которую никто не разгребает, значит показать
оператору «отправлено» и не доставить — это худший исход из возможных.
"""
from __future__ import annotations

import hmac
import logging
import os
import re
import uuid
from collections.abc import Mapping
from typing import Any

from flask import Blueprint, Response, current_app, jsonify, request
from psycopg.types.json import Json

import funnel_workspace_store as store
from app import pg_connect

SOURCE_KEY = "avito"
SOURCE_TYPE = "avito_web"
SOURCE_DISPLAY_NAME = "Авито"

PAGE_PREFIX = "/avito"
API_PREFIX = "/api/agent-center/avito"
# Воркер браузерной сессии живёт на другой машине (выход в Авито) и приходит по токену,
# а не по сессии кабинета: у него нет браузера оператора и нет права видеть кабинет.
WORKER_PREFIX = "/api/avito-worker"

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_LABEL_MAX = 80
_TEXT_MAX = 4000
_SESSION_STATUSES = ("unknown", "ok", "needs_login", "blocked", "error")

avito_bp = Blueprint("avito_channel", __name__)


def register_avito_channel(app: Any) -> None:
    if avito_bp.name not in app.blueprints:
        app.register_blueprint(avito_bp)


def transport_enabled() -> bool:
    """Включён ли воркер браузерной сессии. Интерфейс работает и без него — только читает."""
    return os.getenv("AVITO_CHANNEL_ENABLED", "0").strip() == "1"


# --- Аккаунты --------------------------------------------------------------------------------

_ACCOUNT_COLS = ("slug, label, profile_dir, egress_label, session_status, session_checked_at, "
                 "last_error, is_active, created_at, updated_at")


def list_accounts(*, include_disabled: bool = True) -> list[dict[str, Any]]:
    where = "" if include_disabled else "WHERE is_active"
    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {_ACCOUNT_COLS} FROM avito_accounts {where} ORDER BY label, slug")
            return list(cur.fetchall())


def get_account(slug: str) -> dict[str, Any] | None:
    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {_ACCOUNT_COLS} FROM avito_accounts WHERE slug = %s", (slug,))
            return cur.fetchone()


def upsert_account(*, slug: str, label: str, egress_label: str = "",
                   profile_dir: str | None = None) -> dict[str, Any]:
    with pg_connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO avito_accounts (slug, label, egress_label, profile_dir) "
                    "VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (slug) DO UPDATE SET label = EXCLUDED.label, "
                    "egress_label = EXCLUDED.egress_label, "
                    "profile_dir = COALESCE(EXCLUDED.profile_dir, avito_accounts.profile_dir), "
                    "updated_at = now() "
                    f"RETURNING {_ACCOUNT_COLS}",
                    (slug, label, egress_label or None, profile_dir),
                )
                return cur.fetchone()


def set_account_active(slug: str, *, is_active: bool) -> dict[str, Any] | None:
    with pg_connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE avito_accounts SET is_active = %s, updated_at = now() "
                    f"WHERE slug = %s RETURNING {_ACCOUNT_COLS}",
                    (bool(is_active), slug),
                )
                return cur.fetchone()


def set_session_status(slug: str, *, status: str, error: str | None = None) -> dict[str, Any] | None:
    """Состояние веб-сессии пишет воркер транспорта; интерфейс его только показывает."""
    if status not in _SESSION_STATUSES:
        raise ValueError(f"Неизвестное состояние сессии: {status}")
    with pg_connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE avito_accounts SET session_status = %s, last_error = %s, "
                    "session_checked_at = now(), updated_at = now() "
                    f"WHERE slug = %s RETURNING {_ACCOUNT_COLS}",
                    (status, (error or "")[:500] or None, slug),
                )
                return cur.fetchone()


# --- Диалоги ---------------------------------------------------------------------------------

_CONVERSATION_COLS = (
    "c.id, c.business_connection_id AS account_slug, c.external_chat_id, c.external_user_id, "
    "c.username, c.display_name, c.status, c.control_mode, c.unread_count, "
    "c.last_read_message_id, c.state_version, c.last_message_at, c.last_message_text, "
    "c.last_author_type, c.metadata, c.created_at"
)


def list_conversations(*, account: str = "", status: str = "", query: str = "",
                       limit: int = 100, offset: int = 0) -> dict[str, Any]:
    """Список обращений Авито. Поиск смотрит и в переписку, а не только в превью:
    старый разговор находится по фразе из его середины, как в рабочем окне воронок."""
    limit = max(1, min(int(limit or 100), 200))
    offset = max(0, int(offset or 0))
    where = ["c.source_key = %s"]
    args: list[Any] = [SOURCE_KEY]
    if account:
        where.append("c.business_connection_id = %s")
        args.append(account)
    if status and status != "all":
        where.append("c.status = %s")
        args.append(status)
    if query:
        where.append(
            "(c.display_name ILIKE %s OR c.username ILIKE %s OR c.last_message_text ILIKE %s "
            "OR EXISTS (SELECT 1 FROM funnel_workspace_messages m "
            "WHERE m.conversation_id = c.id AND m.text ILIKE %s))"
        )
        args.extend([f"%{query}%"] * 4)
    clause = " AND ".join(where)
    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_CONVERSATION_COLS} FROM funnel_workspace_conversations c "
                f"WHERE {clause} "
                "ORDER BY (c.unread_count > 0) DESC, c.last_message_at DESC NULLS LAST, c.id DESC "
                "LIMIT %s OFFSET %s",
                (*args, limit, offset),
            )
            rows = list(cur.fetchall())
            cur.execute(
                "SELECT count(*) AS total, "
                "count(*) FILTER (WHERE c.unread_count > 0) AS unread "
                f"FROM funnel_workspace_conversations c WHERE {clause}",
                tuple(args),
            )
            totals = cur.fetchone() or {"total": 0, "unread": 0}
    return {
        "conversations": [_conversation_json(r) for r in rows],
        "total": int(totals["total"] or 0),
        "unread": int(totals["unread"] or 0),
        "limit": limit,
        "offset": offset,
    }


def get_conversation(conversation_id: int) -> dict[str, Any] | None:
    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_CONVERSATION_COLS} FROM funnel_workspace_conversations c "
                "WHERE c.id = %s AND c.source_key = %s",
                (conversation_id, SOURCE_KEY),
            )
            row = cur.fetchone()
    return _conversation_json(row) if row else None


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else None


def _conversation_json(row: Any) -> dict[str, Any]:
    metadata = dict(row["metadata"] or {})
    listing = metadata.get("listing") if isinstance(metadata.get("listing"), dict) else {}
    return {
        "id": row["id"],
        "account_slug": row["account_slug"] or "",
        "external_chat_id": row["external_chat_id"],
        "external_user_id": row["external_user_id"],
        "username": row["username"] or "",
        "display_name": row["display_name"] or row["username"] or "Собеседник",
        "status": row["status"],
        "control_mode": row["control_mode"],
        "unread_count": int(row["unread_count"] or 0),
        "last_read_message_id": int(row["last_read_message_id"] or 0),
        "state_version": int(row["state_version"] or 1),
        "last_message_at": _iso(row["last_message_at"]),
        "last_message_text": row["last_message_text"] or "",
        "last_author_type": row["last_author_type"] or "",
        "created_at": _iso(row["created_at"]),
        # Объявление, вокруг которого идёт разговор: его кладёт транспорт при первом сообщении.
        "listing": {
            "id": str(listing.get("id") or ""),
            "title": str(listing.get("title") or ""),
            "url": str(listing.get("url") or ""),
            "price": str(listing.get("price") or ""),
        },
    }


def _message_json(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "author_type": row["author_type"],
        "author_name": row["author_name"] or "",
        "direction": row["direction"],
        "text": row["text"] or "",
        "delivery_status": row["delivery_status"],
        "error_detail": row.get("error_detail") or "",
        "occurred_at": _iso(row["occurred_at"]),
        "sent_at": _iso(row.get("sent_at")),
    }


# --- Страница --------------------------------------------------------------------------------

@avito_bp.get(f"{PAGE_PREFIX}/<int:conversation_id>")
@avito_bp.get(PAGE_PREFIX)
@avito_bp.get(f"{PAGE_PREFIX}/")
def avito_page(conversation_id: int | None = None) -> Response:
    """Страница канала; с id открывает сразу нужный разговор (адрес разбирает сам интерфейс)."""
    del conversation_id
    index_view = current_app.view_functions.get("index")
    if index_view is None:
        return Response("Frontend route is not registered.", status=503, mimetype="text/plain")
    return index_view()


# --- API -------------------------------------------------------------------------------------

def _bad(message: str, status: int = 400):
    return jsonify({"error": message}), status


@avito_bp.get(f"{API_PREFIX}/state")
def avito_state():
    try:
        store.ensure_source(SOURCE_KEY, source_type=SOURCE_TYPE, display_name=SOURCE_DISPLAY_NAME)
        accounts = list_accounts()
        counts = list_conversations(limit=1)
        return jsonify({
            "transport_enabled": transport_enabled(),
            "accounts": [
                {
                    "slug": a["slug"],
                    "label": a["label"],
                    "egress_label": a["egress_label"] or "",
                    "session_status": a["session_status"],
                    "session_checked_at": _iso(a["session_checked_at"]),
                    "last_error": a["last_error"] or "",
                    "is_active": bool(a["is_active"]),
                }
                for a in accounts
            ],
            "total_conversations": counts["total"],
            "unread_conversations": counts["unread"],
        })
    except Exception:  # noqa: BLE001
        logging.exception("avito state failed")
        return _bad("Не удалось загрузить состояние канала.", 500)


@avito_bp.post(f"{API_PREFIX}/accounts")
def avito_account_create():
    body = request.get_json(silent=True) or {}
    slug = str(body.get("slug") or "").strip().lower()
    label = str(body.get("label") or "").strip()[:_LABEL_MAX]
    if not _SLUG_RE.match(slug):
        return _bad("Код аккаунта: латиница, цифры, дефис или подчёркивание, до 63 символов.")
    if not label:
        return _bad("Укажите название аккаунта — его видит оператор в списке.")
    try:
        account = upsert_account(
            slug=slug, label=label,
            egress_label=str(body.get("egress_label") or "").strip()[:_LABEL_MAX],
        )
        return jsonify({"account": {"slug": account["slug"], "label": account["label"],
                                    "session_status": account["session_status"],
                                    "egress_label": account["egress_label"] or "",
                                    "is_active": bool(account["is_active"])}})
    except Exception:  # noqa: BLE001
        logging.exception("avito account upsert failed: %s", slug)
        return _bad("Не удалось сохранить аккаунт.", 500)


@avito_bp.patch(f"{API_PREFIX}/accounts/<slug>")
def avito_account_update(slug: str):
    body = request.get_json(silent=True) or {}
    if "is_active" not in body:
        return _bad("Менять можно только включённость аккаунта (is_active).")
    try:
        account = set_account_active(slug, is_active=bool(body.get("is_active")))
        if not account:
            return _bad("Аккаунт не найден.", 404)
        return jsonify({"account": {"slug": account["slug"], "label": account["label"],
                                    "is_active": bool(account["is_active"]),
                                    "session_status": account["session_status"]}})
    except Exception:  # noqa: BLE001
        logging.exception("avito account update failed: %s", slug)
        return _bad("Не удалось обновить аккаунт.", 500)


@avito_bp.get(f"{API_PREFIX}/conversations")
def avito_conversations():
    try:
        return jsonify(list_conversations(
            account=str(request.args.get("account") or "").strip(),
            status=str(request.args.get("status") or "").strip(),
            query=str(request.args.get("q") or "").strip()[:200],
            limit=int(request.args.get("limit") or 100),
            offset=int(request.args.get("offset") or 0),
        ))
    except ValueError:
        return _bad("Некорректные параметры списка.")
    except Exception:  # noqa: BLE001
        logging.exception("avito conversations list failed")
        return _bad("Не удалось загрузить обращения.", 500)


@avito_bp.get(f"{API_PREFIX}/conversations/<int:conversation_id>/messages")
def avito_messages(conversation_id: int):
    conversation = get_conversation(conversation_id)
    if not conversation:
        return _bad("Разговор не найден.", 404)
    try:
        after_id = int(request.args.get("after_id") or 0)
        rows = store.list_messages(conversation_id, after_id=after_id, limit=200)
        return jsonify({"conversation": conversation,
                        "messages": [_message_json(r) for r in rows]})
    except ValueError:
        return _bad("Некорректный after_id.")
    except Exception:  # noqa: BLE001
        logging.exception("avito messages failed: %s", conversation_id)
        return _bad("Не удалось загрузить переписку.", 500)


def _delivery_block_reason(conversation: dict[str, Any]) -> str | None:
    """Почему ответ отправить нельзя. Пустая очередь честнее ложного «отправлено»."""
    if not transport_enabled():
        return ("Транспорт Авито выключен (AVITO_CHANNEL_ENABLED=0) — сообщение никто не доставит. "
                "Включите канал на сервере, тогда ответы уйдут.")
    account_slug = conversation["account_slug"]
    account = get_account(account_slug) if account_slug else None
    if not account:
        return f"Аккаунт «{account_slug or '—'}» не зарегистрирован в канале."
    if not account["is_active"]:
        return f"Аккаунт «{account['label']}» выключен."
    if account["session_status"] != "ok":
        human = {"needs_login": "нужен повторный вход", "blocked": "аккаунт заблокирован Авито",
                 "error": "сессия в ошибке", "unknown": "сессия ещё не проверена"}
        return (f"Сессия аккаунта «{account['label']}»: "
                f"{human.get(account['session_status'], account['session_status'])}.")
    return None


@avito_bp.post(f"{API_PREFIX}/conversations/<int:conversation_id>/reply")
def avito_reply(conversation_id: int):
    conversation = get_conversation(conversation_id)
    if not conversation:
        return _bad("Разговор не найден.", 404)
    body = request.get_json(silent=True) or {}
    text = str(body.get("text") or "").strip()
    if not text:
        return _bad("Пустой ответ отправить нельзя.")
    if len(text) > _TEXT_MAX:
        return _bad(f"Сообщение длиннее {_TEXT_MAX} символов.")
    operator_name = str(body.get("operator_name") or "Оператор").strip()[:_LABEL_MAX]
    blocked = _delivery_block_reason(conversation)
    if blocked:
        return jsonify({"error": blocked, "code": "avito_transport_unavailable"}), 409
    try:
        expected_version = int(body.get("expected_version") or conversation["state_version"])
        result = store.enqueue_outgoing_operator(
            conversation_id,
            text=text,
            expected_version=expected_version,
            operator_name=operator_name,
            idempotency_key=str(body.get("idempotency_key") or f"avito-op-{uuid.uuid4()}"),
            metadata={"channel": SOURCE_KEY, "account": conversation["account_slug"]},
        )
        message = result.get("message") or {}
        return jsonify({"queued": True, "message_id": message.get("id"),
                        "duplicate": bool(result.get("duplicate")),
                        "conversation": get_conversation(conversation_id)})
    except store.WorkspaceConflictError:
        return jsonify({"error": "Разговор уже изменился — обновите переписку и повторите.",
                        "code": "version_conflict"}), 409
    except (store.WorkspaceControlError, store.WorkspaceValidationError) as exc:
        return jsonify({"error": str(exc), "code": "rejected"}), 409
    except Exception:  # noqa: BLE001
        logging.exception("avito reply failed: %s", conversation_id)
        return _bad("Не удалось поставить ответ в очередь отправки.", 500)


@avito_bp.post(f"{API_PREFIX}/conversations/<int:conversation_id>/control")
def avito_control(conversation_id: int):
    conversation = get_conversation(conversation_id)
    if not conversation:
        return _bad("Разговор не найден.", 404)
    body = request.get_json(silent=True) or {}
    mode = str(body.get("mode") or "").strip().lower()
    if mode not in {"ai", "human", "paused"}:
        return _bad("Режим: ai, human или paused.")
    try:
        store.transition_control(
            conversation_id,
            mode=mode,
            expected_version=int(body.get("expected_version") or conversation["state_version"]),
            actor_type="operator",
            actor_name=str(body.get("operator_name") or "Оператор").strip()[:_LABEL_MAX],
            reason=str(body.get("reason") or "").strip()[:200] or None,
            permanent=bool(body.get("permanent")),
        )
        return jsonify({"conversation": get_conversation(conversation_id)})
    except store.WorkspaceConflictError:
        return jsonify({"error": "Разговор уже изменился — обновите переписку и повторите.",
                        "code": "version_conflict"}), 409
    except (store.WorkspaceControlError, store.WorkspaceValidationError) as exc:
        return jsonify({"error": str(exc), "code": "rejected"}), 409
    except Exception:  # noqa: BLE001
        logging.exception("avito control failed: %s", conversation_id)
        return _bad("Не удалось передать управление.", 500)


# --- Дверь воркера браузерной сессии ----------------------------------------------------------
#
# Воркер живёт на машине-выходе (у неё российский адрес и браузер с живой сессией Авито) и
# ходит сюда по токену. Он НЕ получает прав кабинета: только положить входящее, забрать
# исходящее и отчитаться о состоянии сессии. Веб-процесс, наоборот, никогда не ходит в Авито.

def worker_authorized() -> bool:
    """Fail closed: без заданного токена дверь закрыта совсем, а не открыта всем."""
    expected = os.getenv("AVITO_WORKER_TOKEN", "").strip()
    if not expected:
        return False
    provided = str(request.headers.get("X-Avito-Worker-Token") or "").strip()
    # Сравниваем БАЙТЫ: hmac.compare_digest на строках с не-ASCII символами бросает
    # TypeError, то есть токен с кириллицей ронял бы дверь в 500 вместо честного отказа.
    return bool(provided) and hmac.compare_digest(provided.encode("utf-8"),
                                                  expected.encode("utf-8"))


def is_worker_request(path: str) -> bool:
    return path == WORKER_PREFIX or path.startswith(WORKER_PREFIX + "/")


@avito_bp.before_request
def _guard_worker_routes():
    if is_worker_request(request.path) and not worker_authorized():
        return jsonify({"error": "forbidden"}), 403
    return None


@avito_bp.post(f"{WORKER_PREFIX}/session")
def worker_session_report():
    body = request.get_json(silent=True) or {}
    slug = str(body.get("account") or "").strip().lower()
    status = str(body.get("status") or "").strip()
    if status not in _SESSION_STATUSES:
        return _bad(f"Состояние сессии: одно из {', '.join(_SESSION_STATUSES)}.")
    try:
        account = set_session_status(slug, status=status, error=str(body.get("error") or "") or None)
        if not account:
            return _bad("Аккаунт не найден.", 404)
        return jsonify({"account": account["slug"], "session_status": account["session_status"]})
    except Exception:  # noqa: BLE001
        logging.exception("avito worker session report failed: %s", slug)
        return _bad("Не удалось записать состояние сессии.", 500)


def ingest_inbound(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Кладёт переписку из Авито в общий журнал.

    Идемпотентность держится на двух ключах: сырой пакет пишется в очередь обновлений с
    внешним идентификатором (повтор доставки не создаёт вторую строку), а каждое сообщение —
    на уникальном (диалог, внешний id сообщения). Поэтому воркер может смело повторять
    отправку после обрыва: дубля у оператора не будет.
    """
    slug = str(payload.get("account") or "").strip().lower()
    chat_id = str(payload.get("external_chat_id") or "").strip()
    if not slug or not chat_id:
        raise ValueError("Нужны account и external_chat_id.")
    messages = payload.get("messages")
    if not isinstance(messages, list):
        raise ValueError("messages должен быть списком.")

    store.ensure_source(SOURCE_KEY, source_type=SOURCE_TYPE, display_name=SOURCE_DISPLAY_NAME)
    listing = payload.get("listing") if isinstance(payload.get("listing"), Mapping) else {}
    conversation = store.ensure_conversation(
        external_chat_id=chat_id,
        source_key=SOURCE_KEY,
        business_connection_id=slug,
        external_user_id=payload.get("external_user_id"),
        username=str(payload.get("username") or "") or None,
        display_name=str(payload.get("display_name") or "") or None,
        metadata={"listing": dict(listing)} if listing else None,
    )
    conversation_id = int(conversation["id"])

    update_key = str(payload.get("update_id") or "").strip() or f"{slug}:{chat_id}:{len(messages)}"
    inserted = 0
    with pg_connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO funnel_workspace_updates (source_key, external_update_id, payload, "
                    "processing_status, conversation_id, completed_at) "
                    "VALUES (%s, %s, %s, 'done', %s, now()) "
                    "ON CONFLICT (source_key, external_update_id) DO NOTHING",
                    (SOURCE_KEY, update_key, Json(dict(payload)), conversation_id),
                )
                for message in messages:
                    if not isinstance(message, Mapping):
                        continue
                    external_id = str(message.get("external_message_id") or "").strip()
                    text = str(message.get("text") or "")
                    if not external_id:
                        continue
                    author = str(message.get("author_type") or "client").strip()
                    if author not in {"client", "agent", "operator", "system"}:
                        author = "client"
                    direction = "inbound" if author == "client" else "outbound"
                    cur.execute(
                        "INSERT INTO funnel_workspace_messages (conversation_id, external_message_id, "
                        "author_type, author_name, direction, text, delivery_status, metadata, occurred_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, 'sent', %s, COALESCE(%s, now())) "
                        # Индекс уникальности здесь ЧАСТИЧНЫЙ (миграция 070: только строки с
                        # внешним id), поэтому его условие обязано стоять и в ON CONFLICT —
                        # иначе Postgres не находит совпадающий индекс и падает.
                        "ON CONFLICT (conversation_id, external_message_id) "
                        "WHERE external_message_id IS NOT NULL DO NOTHING "
                        "RETURNING id",
                        (conversation_id, external_id, author,
                         str(message.get("author_name") or "") or None, direction, text,
                         Json({"channel": SOURCE_KEY}), message.get("occurred_at")),
                    )
                    if cur.fetchone():
                        inserted += 1
                if inserted:
                    # Превью, счётчик непрочитанного и «последнее слово» пересобираются по
                    # уцелевшей переписке, а не досчитываются на веру: повторная доставка
                    # того же пакета не должна раздувать счётчик.
                    cur.execute(
                        """
                        WITH last AS (
                            SELECT id, text, occurred_at, author_type
                              FROM funnel_workspace_messages
                             WHERE conversation_id = %s
                             ORDER BY id DESC LIMIT 1
                        ), unread AS (
                            SELECT count(*) AS n
                              FROM funnel_workspace_messages m, funnel_workspace_conversations c
                             WHERE m.conversation_id = %s AND c.id = %s
                               AND m.author_type = 'client' AND m.id > c.last_read_message_id
                        )
                        UPDATE funnel_workspace_conversations c
                           SET last_message_id = last.id,
                               last_message_text = left(last.text, 500),
                               last_message_at = last.occurred_at,
                               last_author_type = last.author_type,
                               unread_count = unread.n,
                               status = CASE WHEN c.status IN ('closed', 'expired') THEN 'open'
                                             ELSE c.status END,
                               updated_at = now()
                          FROM last, unread
                         WHERE c.id = %s
                        """,
                        (conversation_id, conversation_id, conversation_id, conversation_id),
                    )
    return {"conversation_id": conversation_id, "stored_messages": inserted,
            "received": len(messages)}


@avito_bp.post(f"{WORKER_PREFIX}/inbound")
def worker_inbound():
    body = request.get_json(silent=True) or {}
    try:
        return jsonify(ingest_inbound(body))
    except ValueError as exc:
        return _bad(str(exc))
    except Exception:  # noqa: BLE001
        logging.exception("avito worker inbound failed")
        return _bad("Не удалось сохранить переписку.", 500)


@avito_bp.post(f"{WORKER_PREFIX}/outbox/claim")
def worker_outbox_claim():
    body = request.get_json(silent=True) or {}
    worker_id = str(body.get("worker_id") or "").strip()[:200]
    if not worker_id:
        return _bad("Укажите worker_id — по нему держится аренда строки.")
    account = str(body.get("account") or "").strip().lower()
    try:
        claimed = store.claim_outbox(worker_id=worker_id, limit=int(body.get("limit") or 10),
                                     lease_seconds=int(body.get("lease_seconds") or 120))
    except Exception:  # noqa: BLE001
        logging.exception("avito worker outbox claim failed")
        return _bad("Не удалось забрать очередь отправки.", 500)
    items = []
    for row in claimed:
        if str(row.get("source_key") or "") != SOURCE_KEY:
            # Чужой канал: аренду сразу отпускаем, иначе телеграмное сообщение зависнет.
            store.finish_outbox(row["id"], worker_id=worker_id, result="pending",
                                error="claimed by the avito worker by mistake")
            continue
        if account and str(row.get("business_connection_id") or "") != account:
            store.finish_outbox(row["id"], worker_id=worker_id, result="pending",
                                error="another avito account")
            continue
        items.append({
            "outbox_id": row["id"],
            "conversation_id": row["conversation_id"],
            "account": row.get("business_connection_id") or "",
            "external_chat_id": row.get("external_chat_id") or "",
            "text": row.get("text") or "",
            "author_type": row.get("author_type") or "operator",
        })
    return jsonify({"items": items})


@avito_bp.post(f"{WORKER_PREFIX}/outbox/<int:outbox_id>/sending")
def worker_outbox_sending(outbox_id: int):
    """Граница побочного эффекта: до вызова Авито строка помечается «отправляется».

    Без этой отметки обрыв ВО ВРЕМЯ отправки был бы неотличим от обрыва ДО неё, и повтор
    отправил бы человеку второе сообщение.
    """
    body = request.get_json(silent=True) or {}
    worker_id = str(body.get("worker_id") or "").strip()[:200]
    try:
        guard = store.outbox_send_guard(outbox_id, worker_id=worker_id)
        if not guard.get("allowed"):
            store.finish_outbox(outbox_id, worker_id=worker_id, result="cancelled",
                                error=str(guard.get("reason") or "guard rejected"))
            return jsonify({"allowed": False, "reason": guard.get("reason")}), 409
        store.begin_outbox_send(outbox_id, worker_id=worker_id)
        return jsonify({"allowed": True})
    except Exception:  # noqa: BLE001
        logging.exception("avito worker outbox sending failed: %s", outbox_id)
        return _bad("Не удалось начать отправку.", 500)


@avito_bp.post(f"{WORKER_PREFIX}/outbox/<int:outbox_id>/result")
def worker_outbox_result(outbox_id: int):
    body = request.get_json(silent=True) or {}
    worker_id = str(body.get("worker_id") or "").strip()[:200]
    result = str(body.get("result") or "").strip()
    if result not in {"sent", "failed", "unknown"}:
        return _bad("result: sent, failed или unknown.")
    try:
        store.finish_outbox(outbox_id, worker_id=worker_id, result=result,
                            provider_message_id=body.get("provider_message_id"),
                            error=str(body.get("error") or "") or None)
        return jsonify({"ok": True})
    except Exception:  # noqa: BLE001
        logging.exception("avito worker outbox result failed: %s", outbox_id)
        return _bad("Не удалось записать итог отправки.", 500)


@avito_bp.post(f"{API_PREFIX}/conversations/<int:conversation_id>/read")
def avito_mark_read(conversation_id: int):
    conversation = get_conversation(conversation_id)
    if not conversation:
        return _bad("Разговор не найден.", 404)
    body = request.get_json(silent=True) or {}
    try:
        store.mark_read(conversation_id, through_message_id=int(body.get("through_message_id") or 0))
        return jsonify({"conversation": get_conversation(conversation_id)})
    except ValueError:
        return _bad("Некорректный through_message_id.")
    except Exception:  # noqa: BLE001
        logging.exception("avito mark read failed: %s", conversation_id)
        return _bad("Не удалось отметить прочитанным.", 500)
