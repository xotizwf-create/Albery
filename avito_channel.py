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

import logging
import os
import re
import uuid
from typing import Any

from flask import Blueprint, Response, current_app, jsonify, request

import funnel_workspace_store as store
from app import pg_connect

SOURCE_KEY = "avito"
SOURCE_TYPE = "avito_web"
SOURCE_DISPLAY_NAME = "Авито"

PAGE_PREFIX = "/avito"
API_PREFIX = "/api/agent-center/avito"

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
