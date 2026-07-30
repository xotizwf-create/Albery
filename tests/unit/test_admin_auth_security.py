"""Границы доступа в Albery: что закрыто паролем, как блокируется подбор, чем живёт сессия.

Задача владельца 30.07.2026: посторонний не должен попасть ни на страницы управления, ни в
кабинет WB; открыт только калькулятор. Пять неверных паролей — час блокировки с ответом 429.

Каждая проверка здесь закрепляет уже разобранный случай, поэтому следующая правка не сможет
тихо открыть дверь: список исключений из-под пароля сверяется целиком, а не по одной строке.
"""
from __future__ import annotations

import time

import pytest
from werkzeug.security import generate_password_hash

import auth_lockout


PASSWORD = "правильный-пароль-для-теста"
PASSWORD_HASH = generate_password_hash(PASSWORD)

# Страницы, за которыми стоит управление системой и кабинет WB. Ни одна из них не
# открывается без пароля — ни целиком, ни отдельным файлом сборки.
PROTECTED_PATHS = [
    "/main",
    "/prompts",
    "/settings",
    "/chats",
    "/zoom",
    "/reports",
    "/analytics",
    "/Analytics",
    "/analytics/index.html",
    "/analytics/assets/index.js",
]

# Ровно тот список, который сейчас разрешено открывать без пароля. Тест сверяет его
# целиком: добавить сюда страницу управления, не заметив этого, не получится.
EXPECTED_EXEMPT_ROUTES = {
    "/login",
    "/logout",
    "/mcp",
    "/mcp-faq",
    "/mcp-ops",
    "/mcp-core",
    "/mcp-ops-core",
    "/sse",
    "/sse-faq",
    "/sse-ops",
    "/favicon.ico",
    "/favicon.svg",
    "/favicon-16x16.png",
    "/favicon-32x32.png",
    "/favicon-64x64.png",
    "/Калькулятор",
}

EXPECTED_EXEMPT_PREFIXES = {
    "/assets/",
    "/mcp/",
    "/mcp-faq/",
    "/mcp-ops/",
    "/mcp-core/",
    "/mcp-ops-core/",
    "/mcp-agent/",
    "/sse/",
    "/sse-faq/",
    "/sse-ops/",
    "/bitrix/events/",
    "/bitrix/imbot/",
    "/zoom/events/",
    "/google-drive/events/",
    "/zoom-export/",
    "/applet/",
    "/Калькулятор/",
    "/iu/",
}


@pytest.fixture()
def admin_env(monkeypatch, tmp_path):
    """Заданный пароль администратора и счётчик блокировок в отдельной папке теста."""
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", PASSWORD_HASH)
    monkeypatch.setenv("AUTH_LOCKOUT_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("AUTH_RATE_LIMIT_ATTEMPTS", raising=False)
    monkeypatch.delenv("AUTH_RATE_LIMIT_WINDOW_SECONDS", raising=False)
    auth_lockout.admin_lockout.reset_all()
    yield
    auth_lockout.admin_lockout.reset_all()


# --------------------------------------------------------------------------- доступ
@pytest.mark.parametrize("path", PROTECTED_PATHS)
def test_management_and_wb_cabinet_are_closed_without_password(client, path):
    response = client.get(path)
    assert response.status_code == 302, f"{path} отдался без пароля ({response.status_code})"
    assert "/login" in response.headers.get("Location", ""), f"{path} не отправил на вход"


def test_calculator_stays_open_without_password(client):
    response = client.get("/Калькулятор/")
    assert response.status_code != 302 or "/login" not in response.headers.get("Location", "")


def test_api_without_session_never_returns_data(client):
    """На проде это 401; в тестовой среде ключ сессии короткий и окно отвечает 503.

    Проверяется само свойство, а не код: без входа переписка не отдаётся никогда.
    """
    response = client.get("/api/funnel-workspace/conversations")
    assert response.status_code != 200
    assert "conversations" not in response.get_data(as_text=True)


def test_exempt_list_is_exactly_the_reviewed_one(app_module):
    """Список исключений — согласованный, а не растущий сам собой."""
    assert set(app_module.AUTH_EXEMPT_ROUTES) == EXPECTED_EXEMPT_ROUTES
    assert set(app_module.AUTH_EXEMPT_PREFIXES) == EXPECTED_EXEMPT_PREFIXES


def test_exempt_prefixes_do_not_open_management_paths(app_module):
    for path in PROTECTED_PATHS:
        assert not app_module.auth_exempt_path(path), f"{path} попал в исключения"


# --------------------------------------------------------------------------- блокировка
def test_policy_is_five_attempts_and_one_hour(admin_env):
    window, attempts = auth_lockout.admin_lockout.settings()
    assert (window, attempts) == (3600, 5)


def test_sixth_wrong_password_is_blocked_with_429_for_an_hour(client, admin_env):
    for number in range(5):
        response = client.post("/login", data={"password": "неверный"})
        assert response.status_code == 401, f"попытка {number + 1} должна отвечать 401"

    blocked = client.post("/login", data={"password": "неверный"})
    assert blocked.status_code == 429
    retry_after = int(blocked.headers["Retry-After"])
    assert 3540 <= retry_after <= 3600, f"блокировка не на час: {retry_after} с"


def test_correct_password_during_lockout_is_still_refused(client, admin_env):
    for _ in range(5):
        client.post("/login", data={"password": "неверный"})
    response = client.post("/login", data={"password": PASSWORD})
    assert response.status_code == 429
    with client.session_transaction() as session_data:
        assert not session_data.get("admin_authenticated")


def test_attempts_during_lockout_do_not_extend_it(client, admin_env):
    for _ in range(5):
        client.post("/login", data={"password": "неверный"})
    first = int(client.post("/login", data={"password": "x"}).headers["Retry-After"])
    for _ in range(10):
        client.post("/login", data={"password": "x"})
    later = int(client.post("/login", data={"password": "x"}).headers["Retry-After"])
    assert later <= first, "долбёжка в закрытую дверь продлевает блокировку"


def test_successful_login_clears_the_counter(client, admin_env):
    for _ in range(4):
        client.post("/login", data={"password": "неверный"})
    assert client.post("/login", data={"password": PASSWORD}).status_code == 302
    for _ in range(4):
        assert client.post("/login", data={"password": "неверный"}).status_code == 401


def test_lockout_survives_a_service_restart(tmp_path, monkeypatch):
    """Деплой не должен снимать блокировку с того, кто подбирает пароль."""
    monkeypatch.delenv("AUTH_RATE_LIMIT_ATTEMPTS", raising=False)
    monkeypatch.delenv("AUTH_RATE_LIMIT_WINDOW_SECONDS", raising=False)
    before = auth_lockout.LoginLockout(
        "restart-check",
        attempts_env="AUTH_RATE_LIMIT_ATTEMPTS",
        window_env="AUTH_RATE_LIMIT_WINDOW_SECONDS",
        state_path=str(tmp_path),
    )
    for _ in range(5):
        before.record_failure("203.0.113.9")
    assert before.check("203.0.113.9")[0]

    after_restart = auth_lockout.LoginLockout(
        "restart-check",
        attempts_env="AUTH_RATE_LIMIT_ATTEMPTS",
        window_env="AUTH_RATE_LIMIT_WINDOW_SECONDS",
        state_path=str(tmp_path),
    )
    blocked, retry_after = after_restart.check("203.0.113.9")
    assert blocked, "после перезапуска блокировка потерялась"
    assert retry_after > 3500


def test_lockout_is_per_address(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTH_RATE_LIMIT_ATTEMPTS", raising=False)
    monkeypatch.delenv("AUTH_RATE_LIMIT_WINDOW_SECONDS", raising=False)
    lockout = auth_lockout.LoginLockout(
        "per-address",
        attempts_env="AUTH_RATE_LIMIT_ATTEMPTS",
        window_env="AUTH_RATE_LIMIT_WINDOW_SECONDS",
        state_path=str(tmp_path),
    )
    for _ in range(5):
        lockout.record_failure("198.51.100.1")
    assert lockout.check("198.51.100.1")[0]
    assert not lockout.check("198.51.100.2")[0], "заблокирован посторонний адрес"


def test_expired_attempts_release_the_address(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_RATE_LIMIT_WINDOW_SECONDS", "60")
    monkeypatch.delenv("AUTH_RATE_LIMIT_ATTEMPTS", raising=False)
    lockout = auth_lockout.LoginLockout(
        "expiry",
        attempts_env="AUTH_RATE_LIMIT_ATTEMPTS",
        window_env="AUTH_RATE_LIMIT_WINDOW_SECONDS",
        state_path=str(tmp_path),
    )
    stale = time.time() - 120
    lockout._attempts["192.0.2.5"] = [stale] * 5
    lockout._loaded = True
    assert not lockout.check("192.0.2.5")[0], "старые попытки продолжают держать блокировку"


# --------------------------------------------------------------------------- сессия
def test_session_cookie_is_https_only_and_not_readable_by_scripts(app_module):
    assert app_module.app.config["SESSION_COOKIE_SECURE"] is True
    assert app_module.app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app_module.app.config["SESSION_COOKIE_SAMESITE"] == "Lax"


def test_changing_the_password_invalidates_issued_sessions(client, admin_env, monkeypatch):
    """Смена пароля обязана выбрасывать всех, у кого уже есть cookie."""
    assert client.post("/login", data={"password": PASSWORD}).status_code == 302
    assert client.get("/main").status_code != 302

    monkeypatch.setenv("ADMIN_PASSWORD_HASH", generate_password_hash("новый-пароль"))
    response = client.get("/main")
    assert response.status_code == 302
    assert "/login" in response.headers.get("Location", "")


def test_session_older_than_the_limit_is_refused(client, admin_env, monkeypatch):
    assert client.post("/login", data={"password": PASSWORD}).status_code == 302
    with client.session_transaction() as session_data:
        session_data["admin_authenticated_at"] = time.time() - 31 * 86_400
    response = client.get("/main")
    assert response.status_code == 302
    assert "/login" in response.headers.get("Location", "")


def test_session_without_a_password_fingerprint_is_refused(client, admin_env):
    """Cookie, выданная до этой правки (без отпечатка), не считается входом."""
    with client.session_transaction() as session_data:
        session_data["admin_authenticated"] = True
    response = client.get("/main")
    assert response.status_code == 302
    assert "/login" in response.headers.get("Location", "")
