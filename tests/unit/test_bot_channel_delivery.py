from __future__ import annotations

# Диалог, пришедший в бота напрямую (а не через бизнес-аккаунт менеджера), отвечается
# обычным сообщением бота: бизнес-подключения у такого чата нет и быть не может.

import sys
from types import SimpleNamespace

import pytest

import funnel_telegram_gateway as gateway


class FakeStore:
    def __init__(self, item):
        self.item = item
        self.finishes: list[tuple[int, dict]] = []

    def outbox_send_guard(self, _outbox_id, *, worker_id):
        return {"allowed": True, "outbox": self.item}

    def begin_outbox_send(self, _outbox_id, *, worker_id, lease_seconds):
        self.item = {**self.item, "delivery_status": "sending"}
        return {"allowed": True, "outbox": self.item}

    def finish_outbox(self, outbox_id, **kwargs):
        self.finishes.append((outbox_id, kwargs))
        return {"outbox": {**self.item, "delivery_status": kwargs["result"]}, "message": {}}


def _bot_outbox(**overrides):
    item = {
        "id": 11,
        "message_id": 91,
        "conversation_id": 7,
        "conversation_version": 3,
        "author_type": "operator",
        "source_key": "telegram_bot",
        "external_chat_id": "555",
        "business_connection_id": "",
        "text": "Здравствуйте! Отвечаю по вашему вопросу.",
        "payload": {},
    }
    item.update(overrides)
    return item


def test_reply_to_a_bot_chat_goes_out_without_a_business_connection(monkeypatch):
    item = _bot_outbox()
    store = FakeStore(item)
    calls: list[tuple[str, dict]] = []

    def api(method, **kwargs):
        calls.append((method, kwargs))
        return {"message_id": 777}

    tg = SimpleNamespace(
        _business_connection_id=lambda preferred: ("", "бизнес-подключение не найдено"),
        api=api,
    )
    monkeypatch.setitem(sys.modules, "funnel_workspace_store", store)
    monkeypatch.setitem(sys.modules, "tg_agent", tg)

    gateway._process_outbox_item(item, worker_id="worker")

    assert calls, "ответ клиенту обязан уйти, а не упасть на отсутствии бизнес-подключения"
    method, kwargs = calls[0]
    assert method == "sendMessage"
    assert kwargs["chat_id"] == 555
    # Telegram отвергает business_connection_id в обычном чате — его тут быть не должно.
    assert "business_connection_id" not in kwargs
    assert store.finishes[0][1]["result"] == "sent"
    assert store.finishes[0][1]["provider_message_id"] == "777"


def test_business_dialog_still_requires_its_connection(monkeypatch):
    # Регресс: у диалога бизнес-аккаунта отсутствие подключения — по-прежнему отказ,
    # иначе ответ уйдёт «от бота», а не от аккаунта менеджера, и клиент это увидит.
    item = _bot_outbox(source_key="telegram", business_connection_id="conn-A")
    store = FakeStore(item)

    tg = SimpleNamespace(
        _business_connection_id=lambda preferred: ("", "бизнес-подключение не найдено"),
        api=lambda *_a, **_k: pytest.fail("без подключения отправлять нельзя"),
    )
    monkeypatch.setitem(sys.modules, "funnel_workspace_store", store)
    monkeypatch.setitem(sys.modules, "tg_agent", tg)

    gateway._process_outbox_item(item, worker_id="worker")

    assert store.finishes[0][1]["result"] == "failed"
    assert "подключени" in str(store.finishes[0][1]["error"]).lower()


def test_file_to_a_bot_chat_also_goes_without_a_connection(tmp_path, monkeypatch):
    import funnel_workspace_uploads as uploads

    document = tmp_path / "file.bin"
    document.write_bytes(b"%PDF-1.4 x")
    item = _bot_outbox(payload={"outgoing_file": {
        "token": "tok", "file_name": "Условия.pdf",
        "mime_type": "application/pdf", "file_size": 10,
    }})
    store = FakeStore(item)
    calls: list[tuple[str, dict]] = []

    tg = SimpleNamespace(
        _business_connection_id=lambda preferred: ("", "нет подключения"),
        api_multipart=lambda method, **kwargs: (
            calls.append((method, kwargs)) or {"message_id": 778, "document": {"file_id": "F1"}}
        ),
    )
    monkeypatch.setitem(sys.modules, "funnel_workspace_store", store)
    monkeypatch.setitem(sys.modules, "tg_agent", tg)
    monkeypatch.setattr(uploads, "resolve_upload", lambda _token: {
        "token": "tok", "path": document, "file_name": "Условия.pdf",
        "mime_type": "application/pdf", "file_size": 10,
    })

    gateway._process_outbox_item(item, worker_id="worker")

    method, kwargs = calls[0]
    assert method == "sendDocument"
    assert "business_connection_id" not in kwargs
    assert store.finishes[0][1]["result"] == "sent"


def test_migration_registers_the_bot_source():
    from pathlib import Path

    migration = (Path(__file__).resolve().parents[2] / "database" / "migrations"
                 / "074_workspace_bot_source.sql").read_text(encoding="utf-8")
    assert "INSERT INTO funnel_workspace_sources" in migration
    assert "'telegram_bot'" in migration
    assert "ON CONFLICT (source_key) DO NOTHING" in migration

    ensure = (Path(__file__).resolve().parents[2] / "scripts" / "ensure_postgres.py").read_text(
        encoding="utf-8")
    # Строка-справочник в существующей таблице: без этого списка на проде её бы не появилось.
    assert "074_workspace_bot_source.sql" in ensure


def test_attachment_from_a_bot_chat_is_served(monkeypatch):
    import funnel_workspace_media as media

    from contextlib import contextmanager

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, *_args):
            pass

        def fetchone(self):
            return {
                "metadata": {"telegram_media": {"file_id": "F1", "file_name": "счёт.pdf"}},
                "source_key": "telegram_bot",
            }

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return Cursor()

    descriptor, file_id = media._load_message_media(42, connect=lambda: Conn())

    assert file_id == "F1"
    assert descriptor["file_name"] == "счёт.pdf"


# --- приём: клиент написал боту напрямую ------------------------------------------------


class _IngestStore:
    def __init__(self):
        self.calls: list[dict] = []

    def ingest_business_message(self, **kwargs):
        self.calls.append(kwargs)
        return {"conversation": {"id": 5}, "message": {"id": 50}, "duplicate": False}


def _client_update(text="Здравствуйте, расскажите про ИУ"):
    return {
        "message": {
            "message_id": 4,
            "date": 1785600000,
            "chat": {"id": 555, "type": "private", "username": "client"},
            "from": {"id": 555, "first_name": "Пётр", "username": "client"},
            "text": text,
        }
    }


def _tg_stub(monkeypatch, *, owner=False):
    tg = SimpleNamespace(
        is_owner=lambda _sender: owner,
        _workers=SimpleNamespace(submit=lambda *_a: None),
        _handle_update_safely=lambda _u: None,
    )
    monkeypatch.setitem(sys.modules, "tg_agent", tg)
    return tg


def test_client_message_to_the_bot_reaches_the_workspace(monkeypatch):
    monkeypatch.setenv("IU_CLIENT_BOT_ENABLED", "1")
    store = _IngestStore()
    monkeypatch.setattr(gateway, "_store", lambda: store)
    _tg_stub(monkeypatch)

    gateway.route_captured_update(_client_update(), provider_update_id=99)

    assert store.calls, "сообщение клиента боту обязано попасть в рабочее окно"
    call = store.calls[0]
    assert call["source_key"] == "telegram_bot"
    assert call["business_connection_id"] == ""
    assert call["author_type"] == "client"
    assert call["external_chat_id"] == "555"
    assert call["external_user_id"] == 555
    assert call["text"] == "Здравствуйте, расскажите про ИУ"


def test_client_bot_channel_is_off_until_it_is_switched_on(monkeypatch):
    monkeypatch.delenv("IU_CLIENT_BOT_ENABLED", raising=False)
    store = _IngestStore()
    monkeypatch.setattr(gateway, "_store", lambda: store)
    _tg_stub(monkeypatch)

    gateway.route_captured_update(_client_update())

    assert store.calls == [], "пока канал не включён, поведение системы не меняется"


def test_owner_dm_still_goes_to_the_internal_channel(monkeypatch):
    monkeypatch.setenv("IU_CLIENT_BOT_ENABLED", "1")
    store = _IngestStore()
    monkeypatch.setattr(gateway, "_store", lambda: store)
    submitted = []
    tg = SimpleNamespace(
        is_owner=lambda _sender: True,
        _workers=SimpleNamespace(submit=lambda *args: submitted.append(args)),
        _handle_update_safely=lambda _u: None,
    )
    monkeypatch.setitem(sys.modules, "tg_agent", tg)

    gateway.route_captured_update(_client_update("покажи задачи"))

    assert submitted, "владелец продолжает работать со своим ассистентом"
    assert store.calls == [], "переписка владельца не заводит лид в воронке"
