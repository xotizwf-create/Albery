"""Дверь воркера Авито: токен, идемпотентность входящих, граница отправки.

Воркер приходит с ЧУЖОЙ машины, поэтому дверь проверяется отдельно от кабинета: без токена
её быть не должно вовсе, а повторная доставка одного и того же пакета не должна создавать
оператору дубли переписки.
"""
from __future__ import annotations

import pytest


@pytest.fixture()
def avito(app_module):
    import avito_channel

    return avito_channel


@pytest.fixture()
def worker(client, monkeypatch):
    monkeypatch.setenv("AVITO_WORKER_TOKEN", "тест-токен")
    return client


HEADERS = {"X-Avito-Worker-Token": "тест-токен"}


def test_worker_door_is_closed_without_a_configured_token(avito, client, monkeypatch):
    monkeypatch.delenv("AVITO_WORKER_TOKEN", raising=False)

    response = client.post("/api/avito-worker/session",
                           json={"account": "main", "status": "ok"},
                           headers={"X-Avito-Worker-Token": "что угодно"})

    assert response.status_code == 403


def test_worker_door_rejects_a_wrong_token(avito, worker):
    response = worker.post("/api/avito-worker/session",
                           json={"account": "main", "status": "ok"},
                           headers={"X-Avito-Worker-Token": "не тот"})

    assert response.status_code == 403


def test_worker_door_does_not_need_the_cabinet_session(avito, worker, monkeypatch):
    """Ключевое: у воркера нет сессии кабинета, и общий рубильник /api его не отрезает."""
    monkeypatch.delenv("ALLOW_LEGACY_HTTP_API", raising=False)
    monkeypatch.setattr(avito, "set_session_status",
                        lambda slug, **kw: {"slug": slug, "session_status": kw["status"]})

    response = worker.post("/api/avito-worker/session",
                           json={"account": "main", "status": "ok"}, headers=HEADERS)

    assert response.status_code == 200
    assert response.get_json()["session_status"] == "ok"


def test_unknown_session_status_is_refused(avito, worker):
    response = worker.post("/api/avito-worker/session",
                           json={"account": "main", "status": "всё хорошо"}, headers=HEADERS)

    assert response.status_code == 400


def test_inbound_requires_account_and_chat(avito, worker):
    response = worker.post("/api/avito-worker/inbound",
                           json={"messages": []}, headers=HEADERS)

    assert response.status_code == 400
    assert "external_chat_id" in response.get_json()["error"]


def test_inbound_stores_the_talk_and_survives_a_repeat(avito, worker, monkeypatch):
    seen_sql: list[str] = []

    class _Cur:
        def __enter__(self): return self
        def __exit__(self, *_e): return False
        def execute(self, sql, args=()): seen_sql.append(" ".join(sql.split()))
        def fetchone(self): return {"id": 1}

    class _Conn:
        def __enter__(self): return self
        def __exit__(self, *_e): return False
        def transaction(self): return self
        def cursor(self): return _Cur()

    monkeypatch.setattr(avito, "pg_connect", lambda: _Conn())
    monkeypatch.setattr(avito.store, "ensure_source", lambda *a, **k: {"source_key": "avito"})
    monkeypatch.setattr(avito.store, "ensure_conversation", lambda **kw: {"id": 42})

    payload = {
        "account": "main", "external_chat_id": "u2i-abc", "display_name": "Пётр",
        "update_id": "u2i-abc:17", "listing": {"id": "4123456789", "title": "SSD 1 ТБ"},
        "messages": [{"external_message_id": "m1", "text": "Товар актуален?",
                      "author_type": "client"}],
    }
    response = worker.post("/api/avito-worker/inbound", json=payload, headers=HEADERS)

    assert response.status_code == 200
    assert response.get_json()["conversation_id"] == 42
    joined = " | ".join(seen_sql)
    # Сырой пакет и каждое сообщение защищены от повторной доставки на уровне базы.
    assert "ON CONFLICT (source_key, external_update_id) DO NOTHING" in joined
    assert "ON CONFLICT (conversation_id, external_message_id) DO NOTHING" in joined
    # Счётчик непрочитанного пересобирается по переписке, а не увеличивается на веру.
    assert "unread_count = unread.n" in joined


def test_claim_returns_only_this_channel_and_frees_the_rest(avito, worker, monkeypatch):
    released: list[tuple[int, str]] = []
    monkeypatch.setattr(avito.store, "claim_outbox", lambda **kw: [
        {"id": 1, "conversation_id": 10, "source_key": "avito", "business_connection_id": "main",
         "external_chat_id": "u2i-a", "text": "Здравствуйте", "author_type": "operator"},
        {"id": 2, "conversation_id": 11, "source_key": "telegram", "business_connection_id": "",
         "external_chat_id": "77", "text": "чужое", "author_type": "agent"},
        {"id": 3, "conversation_id": 12, "source_key": "avito", "business_connection_id": "second",
         "external_chat_id": "u2i-b", "text": "другой аккаунт", "author_type": "operator"},
    ])
    monkeypatch.setattr(avito.store, "finish_outbox",
                        lambda outbox_id, **kw: released.append((outbox_id, kw.get("result"))))

    response = worker.post("/api/avito-worker/outbox/claim",
                           json={"worker_id": "w1", "account": "main"}, headers=HEADERS)

    items = response.get_json()["items"]
    assert [item["outbox_id"] for item in items] == [1]
    # Чужие строки не зависают под нашей арендой — их сразу возвращают в очередь.
    assert sorted(released) == [(2, "pending"), (3, "pending")]


def test_claim_demands_a_worker_id_because_the_lease_hangs_on_it(avito, worker):
    response = worker.post("/api/avito-worker/outbox/claim", json={}, headers=HEADERS)

    assert response.status_code == 400


def test_sending_marks_the_side_effect_boundary_before_the_call(avito, worker, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(avito.store, "outbox_send_guard", lambda *a, **k: {"allowed": True})
    monkeypatch.setattr(avito.store, "begin_outbox_send",
                        lambda *a, **k: calls.append("begin"))

    response = worker.post("/api/avito-worker/outbox/5/sending",
                           json={"worker_id": "w1"}, headers=HEADERS)

    assert response.status_code == 200 and response.get_json()["allowed"] is True
    assert calls == ["begin"]


def test_rejected_guard_cancels_instead_of_sending(avito, worker, monkeypatch):
    finished: list[dict] = []
    monkeypatch.setattr(avito.store, "outbox_send_guard",
                        lambda *a, **k: {"allowed": False, "reason": "control_changed"})
    monkeypatch.setattr(avito.store, "begin_outbox_send",
                        lambda *a, **k: pytest.fail("отправку начинать было нельзя"))
    monkeypatch.setattr(avito.store, "finish_outbox",
                        lambda outbox_id, **kw: finished.append(kw))

    response = worker.post("/api/avito-worker/outbox/5/sending",
                           json={"worker_id": "w1"}, headers=HEADERS)

    assert response.status_code == 409
    assert finished and finished[0]["result"] == "cancelled"


@pytest.mark.parametrize("result", ["sent", "failed", "unknown"])
def test_worker_reports_every_delivery_outcome(avito, worker, monkeypatch, result):
    recorded: list[str] = []
    monkeypatch.setattr(avito.store, "finish_outbox",
                        lambda outbox_id, **kw: recorded.append(kw["result"]))

    response = worker.post("/api/avito-worker/outbox/5/result",
                           json={"worker_id": "w1", "result": result}, headers=HEADERS)

    assert response.status_code == 200
    assert recorded == [result]


def test_invented_outcome_is_refused(avito, worker):
    response = worker.post("/api/avito-worker/outbox/5/result",
                           json={"worker_id": "w1", "result": "наверное дошло"}, headers=HEADERS)

    assert response.status_code == 400
