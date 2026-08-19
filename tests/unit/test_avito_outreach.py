"""Написать первым: очередь заводит разговор, которого ещё нет.

Чат создаёт сам Авито в момент отправки, поэтому разговор живёт с временным ключом
`item:<id>`. Настоящий идентификатор приходит одним из двух путей: вместе с итогом доставки —
или, если воркер его не увидел (со страницы объявления перехода в чат не происходит), по
номеру объявления при зеркалировании. Без второго пути следующий обход заводил бы ВТОРОЙ
разговор на того же человека: один с временным ключом, другой настоящий.
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
    # Об этом объявлении ещё не говорим: разговор будет заведён с временным ключом.
    monkeypatch.setattr(avito, "find_conversation_by_listing", lambda **kw: None)
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


def test_outreach_writes_into_the_existing_chat_about_this_listing(avito, cabinet, monkeypatch):
    """Об объявлении уже говорим — пишем в тот же разговор, а не заводим второй."""
    queued = {}
    _wire(avito, monkeypatch)
    monkeypatch.setattr(avito, "find_conversation_by_listing",
                        lambda **kw: {"id": 700, "state_version": 4, "control_mode": "human"})
    monkeypatch.setattr(avito.store, "ensure_conversation",
                        lambda **kw: pytest.fail("второй разговор заводить было нельзя"))
    monkeypatch.setattr(avito.store, "enqueue_outgoing_operator",
                        lambda cid, **kw: queued.update({"cid": cid, **kw}) or {"message": {"id": 9}})

    response = cabinet.post("/api/agent-center/avito/outreach",
                            json={"account": "main", "item_id": "4297041572",
                                  "text": "Здравствуйте! Ещё продаёте?"})

    assert response.status_code == 200
    assert queued["cid"] == 700


class _Recorder:
    """Поддельная база: отвечает на поиск разговора заранее известными строками.

    Ключ — внешний идентификатор чата, как в самом запросе: так тест проверяет ПОВЕДЕНИЕ
    (что и с чем сшивается), а не текст SQL.
    """

    def __init__(self, existing: dict[str, dict]):
        self.existing = existing
        self.calls: list[tuple[str, tuple]] = []
        self._last = None

    # --- интерфейс курсора/соединения psycopg -------------------------------------------
    def __enter__(self): return self
    def __exit__(self, *_e): return False
    def transaction(self): return self
    def cursor(self): return self

    def execute(self, sql, params=()):
        statement = " ".join(str(sql).split())
        self.calls.append((statement, tuple(params or ())))
        self._last = None
        if "FROM funnel_workspace_conversations" in statement and "external_chat_id = %s" in statement:
            self._last = self.existing.get(params[2])

    def fetchone(self): return self._last
    def fetchall(self): return [self._last] if self._last else []

    def updates(self, table: str) -> list[tuple[str, tuple]]:
        return [(sql, args) for sql, args in self.calls if sql.startswith(f"UPDATE {table}")]


def _recorder(avito, monkeypatch, existing):
    recorder = _Recorder(existing)
    monkeypatch.setattr(avito, "pg_connect", lambda: recorder)
    return recorder


def test_the_temporary_conversation_is_stitched_by_the_listing_number(avito, monkeypatch):
    """Живой случай 19.08.2026: сообщение ушло, а разговор 648 остался на `item:4297041572`."""
    recorder = _recorder(avito, monkeypatch, {"item:4297041572": {"id": 648}})

    stitched = avito.stitch_outreach_conversation(account="main", listing_id="4297041572",
                                                  chat_id="u2i-nastoyaschiy")

    assert stitched == {"action": "adopted", "conversation_id": 648,
                        "external_chat_id": "u2i-nastoyaschiy"}
    conversations = recorder.updates("funnel_workspace_conversations")
    assert conversations and conversations[0][1] == ("u2i-nastoyaschiy", 648)
    # Неотправленное письмо теперь уйдёт прямо в чат, а не через страницу объявления.
    assert recorder.updates("funnel_workspace_outbox")[0][1] == ("u2i-nastoyaschiy", 648)


def test_mirroring_stitches_instead_of_starting_a_second_conversation(avito, monkeypatch):
    """Тот же случай на полном пути зеркалирования, а не только в самой сшивке."""
    recorder = _recorder(avito, monkeypatch, {"item:4297041572": {"id": 648}})
    monkeypatch.setattr(avito.store, "ensure_source", lambda *a, **k: None)
    monkeypatch.setattr(avito.store, "ensure_conversation", lambda **kw: {"id": 648})

    answer = avito.ingest_inbound({
        "account": "main", "external_chat_id": "u2i-nastoyaschiy",
        "listing": {"id": "4297041572", "title": "Генератор бензиновый 3,3 кВт"},
        "messages": [],
    })

    assert answer["stitched"]["action"] == "adopted"
    assert recorder.updates("funnel_workspace_conversations")[0][1] == ("u2i-nastoyaschiy", 648)


def test_a_duplicate_conversation_is_folded_into_the_real_one(avito, monkeypatch):
    """Дубль уже успел завестись — переписка временного переезжает в настоящий разговор."""
    recorder = _recorder(avito, monkeypatch, {
        "item:4297041572": {"id": 648},
        "u2i-nastoyaschiy": {"id": 700, "state_version": 4},
    })

    stitched = avito.stitch_outreach_conversation(account="main", listing_id="4297041572",
                                                  chat_id="u2i-nastoyaschiy")

    assert stitched["action"] == "merged" and stitched["conversation_id"] == 700
    assert recorder.updates("funnel_workspace_messages")[0][1] == (700, 648, 700)
    # Временный ключ уходит в отставку, иначе следующий обход сшивал бы его снова и снова.
    retired = recorder.updates("funnel_workspace_conversations")[0]
    assert retired[1] == ("merged:u2i-nastoyaschiy", 700, 648)
    assert "merged_into" in retired[0]


def test_a_chat_without_a_pending_outreach_is_left_alone(avito, monkeypatch):
    recorder = _recorder(avito, monkeypatch, {})

    assert avito.stitch_outreach_conversation(account="main", listing_id="8288883000",
                                              chat_id="u2i-chuzhoy") is None
    assert not recorder.updates("funnel_workspace_conversations")


def test_a_chat_without_a_listing_is_left_alone(avito, monkeypatch):
    """Служебные чаты Авито идут без объявления — сшивать их не по чему."""
    recorder = _recorder(avito, monkeypatch, {"item:4297041572": {"id": 648}})

    assert avito.stitch_outreach_conversation(account="main", listing_id="",
                                              chat_id="a2u-145964554-198797068") is None
    assert not recorder.calls


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
