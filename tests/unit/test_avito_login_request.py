"""Вход по кнопке из кабинета: заявка вместо непонятного «воркер войдёт сам».

Кабинет — страница на сервере, и открыть браузер на компьютере человека он не может. А
войти в Авито можно только с домашнего адреса. До 21.08.2026 из этого следовал тупик:
кнопка «добавить аккаунт» была, аккаунт заводился, а войти было нечем — интерфейс писал
«вход выполняет воркер транспорта» и на этом всё. Владелец: «а что нажать-то, вообще
непонятно».

Теперь кнопка оставляет ЗАЯВКУ, а воркер, который и так работает на нужной машине, видит
её на ближайшем обходе и открывает окно там.

Ключевой инвариант: заявку снимает только ПОДТВЕРЖДЁННЫЙ вход. Снять её по факту «воркер
увидел» нельзя — человек мог не дойти до компьютера, и тогда заявка обязана дожить до
настоящего входа, а не исчезнуть после первой попытки.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

REQUESTED_AT = datetime(2026, 8, 21, 16, 30, tzinfo=timezone.utc)


@pytest.fixture()
def avito(app_module):
    import avito_channel

    return avito_channel


@pytest.fixture()
def cabinet(client, monkeypatch):
    monkeypatch.setenv("ALLOW_LEGACY_HTTP_API", "1")
    with client.session_transaction() as browser_session:
        browser_session["admin_authenticated"] = True
    return client


@pytest.fixture()
def worker(client, monkeypatch):
    monkeypatch.setenv("AVITO_WORKER_TOKEN", "тест-токен")
    return client


HEADERS = {"X-Avito-Worker-Token": "тест-токен"}


def _account(**over):
    row = {"slug": "sklad", "label": "Склад", "profile_dir": None, "egress_label": "",
           "session_status": "unknown", "session_checked_at": None, "last_error": None,
           "is_active": False, "created_at": None, "updated_at": None,
           "login_requested_at": None, "login_requested_by": None}
    row.update(over)
    return row


# --- Код аккаунта человек не придумывает ------------------------------------------------

@pytest.mark.parametrize("label,expected", [
    ("Отдел закупок", "otdel-zakupok"),
    ("Рабочий", "rabochiy"),
    ("  Склад  №1  ", "sklad-1"),
])
def test_the_code_comes_from_the_name(avito, label, expected):
    """Поле «код» в форме и было тем, на чём спотыкались."""
    assert avito.slug_from_label(label) == expected


def test_a_nameless_code_is_still_valid_for_the_server(avito):
    assert avito._SLUG_RE.match(avito.slug_from_label("!!!"))


def test_creating_an_account_needs_only_a_name(avito, cabinet, monkeypatch):
    created = {}
    monkeypatch.setattr(avito, "upsert_account",
                        lambda **kw: created.update(kw) or _account(slug=kw["slug"],
                                                                    label=kw["label"]))

    response = cabinet.post("/api/agent-center/avito/accounts", json={"label": "Отдел закупок"})

    assert response.status_code == 200
    assert created["slug"] == "otdel-zakupok"


# --- Заявка на вход ---------------------------------------------------------------------

def test_the_cabinet_button_leaves_a_request(avito, cabinet, monkeypatch):
    asked = {}
    monkeypatch.setattr(avito, "get_account", lambda slug: _account(slug=slug))
    monkeypatch.setattr(avito, "request_login",
                        lambda slug, **kw: asked.update({"slug": slug, **kw})
                        or _account(slug=slug, login_requested_at=REQUESTED_AT))

    response = cabinet.post("/api/agent-center/avito/accounts/sklad/login-request",
                            json={"operator_name": "Александр"})

    assert response.status_code == 200
    assert asked == {"slug": "sklad", "requested_by": "Александр"}


def test_a_request_for_an_unknown_account_is_refused(avito, cabinet, monkeypatch):
    monkeypatch.setattr(avito, "get_account", lambda slug: None)

    response = cabinet.post("/api/agent-center/avito/accounts/net-takogo/login-request", json={})

    assert response.status_code == 404


def test_the_worker_sees_only_accounts_that_still_need_a_login(avito, worker, monkeypatch):
    monkeypatch.setattr(avito, "pending_login_requests",
                        lambda: [_account(slug="sklad", login_requested_at=REQUESTED_AT)])

    response = worker.get("/api/avito-worker/login-requests", headers=HEADERS)

    assert response.status_code == 200
    assert [a["slug"] for a in response.get_json()["accounts"]] == ["sklad"]


def test_login_requests_need_the_worker_token(avito, client, monkeypatch):
    monkeypatch.setenv("AVITO_WORKER_TOKEN", "тест-токен")

    response = client.get("/api/avito-worker/login-requests",
                          headers={"X-Avito-Worker-Token": "не тот"})

    assert response.status_code == 403


def test_the_state_tells_the_cabinet_a_login_is_pending(avito, cabinet, monkeypatch):
    """Без этого поля человек не отличит «ждём вход» от «просто не проверена»."""
    monkeypatch.setattr(avito.store, "ensure_source", lambda *a, **k: None)
    monkeypatch.setattr(avito, "list_accounts",
                        lambda **kw: [_account(login_requested_at=REQUESTED_AT)])
    monkeypatch.setattr(avito, "list_conversations", lambda **kw: {"total": 0, "unread": 0})

    response = cabinet.get("/api/agent-center/avito/state")

    assert response.get_json()["accounts"][0]["login_requested_at"]
