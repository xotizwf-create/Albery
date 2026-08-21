"""Удаление аккаунта Авито: переписку молча не уносим.

Разговоры привязаны к аккаунту не внешним ключом, а строкой (business_connection_id),
поэтому база не помешает ни оставить их сиротами, ни снести заодно с аккаунтом. Решать
должен человек: переписка с клиентом существует в одном экземпляре, и «нажали удалить
аккаунт» — не согласие на её потерю.

Отсюда порядок: аккаунт с перепиской не удаляется, сервер отвечает 409 и ЧИСЛОМ разговоров,
и только по явному подтверждению уносит их вместе с аккаунтом.
"""
from __future__ import annotations

import pytest


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


def _account(**over):
    row = {"slug": "sklad", "label": "Склад", "profile_dir": None, "egress_label": "",
           "session_status": "unknown", "session_checked_at": None, "last_error": None,
           "is_active": False, "created_at": None, "updated_at": None,
           "login_requested_at": None, "login_requested_by": None}
    row.update(over)
    return row


class _Db:
    """Поддельная база: считает разговоры и запоминает, что удаляли."""

    def __init__(self, talks):
        self.talks = talks
        self.deleted = []

    def __enter__(self): return self
    def __exit__(self, *_e): return False
    def transaction(self): return self
    def cursor(self): return self

    def execute(self, sql, params=()):
        statement = " ".join(str(sql).split())
        if statement.startswith("DELETE FROM funnel_workspace_conversations"):
            self.deleted.append("переписка")
        elif statement.startswith("DELETE FROM avito_accounts"):
            self.deleted.append("аккаунт")

    def fetchone(self):
        return {"n": self.talks}


def test_an_empty_account_is_removed_at_once(avito, cabinet, monkeypatch):
    db = _Db(talks=0)
    monkeypatch.setattr(avito, "get_account", lambda slug: _account(slug=slug))
    monkeypatch.setattr(avito, "pg_connect", lambda: db)

    response = cabinet.delete("/api/agent-center/avito/accounts/sklad")

    assert response.status_code == 200
    assert response.get_json()["deleted"] is True
    assert db.deleted == ["аккаунт"], "удалять переписку было нечего"


def test_an_account_with_talks_is_not_removed_silently(avito, cabinet, monkeypatch):
    """Главный инвариант: переписка не исчезает от нажатия «удалить аккаунт»."""
    db = _Db(talks=17)
    monkeypatch.setattr(avito, "get_account", lambda slug: _account(slug=slug))
    monkeypatch.setattr(avito, "pg_connect", lambda: db)

    response = cabinet.delete("/api/agent-center/avito/accounts/sklad")

    assert response.status_code == 409
    assert response.get_json()["conversations"] == 17
    assert db.deleted == [], "ничего удалять было нельзя"


def test_the_refusal_says_how_much_will_be_lost(avito, cabinet, monkeypatch):
    """Человек решает по числу, а не по общей фразе «переписка будет удалена»."""
    monkeypatch.setattr(avito, "get_account", lambda slug: _account(slug=slug))
    monkeypatch.setattr(avito, "pg_connect", lambda: _Db(talks=17))

    response = cabinet.delete("/api/agent-center/avito/accounts/sklad")

    assert "17" in response.get_json()["error"]


def test_an_explicit_confirmation_removes_the_talks_too(avito, cabinet, monkeypatch):
    db = _Db(talks=17)
    monkeypatch.setattr(avito, "get_account", lambda slug: _account(slug=slug))
    monkeypatch.setattr(avito, "pg_connect", lambda: db)

    response = cabinet.delete("/api/agent-center/avito/accounts/sklad?with_conversations=1")

    assert response.status_code == 200
    assert db.deleted == ["переписка", "аккаунт"]


def test_deleting_an_unknown_account_is_a_clean_404(avito, cabinet, monkeypatch):
    monkeypatch.setattr(avito, "get_account", lambda slug: None)
    monkeypatch.setattr(avito, "pg_connect", lambda: pytest.fail("до базы дойти было нельзя"))

    response = cabinet.delete("/api/agent-center/avito/accounts/net-takogo")

    assert response.status_code == 404
