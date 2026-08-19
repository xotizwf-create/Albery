"""Написать первым: очередь заводит разговор, которого ещё нет.

Чат создаёт сам Авито в момент отправки, поэтому разговор живёт с временным ключом
`item:<id>`, а настоящий идентификатор приходит вместе с итогом доставки. Если его не
подставить, следующий обход завёл бы ВТОРОЙ разговор на того же человека.
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
    row = {"slug": "main", "label": "Основной", "profile_dir": None, "egress_label": "",
           "session_status": "ok", "session_checked_at": None, "last_error": None,
           "is_active": True, "created_at": None, "updated_at": None}
    row.update(over)
    return row


def _wire(avito, monkeypatch, *, transport=True, account=None):
    monkeypatch.setattr(avito, "transport_enabled", lambda: transport)
    monkeypatch.setattr(avito, "get_account", lambda slug: account if account is not None else _account())
    monkeypatch.setattr(avito.store, "ensure_source", lambda *a, **k: {"source_key": "avito"})
    monkeypatch.setattr(avito.store, "ensure_conversation",
                        lambda **kw: {"id": 77, "state_version": 1, "control_mode": "human"})
    monkeypatch.setattr(avito, "get_conversation", lambda cid: {"id": cid})


def test_outreach_queues_a_message_for_a_listing(avito, cabinet, monkeypatch):
    queued = {}
    _wire(avito, monkeypatch)
    monkeypatch.setattr(avito.store, "enqueue_outgoing_operator",
                        lambda cid, **kw: queued.update({"cid": cid, **kw}) or {"message": {"id": 5}})

    response = cabinet.post("/api/agent-center/avito/outreach",
                            json={"account": "main", "item_url": "https://www.avito.ru/kazan/kvartiry/7707314537",
                                  "text": "Здравствуйте! Квартира ещё сдаётся?"})

    assert response.status_code == 200
    assert queued["cid"] == 77
    assert queued["metadata"]["outreach_item"] == "7707314537"
    assert queued["text"].startswith("Здравствуйте")


def test_outreach_is_refused_without_a_live_session(avito, cabinet, monkeypatch):
    _wire(avito, monkeypatch, account=_account(session_status="needs_login"))
    monkeypatch.setattr(avito.store, "enqueue_outgoing_operator",
                        lambda *a, **k: pytest.fail("ставить в очередь было нельзя"))

    response = cabinet.post("/api/agent-center/avito/outreach",
                            json={"account": "main", "item_id": "7707314537", "text": "Привет"})

    assert response.status_code == 409
    assert response.get_json()["code"] == "avito_transport_unavailable"


def test_outreach_needs_a_listing_and_a_text(avito, cabinet, monkeypatch):
    _wire(avito, monkeypatch)

    no_item = cabinet.post("/api/agent-center/avito/outreach",
                           json={"account": "main", "text": "Привет"})
    no_text = cabinet.post("/api/agent-center/avito/outreach",
                           json={"account": "main", "item_id": "7707314537", "text": "  "})

    assert no_item.status_code == 400 and no_text.status_code == 400


def test_real_chat_id_replaces_the_placeholder_after_delivery(avito, monkeypatch):
    sql: list[str] = []
    args_seen: list[tuple] = []

    class _Cur:
        def __enter__(self): return self
        def __exit__(self, *_e): return False
        def execute(self, statement, params=()):
            sql.append(" ".join(statement.split()))
            args_seen.append(params)
        def fetchone(self): return None

    class _Conn:
        def __enter__(self): return self
        def __exit__(self, *_e): return False
        def transaction(self): return self
        def cursor(self): return _Cur()

    monkeypatch.setenv("AVITO_WORKER_TOKEN", "т")
    monkeypatch.setattr(avito, "pg_connect", lambda: _Conn())
    monkeypatch.setattr(avito.store, "finish_outbox", lambda *a, **k: None)

    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(avito.avito_bp)
    with app.test_client() as client:
        response = client.post("/api/avito-worker/outbox/5/result",
                               json={"worker_id": "w1", "result": "sent",
                                     "external_chat_id": "u2i-real"},
                               headers={"X-Avito-Worker-Token": "т"})

    assert response.status_code == 200
    joined = " | ".join(sql)
    assert "UPDATE funnel_workspace_conversations" in joined
    # %% — экранированный процент: psycopg считает одиночный % началом подстановки.
    assert "external_chat_id LIKE 'item:%%'" in joined
    assert args_seen[0][0] == "u2i-real"
