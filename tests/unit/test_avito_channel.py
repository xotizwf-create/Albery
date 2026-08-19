"""Канал Авито: страница, аккаунты и граница отправки.

Главный инвариант: пока браузерной сессии нет, ответ оператора НЕ уходит в очередь. Строка,
которую никто не разгребает, показала бы оператору «отправлено» и не доставила бы ничего —
это худший исход из возможных, поэтому он закрыт тестом, а не только текстом в интерфейсе.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture()
def avito(app_module):
    import avito_channel

    return avito_channel


@pytest.fixture()
def cabinet(client, monkeypatch):
    """Клиент кабинета в боевой конфигурации: JSON-API Центра Агента живёт за общим
    выключателем ALLOW_LEGACY_HTTP_API (на проде =1). Без него ЛЮБОЙ /api/ отдаёт 410,
    поэтому канал Авито не заводит себе отдельную дверь, а делит её со всем кабинетом."""
    monkeypatch.setenv("ALLOW_LEGACY_HTTP_API", "1")
    with client.session_transaction() as browser_session:
        browser_session["admin_authenticated"] = True
    return client


def _conversation(**over):
    row = {
        "id": 7,
        "account_slug": "main",
        "external_chat_id": "u2i-abc",
        "external_user_id": 4242,
        "username": "",
        "display_name": "Пётр",
        "status": "open",
        "control_mode": "ai",
        "unread_count": 2,
        "last_read_message_id": 0,
        "state_version": 3,
        "last_message_at": None,
        "last_message_text": "Здравствуйте, товар актуален?",
        "last_author_type": "client",
        "metadata": {"listing": {"id": "4123456789", "title": "SSD 1 ТБ",
                                 "url": "https://www.avito.ru/moskva/ssd_4123456789",
                                 "price": "5 400 ₽"}},
        "created_at": None,
    }
    row.update(over)
    return row


def _account(**over):
    row = {"slug": "main", "label": "Основной", "profile_dir": None,
           "egress_label": "компьютер владельца", "session_status": "ok",
           "session_checked_at": None, "last_error": None, "is_active": True,
           "created_at": None, "updated_at": None}
    row.update(over)
    return row


def test_page_and_api_routes_are_registered(avito, app_module):
    rules = {str(rule) for rule in app_module.app.url_map.iter_rules()}
    assert "/avito" in rules
    assert "/avito/<int:conversation_id>" in rules
    assert "/api/agent-center/avito/state" in rules
    assert "/api/agent-center/avito/conversations" in rules
    assert "/api/agent-center/avito/conversations/<int:conversation_id>/reply" in rules


def test_conversation_json_exposes_the_listing_behind_the_talk(avito):
    payload = avito._conversation_json(_conversation())

    assert payload["account_slug"] == "main"
    assert payload["listing"]["title"] == "SSD 1 ТБ"
    assert payload["listing"]["url"].endswith("ssd_4123456789")
    assert payload["state_version"] == 3


def test_conversation_json_survives_a_talk_without_a_listing(avito):
    payload = avito._conversation_json(_conversation(metadata={}, display_name="", username="ivan"))

    assert payload["listing"] == {"id": "", "title": "", "url": "", "price": ""}
    assert payload["display_name"] == "ivan"


def test_reply_is_refused_while_the_transport_is_off(avito, monkeypatch):
    monkeypatch.setattr(avito, "transport_enabled", lambda: False)
    monkeypatch.setattr(avito, "get_account", lambda slug: _account())

    reason = avito._delivery_block_reason(avito._conversation_json(_conversation()))

    assert reason is not None
    assert "AVITO_CHANNEL_ENABLED" in reason


@pytest.mark.parametrize(
    ("account", "expected"),
    [
        (None, "не зарегистрирован"),
        (_account(is_active=False), "выключен"),
        (_account(session_status="needs_login"), "нужен повторный вход"),
        (_account(session_status="blocked"), "заблокирован"),
        (_account(session_status="unknown"), "ещё не проверена"),
    ],
)
def test_reply_is_refused_until_the_account_session_is_alive(avito, monkeypatch, account, expected):
    monkeypatch.setattr(avito, "transport_enabled", lambda: True)
    monkeypatch.setattr(avito, "get_account", lambda slug: account)

    reason = avito._delivery_block_reason(avito._conversation_json(_conversation()))

    assert reason is not None and expected in reason


def test_live_session_and_enabled_transport_allow_the_reply(avito, monkeypatch):
    monkeypatch.setattr(avito, "transport_enabled", lambda: True)
    monkeypatch.setattr(avito, "get_account", lambda slug: _account())

    assert avito._delivery_block_reason(avito._conversation_json(_conversation())) is None


def test_reply_endpoint_returns_the_reason_and_never_queues(avito, cabinet, monkeypatch):
    calls = []
    monkeypatch.setattr(avito, "get_conversation",
                        lambda cid: avito._conversation_json(_conversation(id=cid)))
    monkeypatch.setattr(avito, "transport_enabled", lambda: False)
    monkeypatch.setattr(avito, "get_account", lambda slug: _account())
    monkeypatch.setattr(avito.store, "enqueue_outgoing_operator",
                        lambda *a, **k: calls.append(k) or {})

    response = cabinet.post("/api/agent-center/avito/conversations/7/reply",
                           json={"text": "Да, актуален"})

    assert response.status_code == 409
    assert response.get_json()["code"] == "avito_transport_unavailable"
    assert calls == []


def test_empty_reply_is_rejected_before_any_delivery_check(avito, cabinet, monkeypatch):
    monkeypatch.setattr(avito, "get_conversation",
                        lambda cid: avito._conversation_json(_conversation(id=cid)))

    response = cabinet.post("/api/agent-center/avito/conversations/7/reply", json={"text": "   "})

    assert response.status_code == 400


def test_conversations_query_is_scoped_to_avito_and_the_chosen_account(avito, fake_pg):
    cursor = fake_pg(avito, responder=lambda sql, params: {"total": 0, "unread": 0}
                     if "count(*)" in sql else [])

    avito.list_conversations(account="second", status="open", query="ssd")

    list_sql, list_params = cursor.executed[0]
    assert "c.source_key = %s" in list_sql
    assert "c.business_connection_id = %s" in list_sql
    assert list_params[0] == "avito" and list_params[1] == "second"
    # Поиск заглядывает в переписку, а не только в превью последнего сообщения.
    assert "funnel_workspace_messages" in list_sql


def test_account_slug_is_validated_before_the_database(avito, cabinet):
    response = cabinet.post("/api/agent-center/avito/accounts",
                           json={"slug": "Плохой Код", "label": "Тест"})

    assert response.status_code == 400
    assert "латиница" in response.get_json()["error"]


def test_migration_reuses_the_shared_journal_instead_of_copying_it():
    from scripts import ensure_postgres

    assert "090_avito_channel.sql" in ensure_postgres.ALWAYS_APPLY_MIGRATIONS
    sql = (REPO / "database" / "migrations" / "090_avito_channel.sql").read_text(encoding="utf-8")
    assert "INSERT INTO funnel_workspace_sources" in sql
    assert "CREATE TABLE IF NOT EXISTS avito_accounts" in sql
    # Ни одной своей таблицы диалогов/сообщений/очередей: канал живёт в таблицах 070.
    for table in ("avito_conversations", "avito_messages", "avito_outbox"):
        assert table not in sql


def test_transport_stays_off_until_it_is_switched_on(avito, monkeypatch):
    monkeypatch.delenv("AVITO_CHANNEL_ENABLED", raising=False)
    assert avito.transport_enabled() is False
    monkeypatch.setenv("AVITO_CHANNEL_ENABLED", "1")
    assert avito.transport_enabled() is True


def test_channel_api_shares_the_cabinet_switch(avito, client, monkeypatch):
    """Отдельной двери у канала нет: выключенный кабинетный API выключает и его."""
    monkeypatch.delenv("ALLOW_LEGACY_HTTP_API", raising=False)

    assert client.get("/api/agent-center/avito/state").status_code == 410


def test_frontend_serves_the_channel_on_its_own_route():
    main_tsx = (REPO / "Интерфейс" / "src" / "main.tsx").read_text(encoding="utf-8")
    app_tsx = (REPO / "Интерфейс" / "src" / "App.tsx").read_text(encoding="utf-8")

    assert "'/avito'" in main_tsx and "AvitoInbox" in main_tsx
    assert '"Авито": "/avito"' in app_tsx
    assert 'window.location.assign("/avito")' in app_tsx
