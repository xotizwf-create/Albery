from __future__ import annotations

import csv
import hashlib
import hmac
import io
import logging
import os
import secrets
import threading
import time
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from flask import Blueprint, Response, current_app, g, jsonify, request, session
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash

import funnel_workspace_store as store
import funnel_workspace_media as workspace_media


logger = logging.getLogger(__name__)

API_PREFIX = "/api/funnel-workspace"
PAGE_PREFIX = "/agent-funnels"
SESSION_AUTH_KEY = "funnel_workspace_authenticated"
SESSION_OPERATOR_KEY = "funnel_workspace_operator"
SESSION_CSRF_KEY = "funnel_workspace_csrf"
SESSION_AUTH_AT_KEY = "funnel_workspace_authenticated_at"
SESSION_PASSWORD_FINGERPRINT_KEY = "funnel_workspace_password_fingerprint"
_LOGIN_ATTEMPTS: dict[str, list[float]] = {}
_LOGIN_LOCK = threading.Lock()
MOSCOW_TZ = ZoneInfo("Europe/Moscow")

funnel_workspace_bp = Blueprint("funnel_workspace", __name__)


def is_workspace_request(path: str | None = None) -> bool:
    current = path if path is not None else request.path
    return (
        current == PAGE_PREFIX
        or current.startswith(f"{PAGE_PREFIX}/")
        or current == API_PREFIX
        or current.startswith(f"{API_PREFIX}/")
    )


def _error(
    message: str,
    status_code: int = 400,
    code: str = "error",
    *,
    details: dict[str, Any] | None = None,
) -> tuple[Response, int]:
    payload: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details:
        payload["error"]["details"] = _jsonable(details)
    return jsonify(payload), status_code


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        normalized = value
        if normalized.tzinfo is None:
            normalized = normalized.replace(tzinfo=timezone.utc)
        return normalized.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _json(payload: Any, status_code: int = 200) -> tuple[Response, int]:
    return jsonify(_jsonable(payload)), status_code


def _client_ip() -> str:
    # Nginx overwrites X-Real-IP in production.  Do not trust a client-controlled
    # X-Forwarded-For chain here; rate limiting must not be bypassable by adding a hop.
    return (
        request.headers.get("X-Real-IP", "").strip()
        or request.remote_addr
        or "unknown"
    )


def _rate_limit_settings() -> tuple[int, int]:
    try:
        window = int(
            os.getenv(
                "FUNNEL_WORKSPACE_AUTH_RATE_LIMIT_WINDOW_SECONDS",
                os.getenv("FUNNEL_WORKSPACE_AUTH_WINDOW_SECONDS", "900"),
            )
            or "900"
        )
    except ValueError:
        window = 900
    try:
        attempts = int(
            os.getenv(
                "FUNNEL_WORKSPACE_AUTH_RATE_LIMIT_ATTEMPTS",
                os.getenv("FUNNEL_WORKSPACE_AUTH_ATTEMPTS", "6"),
            )
            or "6"
        )
    except ValueError:
        attempts = 6
    return max(60, min(window, 86_400)), max(2, min(attempts, 100))


def _login_rate_limited(client_ip: str) -> tuple[bool, int]:
    window, maximum = _rate_limit_settings()
    now = time.monotonic()
    with _LOGIN_LOCK:
        recent = [
            timestamp
            for timestamp in _LOGIN_ATTEMPTS.get(client_ip, [])
            if now - timestamp < window
        ]
        _LOGIN_ATTEMPTS[client_ip] = recent
        if len(_LOGIN_ATTEMPTS) > 10_000:
            empty_keys = [key for key, values in _LOGIN_ATTEMPTS.items() if not values]
            for key in empty_keys[:1000]:
                _LOGIN_ATTEMPTS.pop(key, None)
        if len(recent) < maximum:
            return False, 0
        retry_after = max(1, int(window - (now - recent[0])))
        return True, retry_after


def _record_failed_login(client_ip: str) -> None:
    with _LOGIN_LOCK:
        _LOGIN_ATTEMPTS.setdefault(client_ip, []).append(time.monotonic())


def _clear_failed_logins(client_ip: str) -> None:
    with _LOGIN_LOCK:
        _LOGIN_ATTEMPTS.pop(client_ip, None)


def _configured_password_hash() -> str:
    environment_hash = os.getenv("FUNNEL_WORKSPACE_PASSWORD_HASH", "").strip()
    if environment_hash:
        return environment_hash
    return store.get_workspace_password_hash()


def _password_managed_by_environment() -> bool:
    return bool(os.getenv("FUNNEL_WORKSPACE_PASSWORD_HASH", "").strip())


def _session_secret_is_safe() -> bool:
    secret = str(current_app.config.get("SECRET_KEY") or "")
    known_defaults = {
        "change-this-secret",
        "replace-with-random-string",
        "albery-applet",
    }
    return len(secret) >= 32 and secret not in known_defaults


def _password_fingerprint(password_hash: str | None = None) -> str:
    configured = password_hash if password_hash is not None else _configured_password_hash()
    if not configured:
        return ""
    return hashlib.sha256(configured.encode("utf-8")).hexdigest()


def _clear_workspace_session() -> None:
    session.pop(SESSION_AUTH_KEY, None)
    session.pop(SESSION_OPERATOR_KEY, None)
    session.pop(SESSION_CSRF_KEY, None)
    session.pop(SESSION_AUTH_AT_KEY, None)
    session.pop(SESSION_PASSWORD_FINGERPRINT_KEY, None)


def _auth_session_days() -> int:
    try:
        days = int(os.getenv("FUNNEL_WORKSPACE_AUTH_SESSION_DAYS", "30") or "30")
    except ValueError:
        days = 30
    return min(90, max(1, days))


def funnel_stages() -> list[dict[str, Any]]:
    """Этапы воронки ИУ в том же порядке и с теми же названиями, что видит владелец."""
    import iu_funnel

    return [
        {"value": stage.id, "label": stage.title, "goal": stage.goal, "order": index}
        for index, stage in enumerate(iu_funnel.CHAIN)
    ]


def _workspace_ai_allowed(external_user_id: Any) -> bool:
    import funnel_telegram_gateway

    return funnel_telegram_gateway.ai_allowed(external_user_id)


def workspace_authenticated() -> bool:
    if session.get("admin_authenticated"):
        return True
    if not session.get(SESSION_AUTH_KEY):
        return False
    try:
        authenticated_at = float(session.get(SESSION_AUTH_AT_KEY) or 0)
    except (TypeError, ValueError):
        authenticated_at = 0
    if authenticated_at <= 0 or time.time() - authenticated_at > _auth_session_days() * 86_400:
        _clear_workspace_session()
        return False
    expected_fingerprint = _password_fingerprint()
    session_fingerprint = str(session.get(SESSION_PASSWORD_FINGERPRINT_KEY) or "")
    if not expected_fingerprint or not hmac.compare_digest(
        expected_fingerprint,
        session_fingerprint,
    ):
        _clear_workspace_session()
        return False
    return True


def configured_operator_name() -> str:
    """Имя, закреплённое за паролем рабочего окна (пусто — если не задано)."""
    try:
        return store.get_workspace_operator_name()
    except Exception:  # noqa: BLE001
        # Имя — украшение подписи, а не право доступа: недоступная настройка не
        # должна ронять вход в рабочее окно.
        return ""


def workspace_operator_name() -> str:
    if session.get(SESSION_AUTH_KEY):
        return (
            configured_operator_name()
            or str(session.get(SESSION_OPERATOR_KEY) or "Оператор")
        )
    if session.get("admin_authenticated"):
        return str(session.get("admin_name") or "Администратор")
    return ""


def _ensure_csrf_token() -> str:
    token = str(session.get(SESSION_CSRF_KEY) or "")
    if not token:
        token = secrets.token_urlsafe(32)
        session[SESSION_CSRF_KEY] = token
    return token


def _same_origin() -> bool:
    origin = request.headers.get("Origin", "").strip()
    referer = request.headers.get("Referer", "").strip()
    scheme = (
        request.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip()
        or request.scheme
    )
    host = request.host
    expected = f"{scheme}://{host}"
    if origin:
        return hmac.compare_digest(origin.rstrip("/"), expected.rstrip("/"))
    if referer:
        return referer == expected or referer.startswith(f"{expected}/")
    # Non-browser integrations do not receive this cookie realm.  Browsers using
    # fetch/forms normally send Origin or Referer for a state-changing request.
    return request.remote_addr in {"127.0.0.1", "::1"} and os.getenv(
        "FUNNEL_WORKSPACE_ALLOW_LOCAL_NO_ORIGIN", "0"
    ).strip() == "1"


def _csrf_ok() -> bool:
    expected = str(session.get(SESSION_CSRF_KEY) or "")
    provided = request.headers.get("X-CSRF-Token", "").strip()
    if not provided and request.is_json:
        body = request.get_json(silent=True) or {}
        provided = str(body.get("csrf_token") or "")
    return bool(expected and provided and hmac.compare_digest(expected, provided))


def _is_session_entry_request() -> bool:
    return request.path == f"{API_PREFIX}/session" and request.method in {"GET", "POST"}


def workspace_request_gate() -> tuple[Response, int] | None:
    """Central gate for `/agent-funnels` and its dedicated API realm.

    In app.py call it before the legacy `/api/` kill switch and the admin gate:

        if is_workspace_request():
            return workspace_request_gate()

    Returning this function (including its `None`) exits the parent before_request
    handler, so a valid standalone workspace session never becomes an admin session.
    """

    if not is_workspace_request():
        return None
    if getattr(g, "_funnel_workspace_gate_checked", False):
        return None
    g._funnel_workspace_gate_checked = True
    if request.path == PAGE_PREFIX or request.path.startswith(f"{PAGE_PREFIX}/"):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            return _error("Метод не поддерживается.", 405, "method_not_allowed")
        return None
    if not _session_secret_is_safe():
        return _error(
            "Рабочее окно не запущено: FLASK_SECRET_KEY должен быть случайным и не короче 32 символов.",
            503,
            "session_secret_not_configured",
        )
    if (
        request.content_length is not None
        and request.content_length > 16_384
        and request.path
        in {f"{API_PREFIX}/session", f"{API_PREFIX}/configure-password"}
    ):
        return _error("Запрос слишком большой.", 413, "request_too_large")
    if request.path == f"{API_PREFIX}/session" and request.method == "GET":
        return None
    if request.path == f"{API_PREFIX}/session" and request.method == "POST":
        if not _same_origin():
            return _error("Недопустимый источник запроса.", 403, "invalid_origin")
        return None
    if request.path == f"{API_PREFIX}/session" and request.method == "DELETE":
        if not workspace_authenticated():
            return _error(
                "Требуется вход в рабочее окно.",
                401,
                "authentication_required",
            )
        if not _same_origin():
            return _error(
                "Недопустимый источник запроса.",
                403,
                "invalid_origin",
            )
        if not _csrf_ok():
            return _error(
                "CSRF-токен отсутствует или устарел.",
                403,
                "csrf_failed",
            )
        # Logout must remain possible while the traffic feature flag is off.
        return None
    if request.path == f"{API_PREFIX}/configure-password":
        if not session.get("admin_authenticated"):
            return _error(
                "Настроить отдельный пароль может только администратор.",
                403,
                "admin_session_required",
            )
        if request.method != "POST":
            return _error("Метод не поддерживается.", 405, "method_not_allowed")
        if not _same_origin():
            return _error("Недопустимый источник запроса.", 403, "invalid_origin")
        if not _csrf_ok():
            return _error("CSRF-токен отсутствует или устарел.", 403, "csrf_failed")
        return None
    if not workspace_authenticated():
        return _error("Требуется вход в рабочее окно.", 401, "authentication_required")
    if not store.enabled():
        return _error(
            "Рабочее окно временно выключено.",
            503,
            "workspace_disabled",
        )
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        if not _same_origin():
            return _error("Недопустимый источник запроса.", 403, "invalid_origin")
        if not _csrf_ok():
            return _error("CSRF-токен отсутствует или устарел.", 403, "csrf_failed")
    return None


@funnel_workspace_bp.before_request
def _blueprint_gate() -> tuple[Response, int] | None:
    return workspace_request_gate()


@funnel_workspace_bp.after_request
def _workspace_response_headers(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def register_funnel_workspace(app: Any) -> None:
    if funnel_workspace_bp.name not in app.blueprints:
        app.register_blueprint(funnel_workspace_bp)


@funnel_workspace_bp.get(f"{PAGE_PREFIX}/<int:conversation_id>")
@funnel_workspace_bp.get(PAGE_PREFIX)
@funnel_workspace_bp.get(f"{PAGE_PREFIX}/")
def workspace_page(conversation_id: int | None = None) -> Response:
    """Страница рабочего окна; с id открывает сразу нужный диалог.

    Ссылка вида /agent-funnels/12 нужна напоминаниям: бот присылает оператору не
    «зайдите и найдите», а точный адрес обращения, на которое надо ответить.
    """
    del conversation_id  # адрес разбирает сам интерфейс
    index_view = current_app.view_functions.get("index")
    if index_view is None:
        return Response("Frontend route is not registered.", status=503, mimetype="text/plain")
    return index_view()


def conversation_url(conversation_id: Any) -> str:
    """Постоянный адрес диалога — его печатают в напоминаниях и задачах.

    Адрес берётся из ``FUNNEL_WORKSPACE_PUBLIC_BASE`` — отдельной переменной только для
    ссылок. ``CANONICAL_WEB_HOST`` для этого не годится: он включает перенаправление на
    канонический домен, и любой локальный запрос (в том числе внутренние вызовы MCP по
    127.0.0.1) начинает получать 301. Служебный домен MCP тоже не подходит: его адрес в
    напоминании увёл бы оператора не туда. Если ничего не задано, отдаём относительный
    путь — внутри сайта он всё равно верен.
    """
    host = (
        os.getenv("FUNNEL_WORKSPACE_PUBLIC_BASE", "").strip().rstrip("/")
        or os.getenv("CANONICAL_WEB_HOST", "").strip().rstrip("/")
    )
    path = f"{PAGE_PREFIX}/{int(conversation_id)}"
    if not host:
        return path
    if "://" not in host:
        host = f"https://{host}"
    return f"{host}{path}"


def _session_payload() -> dict[str, Any]:
    authenticated = workspace_authenticated()
    payload: dict[str, Any] = {
        "authenticated": authenticated,
        "operator_name": workspace_operator_name() if authenticated else None,
        "admin_session": bool(session.get("admin_authenticated")),
        "workspace_enabled": store.enabled(),
        "configured": bool(_configured_password_hash()),
        "can_configure": bool(
            session.get("admin_authenticated")
            and not _password_managed_by_environment()
        ),
        "configured_operator_name": configured_operator_name(),
    }
    # Токен выдаётся всегда, а не только вошедшему: первичная установка пароля происходит
    # ДО входа в рабочее окно, и без токена форму невозможно отправить. Это безопасно —
    # значение живёт в подписанной cookie сессии и сверяется с присланным (double submit),
    # прочитать его с чужого источника нельзя, а происхождение запроса проверяется отдельно.
    payload["csrf_token"] = _ensure_csrf_token()
    return payload


@funnel_workspace_bp.get(f"{API_PREFIX}/session")
def get_workspace_session() -> tuple[Response, int]:
    response, status_code = _json(_session_payload())
    response.headers["Cache-Control"] = "no-store"
    return response, status_code


@funnel_workspace_bp.post(f"{API_PREFIX}/session")
def create_workspace_session() -> tuple[Response, int]:
    if session.get("admin_authenticated"):
        return _json(_session_payload())
    if request.content_length is not None and request.content_length > 16_384:
        return _error("Запрос входа слишком большой.", 413, "request_too_large")
    client_ip = _client_ip()
    limited, retry_after = _login_rate_limited(client_ip)
    if limited:
        response, status_code = _error(
            "Слишком много попыток входа. Повторите позже.",
            429,
            "rate_limited",
            details={"retry_after_seconds": retry_after},
        )
        response.headers["Retry-After"] = str(retry_after)
        return response, status_code
    password_hash = _configured_password_hash()
    if not password_hash:
        return _error(
            "FUNNEL_WORKSPACE_PASSWORD_HASH не настроен.",
            503,
            "password_not_configured",
        )
    body = request.get_json(silent=True) if request.is_json else request.form
    body = body or {}
    password = str(body.get("password") or "")
    if len(password) > 256:
        _record_failed_login(client_ip)
        return _error("Неверный пароль.", 401, "invalid_credentials")
    # Имя закреплено за паролем: под одним входом работает один названный сотрудник,
    # и в переписке всегда стоит его имя, а не то, что напечатали в поле при входе.
    operator_name = (
        configured_operator_name()
        or str(body.get("operator_name") or "Оператор").strip()[:80]
        or "Оператор"
    )
    try:
        password_matches = check_password_hash(password_hash, password)
    except (TypeError, ValueError):
        logger.exception("FUNNEL_WORKSPACE_PASSWORD_HASH is malformed")
        return _error(
            "Хэш пароля рабочего окна настроен неверно.",
            503,
            "password_hash_invalid",
        )
    if not password_matches:
        _record_failed_login(client_ip)
        return _error("Неверный пароль.", 401, "invalid_credentials")

    _clear_failed_logins(client_ip)
    session.permanent = True
    session[SESSION_AUTH_KEY] = True
    session[SESSION_OPERATOR_KEY] = operator_name
    session[SESSION_CSRF_KEY] = secrets.token_urlsafe(32)
    session[SESSION_AUTH_AT_KEY] = time.time()
    session[SESSION_PASSWORD_FINGERPRINT_KEY] = _password_fingerprint(password_hash)
    response, status_code = _json(_session_payload())
    response.headers["Cache-Control"] = "no-store"
    return response, status_code


@funnel_workspace_bp.delete(f"{API_PREFIX}/session")
def delete_workspace_session() -> tuple[Response, int]:
    _clear_workspace_session()
    response, status_code = _json(
        {
            "authenticated": bool(session.get("admin_authenticated")),
            "admin_session": bool(session.get("admin_authenticated")),
        }
    )
    response.headers["Cache-Control"] = "no-store"
    return response, status_code


@funnel_workspace_bp.post(f"{API_PREFIX}/configure-password")
def configure_workspace_password() -> tuple[Response, int]:
    if _password_managed_by_environment():
        return _error(
            "Пароль рабочего окна управляется через окружение и не может быть заменён здесь.",
            409,
            "password_managed_by_environment",
        )
    body = _json_body()
    admin_password = str(body.get("admin_password") or "")
    new_password = str(body.get("new_password") or "")
    if len(admin_password) > 256:
        return _error(
            "Пароль администратора не подтверждён.",
            403,
            "admin_reauthentication_failed",
        )
    if len(new_password) < 12:
        return _error(
            "Новый пароль должен содержать не менее 12 символов.",
            400,
            "password_too_short",
        )
    if len(new_password) > 256:
        return _error(
            "Новый пароль слишком длинный.",
            400,
            "password_too_long",
        )

    client_key = f"configure:{_client_ip()}"
    limited, retry_after = _login_rate_limited(client_key)
    if limited:
        response, status_code = _error(
            "Слишком много попыток подтверждения. Повторите позже.",
            429,
            "rate_limited",
            details={"retry_after_seconds": retry_after},
        )
        response.headers["Retry-After"] = str(retry_after)
        return response, status_code
    admin_password_hash = os.getenv("ADMIN_PASSWORD_HASH", "").strip()
    if not admin_password_hash:
        return _error(
            "Пароль администратора не настроен.",
            503,
            "admin_password_not_configured",
        )
    try:
        admin_matches = check_password_hash(admin_password_hash, admin_password)
    except (TypeError, ValueError):
        logger.exception("ADMIN_PASSWORD_HASH is malformed")
        return _error(
            "Хэш пароля администратора настроен неверно.",
            503,
            "admin_password_hash_invalid",
        )
    if not admin_matches:
        _record_failed_login(client_key)
        return _error(
            "Пароль администратора не подтверждён.",
            403,
            "admin_reauthentication_failed",
        )

    _clear_failed_logins(client_key)
    password_hash = generate_password_hash(new_password, method="scrypt")
    store.set_workspace_password_hash(password_hash)
    operator_name = str(body.get("operator_name") or "").strip()[:80]
    if operator_name:
        store.set_workspace_operator_name(operator_name)
    # Never include either password or its hash in the response/logs.
    return _json(
        {
            "configured": True,
            "can_configure": True,
            "configured_operator_name": configured_operator_name(),
        }
    )


@funnel_workspace_bp.get(f"{API_PREFIX}/meta")
def workspace_meta() -> tuple[Response, int]:
    import funnel_telegram_gateway

    return _json(
        {
            "enabled": store.enabled(),
            "operator_name": workspace_operator_name(),
            "ai_enabled": funnel_telegram_gateway.ai_enabled(),
            "ai_rollout_limited": (
                funnel_telegram_gateway.ai_allow_ids() is not None
            ),
            "source": "telegram",
            "source_name": "Telegram",
            "bitrix_base_url": os.getenv("BITRIX_PORTAL_URL", "").strip().rstrip("/"),
            "telegram_connected": funnel_telegram_gateway.telegram_connected(),
            "sources": store.list_sources(),
            # Этапы берутся из самой воронки ИУ (iu_funnel.CHAIN), а не дублируются
            # списком: поменяют воронку — рабочее окно покажет ровно то же самое.
            "funnel_stages": funnel_stages(),
            "work_states": [
                {"value": value, "label": store.WORK_STATE_LABELS[value]}
                for value in (
                    store.WORK_STATE_NEW,
                    store.WORK_STATE_CLIENT_WAITING,
                    store.WORK_STATE_WAITING_CLIENT,
                    store.WORK_STATE_URGENT,
                )
            ],
            "statuses": [
                {"key": "new", "value": "new", "label": "Новая"},
                {"key": "open", "value": "open", "label": "В работе"},
                {"key": "waiting", "value": "waiting", "label": "Нужен человек"},
                {"key": "closed", "value": "closed", "label": "Закрыта"},
                {"key": "spam", "value": "spam", "label": "Спам"},
                {"key": "expired", "value": "expired", "label": "Истекло окно ответа"},
            ],
            "control_modes": [
                {"key": key, "label": label}
                for key, label in store.CONTROL_MODE_LABELS.items()
            ],
            # Очередь разбора: в этом же порядке список приходит с сервера.
            "work_state_priority": [
                {"value": value, "label": store.WORK_STATE_LABELS[value],
                 "priority": store.WORK_STATE_PRIORITY[value]}
                for value in sorted(
                    store.WORK_STATE_PRIORITY,
                    key=store.WORK_STATE_PRIORITY.__getitem__,
                )
            ],
            "human_lease_seconds": store.human_lease_seconds(),
            "reply_window_hours": store.reply_window_hours(),
            "urgent_after_minutes": store.urgent_after_minutes(),
            "retention_days": store.retention_days(),
            "max_message_length": store.MAX_MESSAGE_LENGTH,
        }
    )


def _conversation_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    payload["source"] = payload.get("source_key")
    payload["last_message"] = payload.get("last_message_text")
    payload["display_name"] = (
        payload.get("display_name")
        or (f"@{payload['username']}" if payload.get("username") else None)
        or f"Telegram {payload.get('external_chat_id')}"
    )
    payload["deal_title"] = payload.get("deal_title")
    payload["stage_name"] = payload.get("stage_name")
    payload["control_mode_internal"] = payload.get("control_mode")
    deadline = payload.get("reply_deadline_at")
    if isinstance(deadline, str):
        try:
            deadline = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
        except ValueError:
            deadline = None
    if isinstance(deadline, datetime) and deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    reply_open = (
        payload.get("status") in store.ACTIVE_STATUSES
        and (deadline is None or deadline > datetime.now(timezone.utc))
    )
    payload["can_reply"] = reply_open
    payload["ai_available"] = (
        reply_open and _workspace_ai_allowed(payload.get("external_user_id"))
    )
    payload["url"] = conversation_url(payload["id"])
    waiting = payload.get("awaiting_reply_since")
    if isinstance(waiting, str):
        try:
            waiting = datetime.fromisoformat(waiting.replace("Z", "+00:00"))
        except ValueError:
            waiting = None
    if isinstance(waiting, datetime):
        if waiting.tzinfo is None:
            waiting = waiting.replace(tzinfo=timezone.utc)
        waiting_minutes = max(
            0,
            int((datetime.now(timezone.utc) - waiting).total_seconds() // 60),
        )
        payload["waiting_minutes"] = waiting_minutes
    else:
        waiting_minutes = None
        payload["waiting_minutes"] = None

    # Рабочий статус — факт переписки, а не отдельное поле: «Новый клиент», пока мы не
    # ответили ни разу; «Клиент ждёт ответ», пока последнее слово за ним; иначе «Ждём
    # ответ клиента». Срочность — отдельная пометка поверх, она про время.
    has_answer = bool(payload.get("has_answer"))
    if not has_answer:
        payload["work_state"] = store.WORK_STATE_NEW
    elif waiting_minutes is not None:
        payload["work_state"] = store.WORK_STATE_CLIENT_WAITING
    else:
        payload["work_state"] = store.WORK_STATE_WAITING_CLIENT
    payload["work_state_label"] = store.WORK_STATE_LABELS[payload["work_state"]]
    payload["urgent"] = (
        waiting_minutes is not None
        and waiting_minutes >= store.urgent_after_minutes()
    )
    payload["urgency"] = "urgent" if payload["urgent"] else "working"
    # Место в очереди разбора — тот же порядок, в котором список приходит с сервера.
    payload["priority"] = store.WORK_STATE_PRIORITY[
        store.WORK_STATE_URGENT if payload["urgent"] else payload["work_state"]
    ]

    # Третий бейдж — кто ведёт разговор. Полный перехват остаётся «Человек управляет»:
    # для оператора важно, что отвечает человек, а бессрочность видна отдельной пометкой.
    control_mode = str(payload.get("control_mode") or "")
    payload["control_label"] = store.CONTROL_MODE_LABELS.get(
        control_mode,
        store.CONTROL_MODE_LABELS["paused"],
    )
    payload["control_permanent"] = store.is_permanent_hold(payload)
    return payload


def _message_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    attachment = workspace_media.attachment_descriptor(
        payload.get("metadata"),
        payload.get("id"),
    )
    # Provider file identifiers stay server-side.  The browser receives only a
    # same-origin proxy URL and display-safe attachment fields.
    payload.pop("metadata", None)
    payload["attachment"] = attachment
    payload["author_type_internal"] = payload.get("author_type")
    payload["direction_internal"] = payload.get("direction")
    payload["author_type"] = {
        "agent": "ai",
        "operator": "human",
    }.get(str(payload.get("author_type")), payload.get("author_type"))
    payload["direction"] = {
        "inbound": "incoming",
        "outbound": "outgoing",
    }.get(str(payload.get("direction")), payload.get("direction"))
    payload["created_at"] = payload.get("occurred_at") or payload.get("created_at")
    payload["error"] = payload.get("error_detail")
    return payload


def _int_arg(name: str, default: int, *, minimum: int = 0, maximum: int = 100_000) -> int:
    raw = request.args.get(name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise store.WorkspaceValidationError(
            f"Параметр {name} должен быть целым числом.",
            details={"field": name},
        ) from exc
    return min(maximum, max(minimum, value))


@funnel_workspace_bp.get(f"{API_PREFIX}/conversations")
def conversations_list() -> tuple[Response, int]:
    result = store.list_conversations(
        q=request.args.get("q", ""),
        status=request.args.get("status", ""),
        stage=request.args.get("stage", ""),
        state=request.args.get("state", ""),
        urgency=request.args.get("urgency", ""),
        source=request.args.get("source", ""),
        limit=_int_arg("limit", 100, minimum=1, maximum=250),
        offset=_int_arg("offset", 0, minimum=0, maximum=10_000_000),
    )
    return _json(
        {
            "conversations": [
                _conversation_payload(row)
                for row in result["items"]
            ],
            "total": result["total"],
            "limit": result["limit"],
            "offset": result["offset"],
        }
    )


@funnel_workspace_bp.get(f"{API_PREFIX}/conversations/<int:conversation_id>")
def conversation_get(conversation_id: int) -> tuple[Response, int]:
    detail = store.conversation_detail(
        conversation_id,
        message_limit=_int_arg("message_limit", 200, minimum=1, maximum=500),
    )
    detail["conversation"] = _conversation_payload(detail["conversation"])
    detail["messages"] = [
        _message_payload(row)
        for row in detail["messages"]
    ]
    detail["control_events"] = store.list_control_events(conversation_id, limit=50)
    return _json(detail)


@funnel_workspace_bp.get(
    f"{API_PREFIX}/conversations/<int:conversation_id>/messages"
)
def messages_list(conversation_id: int) -> tuple[Response, int]:
    limit = _int_arg("limit", 200, minimum=1, maximum=500)
    before_id = (
        _int_arg("before_id", 0, minimum=1, maximum=9_223_372_036_854_775_807)
        if request.args.get("before_id")
        else None
    )
    messages = store.list_messages(
        conversation_id,
        after_id=_int_arg("after_id", 0, minimum=0, maximum=9_223_372_036_854_775_807),
        before_id=before_id,
        limit=limit,
    )
    return _json(
        {
            "messages": [_message_payload(row) for row in messages],
            "next_after_id": int(messages[-1]["id"]) if messages else None,
            "next_before_id": int(messages[0]["id"]) if messages else None,
            "has_more_before": bool(before_id is not None or not request.args.get("after_id"))
            and len(messages) >= limit,
        }
    )


@funnel_workspace_bp.get(f"{API_PREFIX}/messages/<int:message_id>/attachment")
def message_attachment(message_id: int) -> Response:
    force_download = request.args.get("download", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    return workspace_media.build_attachment_response(
        message_id,
        force_download=force_download,
    )


def _json_body() -> dict[str, Any]:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise store.WorkspaceValidationError("Тело запроса должно быть JSON-объектом.")
    return body


@funnel_workspace_bp.post(
    f"{API_PREFIX}/conversations/<int:conversation_id>/messages"
)
def message_create(conversation_id: int) -> tuple[Response, int]:
    body = _json_body()
    idempotency_key = (
        request.headers.get("Idempotency-Key", "").strip()
        or str(body.get("idempotency_key") or "").strip()
        or f"workspace:{uuid4()}"
    )
    result = store.enqueue_outgoing_operator(
        conversation_id,
        text=body.get("text"),
        expected_version=body.get("expected_version"),
        operator_name=workspace_operator_name(),
        idempotency_key=idempotency_key,
        metadata={"channel": "workspace_ui"},
    )
    return _json(result, 200 if result.get("duplicate") else 201)


@funnel_workspace_bp.post(
    f"{API_PREFIX}/conversations/<int:conversation_id>/control"
)
def conversation_control(conversation_id: int) -> tuple[Response, int]:
    body = _json_body()
    mode = str(body.get("mode") or "").strip().lower()
    if mode == "ai":
        current = store.get_conversation(conversation_id)
        if not _workspace_ai_allowed(current.get("external_user_id")):
            return _error(
                "ИИ пока не включён для этого тестового Telegram-диалога.",
                409,
                "ai_rollout_disabled",
            )
    permanent = bool(body.get("permanent"))
    default_reason = (
        "Оператор ведёт диалог сам — ИИ отключён в этом обращении."
        if permanent
        else "Переключено из рабочего окна."
    )
    conversation = store.transition_control(
        conversation_id,
        mode=mode,
        expected_version=body.get("expected_version"),
        actor_type="operator",
        actor_name=workspace_operator_name(),
        reason=str(body.get("reason") or default_reason),
        lease_seconds=(
            int(body["lease_seconds"])
            if body.get("lease_seconds") not in (None, "")
            else None
        ),
        permanent=permanent,
    )
    return _json({"conversation": _conversation_payload(dict(conversation))})


@funnel_workspace_bp.patch(f"{API_PREFIX}/messages/<int:message_id>")
def message_edit(message_id: int) -> tuple[Response, int]:
    """Изменить наш ответ у клиента в Telegram и в журнале.

    Сначала Telegram, потом база: иначе оператор увидит новый текст там, где у клиента
    остался старый, и будет уверен, что исправил.
    """
    import funnel_telegram_gateway

    body = _json_body()
    text = str(body.get("text") or "").strip()
    if not text:
        return _error("Пустой текст — нечего сохранять.", 400, "empty_text")

    preview = store.message_delivery_target(message_id)
    if str(preview.get("author_type")) not in {"agent", "operator"}:
        return _error(
            "Редактировать можно только наши сообщения.",
            409,
            "not_our_message",
        )
    try:
        applied_by = funnel_telegram_gateway.edit_delivered_message({**preview, "text": text})
    except RuntimeError as exc:
        return _error(str(exc), 409, "telegram_edit_failed")

    result = store.edit_outgoing_message(
        message_id,
        text=text,
        actor_name=workspace_operator_name(),
    )
    logger.info(
        "workspace message %s edited by %s (%s)",
        message_id,
        workspace_operator_name() or "неизвестный оператор",
        applied_by,
    )
    return _json({"message": _message_payload(result["message"]), "applied_by": applied_by})


@funnel_workspace_bp.delete(f"{API_PREFIX}/messages/<int:message_id>")
def message_delete(message_id: int) -> tuple[Response, int]:
    """Удалить сообщение у обеих сторон — или убрать из журнала, если оно не дошло.

    Ответ со статусом `failed`/`cancelled` клиент никогда не видел: удалять в Telegram
    нечего, а надгробие «[Сообщение удалено]» оставляло бы в переписке вечный след от
    несуществующего сообщения.
    """
    import funnel_telegram_gateway

    preview = store.message_delivery_target(message_id)
    if (
        str(preview.get("author_type")) in {"agent", "operator"}
        and str(preview.get("delivery_status") or "").strip().lower()
        in store.UNDELIVERED_STATUSES
    ):
        result = store.purge_undelivered_message(
            message_id,
            actor_name=workspace_operator_name(),
        )
        logger.warning(
            "workspace message %s purged as undelivered (%s) by %s",
            message_id,
            result.get("delivery_status"),
            workspace_operator_name() or "неизвестный оператор",
        )
        return _json(
            {
                "purged": True,
                "applied_by": "never_delivered",
                "conversation_id": result.get("conversation_id"),
                "delivery_status": result.get("delivery_status"),
            }
        )
    try:
        applied_by = funnel_telegram_gateway.delete_delivered_message(preview)
    except RuntimeError as exc:
        return _error(str(exc), 409, "telegram_delete_failed")

    result = store.delete_message_for_everyone(
        message_id,
        actor_name=workspace_operator_name(),
    )
    logger.warning(
        "workspace message %s deleted by %s (%s)",
        message_id,
        workspace_operator_name() or "неизвестный оператор",
        applied_by,
    )
    return _json({"message": _message_payload(result["message"]), "applied_by": applied_by})


@funnel_workspace_bp.delete(
    f"{API_PREFIX}/conversations/<int:conversation_id>"
)
def conversation_delete(conversation_id: int) -> tuple[Response, int]:
    """Удалить обращение вместе с перепиской. Подтверждение спрашивает интерфейс."""
    result = store.delete_conversation(conversation_id)
    logger.warning(
        "workspace conversation %s deleted by %s (%s messages)",
        conversation_id,
        workspace_operator_name() or "неизвестный оператор",
        result.get("messages"),
    )
    return _json(result)


@funnel_workspace_bp.post(
    f"{API_PREFIX}/conversations/<int:conversation_id>/stage"
)
def conversation_stage(conversation_id: int) -> tuple[Response, int]:
    """Перевести сделку на другой этап прямо из рабочего окна."""
    body = _json_body()
    known = {stage["value"] for stage in funnel_stages()}
    target_stage = str(body.get("stage") or "").strip()
    if target_stage not in known:
        return _error(
            "Неизвестный этап воронки.",
            400,
            "unknown_stage",
            details={"stage": target_stage},
        )
    result = store.enqueue_operator_stage_change(
        conversation_id,
        target_stage=target_stage,
        expected_version=body.get("expected_version"),
        operator_name=workspace_operator_name(),
    )
    return _json({"conversation": result["conversation"]})


@funnel_workspace_bp.patch(
    f"{API_PREFIX}/conversations/<int:conversation_id>"
)
def conversation_patch(conversation_id: int) -> tuple[Response, int]:
    body = _json_body()
    conversation = store.update_conversation_status(
        conversation_id,
        status=str(body.get("status") or ""),
        expected_version=body.get("expected_version"),
        actor_name=workspace_operator_name(),
    )
    return _json({"conversation": conversation})


@funnel_workspace_bp.post(
    f"{API_PREFIX}/conversations/<int:conversation_id>/read"
)
def conversation_read(conversation_id: int) -> tuple[Response, int]:
    body = _json_body()
    return _json(
        {
            "conversation": store.mark_read(
                conversation_id,
                through_message_id=body.get("through_message_id"),
            )
        }
    )


def _parse_export_datetime(name: str, *, end_of_day: bool = False) -> datetime | None:
    raw = request.args.get(name, "").strip()
    if not raw:
        return None
    try:
        if len(raw) == 10:
            parsed_date = date.fromisoformat(raw)
            parsed = datetime.combine(parsed_date, datetime_time.min, tzinfo=MOSCOW_TZ)
            if end_of_day:
                parsed += timedelta(days=1)
            return parsed.astimezone(timezone.utc)
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise store.WorkspaceValidationError(
            f"Параметр {name} должен быть датой ISO.",
            details={"field": name},
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _csv_safe(value: Any) -> str:
    if value is None:
        return ""
    text = _jsonable(value)
    text = str(text)
    if text.lstrip(" \t\r\n")[:1] in {"=", "+", "-", "@"}:
        return f"'{text}"
    return text


@funnel_workspace_bp.get(f"{API_PREFIX}/export.csv")
def messages_export() -> Response:
    rows = store.message_export_rows(
        q=request.args.get("q", ""),
        status=request.args.get("status", ""),
        stage=request.args.get("stage", ""),
        source=request.args.get("source", ""),
        author_type=request.args.get("author_type", ""),
        date_from=_parse_export_datetime("date_from"),
        date_to=_parse_export_datetime("date_to", end_of_day=True),
        limit=_int_arg("limit", 10_000, minimum=1, maximum=20_000),
    )
    fields = (
        "message_id",
        "occurred_at",
        "author_type",
        "author_name",
        "direction",
        "text",
        "delivery_status",
        "conversation_id",
        "source_key",
        "external_chat_id",
        "external_user_id",
        "username",
        "display_name",
        "deal_id",
        "funnel_id",
        "stage_id",
        "status",
        "control_mode",
    )
    def generate_csv():
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(fields)
        yield "\ufeff" + stream.getvalue()
        for row in rows:
            stream.seek(0)
            stream.truncate(0)
            writer.writerow([_csv_safe(row.get(field)) for field in fields])
            yield stream.getvalue()

    response = Response(
        generate_csv(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=funnel-workspace-messages.csv",
            "Cache-Control": "no-store",
        },
    )
    return response


@funnel_workspace_bp.errorhandler(store.WorkspaceStoreError)
def handle_store_error(exc: store.WorkspaceStoreError) -> tuple[Response, int]:
    return _error(
        str(exc),
        exc.status_code,
        exc.code,
        details=exc.details,
    )


@funnel_workspace_bp.errorhandler(workspace_media.AttachmentProxyError)
def handle_attachment_error(
    exc: workspace_media.AttachmentProxyError,
) -> tuple[Response, int]:
    return _error(str(exc), exc.status_code, exc.code)


@funnel_workspace_bp.errorhandler(Exception)
def handle_unexpected_error(exc: Exception) -> tuple[Response, int] | HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    logger.exception("Unhandled funnel workspace API error")
    return _error(
        "Внутренняя ошибка рабочего окна.",
        500,
        "internal_error",
    )
