from __future__ import annotations

# Отправка файла клиенту из рабочего окна: приём файла, постановка в очередь,
# доставка документом в тот же Telegram-диалог и показ вложения в ленте.

import io
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import funnel_telegram_gateway as gateway
import funnel_workspace_media as media
import funnel_workspace_store as store
import funnel_workspace_uploads as uploads


NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- приём файла


@pytest.fixture
def upload_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNNEL_WORKSPACE_OUTGOING_DIR", str(tmp_path / "outgoing"))
    return tmp_path / "outgoing"


def test_uploaded_file_is_readable_back_by_its_token(upload_dir):
    saved = uploads.store_upload(
        io.BytesIO(b"%PDF-1.4 contract"),
        file_name="Договор №7.pdf",
        mime_type="application/pdf",
    )

    assert saved["file_name"] == "Договор №7.pdf"
    assert saved["file_size"] == len(b"%PDF-1.4 contract")

    resolved = uploads.resolve_upload(saved["token"])
    assert resolved["file_name"] == "Договор №7.pdf"
    assert resolved["mime_type"] == "application/pdf"
    assert resolved["path"].read_bytes() == b"%PDF-1.4 contract"


def test_file_over_the_limit_is_refused_before_it_reaches_the_client(upload_dir, monkeypatch):
    monkeypatch.setenv("FUNNEL_WORKSPACE_OUTGOING_MAX_BYTES", "16")

    with pytest.raises(uploads.UploadError) as excinfo:
        uploads.store_upload(
            io.BytesIO(b"x" * 64),
            file_name="huge.bin",
            mime_type="application/octet-stream",
        )

    assert excinfo.value.code == "file_too_large"
    # Отвергнутый файл не остаётся на диске.
    assert not any(upload_dir.glob("*")) if upload_dir.exists() else True


def test_forged_token_cannot_reach_files_outside_the_store(upload_dir):
    uploads.store_upload(io.BytesIO(b"data"), file_name="ok.txt", mime_type="text/plain")

    for forged in ("../../etc/passwd", "..", "", "a/b", "x" * 200):
        with pytest.raises(uploads.UploadError):
            uploads.resolve_upload(forged)


def test_dangerous_file_name_never_becomes_a_path(upload_dir):
    saved = uploads.store_upload(
        io.BytesIO(b"data"),
        file_name="../../../etc/passwd",
        mime_type="text/plain",
    )

    resolved = uploads.resolve_upload(saved["token"])
    assert "/" not in resolved["file_name"] and "\\" not in resolved["file_name"]
    assert resolved["path"].parent == uploads.outgoing_dir()


def _upload_reference_connect(payloads):
    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, _sql, _params=None):
            pass

        def fetchall(self):
            return [{"payload": payload} for payload in payloads]

    class Connection:
        def cursor(self):
            return Cursor()

    @contextmanager
    def connect():
        yield Connection()

    return connect


def _old_upload_pair(upload_dir, token):
    upload_dir.mkdir(parents=True, exist_ok=True)
    paths = [upload_dir / f"{token}.bin", upload_dir / f"{token}.json"]
    for path in paths:
        path.write_text("old", encoding="utf-8")
        os.utime(path, (1, 1))
    return paths


def test_sweep_keeps_expired_file_referenced_by_active_outbox(upload_dir):
    token = "protected-upload-token-0001"
    paths = _old_upload_pair(upload_dir, token)
    connect = _upload_reference_connect(
        [{"outgoing_file": {"token": token}}]
    )

    assert uploads.sweep_expired(now=10**9, connect_factory=connect) == 0
    assert all(path.exists() for path in paths)


def test_sweep_removes_only_expired_unreferenced_pair(upload_dir):
    token = "expired-upload-token-000001"
    paths = _old_upload_pair(upload_dir, token)

    assert uploads.sweep_expired(
        now=10**9,
        connect_factory=_upload_reference_connect([]),
    ) == 2
    assert not any(path.exists() for path in paths)


def test_sweep_fails_closed_when_queue_references_are_unavailable(upload_dir):
    token = "fail-closed-upload-token-01"
    paths = _old_upload_pair(upload_dir, token)

    @contextmanager
    def broken_connect():
        raise RuntimeError("database unavailable")
        yield

    assert uploads.sweep_expired(now=10**9, connect_factory=broken_connect) == 0
    assert all(path.exists() for path in paths)


# --------------------------------------------------------------------------- очередь


class FakeCursor:
    def __init__(self, responder):
        self.responder = responder
        self.executed: list[tuple[str, object]] = []
        self._one = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        normalized = " ".join(str(sql).split())
        self.executed.append((normalized, params))
        self._one = self.responder(normalized, params)

    def fetchone(self):
        value = self._one
        self._one = None
        return value

    def fetchall(self):
        return []


class FakeConnection:
    def __init__(self, responder):
        self.cursor_instance = FakeCursor(responder)

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        pass


def connect_factory(responder):
    connection = FakeConnection(responder)

    @contextmanager
    def connect():
        yield connection

    return connect, connection


def _conversation(**overrides):
    base = {
        "id": 41,
        "source_key": "telegram",
        "external_chat_id": "9001",
        "business_connection_id": "bc-1",
        "status": "open",
        "control_mode": "human",
        "state_version": 7,
        "reply_deadline_at": datetime(2026, 7, 27, 18, 0, tzinfo=timezone.utc),
        "resume_at": None,
        "assigned_to": "Оператор",
    }
    base.update(overrides)
    return base


def _enqueue_responder(recorded):
    def respond(sql, params):
        if sql.startswith("SELECT pg_advisory_xact_lock"):
            return None
        if sql.startswith("SELECT * FROM funnel_workspace_conversations"):
            return _conversation()
        if sql.startswith("SELECT o.*, row_to_json(m) AS message"):
            return None
        if sql.startswith("SELECT 1 FROM funnel_workspace_outbox"):
            return None
        if sql.startswith("UPDATE funnel_workspace_ai_jobs"):
            return None
        if sql.startswith("INSERT INTO funnel_workspace_messages"):
            recorded["message"] = params
            return {"id": 88, "conversation_id": 41}
        if sql.startswith("UPDATE funnel_workspace_conversations"):
            recorded["conversation"] = params
            return _conversation(state_version=8)
        if sql.startswith("INSERT INTO funnel_workspace_outbox"):
            recorded["outbox"] = params
            return {"id": 99, "conversation_id": 41, "message_id": 88}
        if sql.startswith("INSERT INTO funnel_workspace_control_events"):
            return {"id": 5}
        return None

    return respond


def test_operator_can_send_a_document_without_any_caption():
    recorded: dict[str, object] = {}
    connect, _ = connect_factory(_enqueue_responder(recorded))

    result = store.enqueue_outgoing_operator(
        41,
        text="",
        expected_version=7,
        operator_name="Оператор",
        idempotency_key="ws-file-1",
        attachment={
            "token": "tok-1",
            "file_name": "Договор №7.pdf",
            "mime_type": "application/pdf",
            "file_size": 1024,
        },
        now=NOW,
        connect=connect,
    )

    assert result["duplicate"] is False
    # Файл доезжает до воркера доставки в payload очереди.
    outbox_params = recorded["outbox"]
    payload = next(
        value.obj if hasattr(value, "obj") else value
        for value in outbox_params
        if hasattr(value, "obj") or isinstance(value, dict)
    )
    assert payload["outgoing_file"]["file_name"] == "Договор №7.pdf"
    assert payload["outgoing_file"]["token"] == "tok-1"
    # В списке диалогов пустая строка вместо последнего сообщения выглядит поломкой.
    assert "Договор №7.pdf" in str(recorded["conversation"][7])


def test_caption_longer_than_telegram_allows_is_refused_with_a_clear_reason():
    connect, _ = connect_factory(_enqueue_responder({}))

    with pytest.raises(store.WorkspaceValidationError) as excinfo:
        store.enqueue_outgoing_operator(
            41,
            text="я" * 1100,
            expected_version=7,
            operator_name="Оператор",
            idempotency_key="ws-file-2",
            attachment={
                "token": "tok-2",
                "file_name": "счёт.pdf",
                "mime_type": "application/pdf",
                "file_size": 10,
            },
            now=NOW,
            connect=connect,
        )

    assert "1024" in str(excinfo.value)


def test_message_without_text_and_without_file_is_still_refused():
    connect, _ = connect_factory(_enqueue_responder({}))

    with pytest.raises(store.WorkspaceValidationError):
        store.enqueue_outgoing_operator(
            41,
            text="",
            expected_version=7,
            operator_name="Оператор",
            idempotency_key="ws-empty",
            now=NOW,
            connect=connect,
        )


# --------------------------------------------------------------------------- доставка


class FakeStoreForOutbox:
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
        return {
            "outbox": {**self.item, "delivery_status": kwargs["result"]},
            "message": {},
        }


def _file_outbox(tmp_path, **overrides):
    document = tmp_path / "contract.bin"
    document.write_bytes(b"%PDF-1.4 contract")
    item = {
        "id": 8,
        "message_id": 88,
        "conversation_id": 7,
        "conversation_version": 3,
        "author_type": "operator",
        "external_chat_id": "123",
        "business_connection_id": "connection-A",
        "text": "Держите договор",
        "payload": {
            "outgoing_file": {
                "token": "tok-1",
                "file_name": "Договор №7.pdf",
                "mime_type": "application/pdf",
                "file_size": 17,
            }
        },
    }
    item.update(overrides)
    return item, document


def _patch_uploads(monkeypatch, document):
    monkeypatch.setattr(
        uploads,
        "resolve_upload",
        lambda token: {
            "token": token,
            "path": document,
            "file_name": "Договор №7.pdf",
            "mime_type": "application/pdf",
            "file_size": document.stat().st_size,
        },
    )


def test_queued_file_leaves_as_a_telegram_document_with_the_text_as_caption(
    tmp_path, monkeypatch
):
    item, document = _file_outbox(tmp_path)
    fake_store = FakeStoreForOutbox(item)
    calls: list[tuple[str, dict]] = []

    def api_multipart(method, **kwargs):
        calls.append((method, kwargs))
        return {"message_id": 456, "document": {"file_id": "AgAC-file-id", "file_size": 17}}

    tg = SimpleNamespace(
        _business_connection_id=lambda preferred: (preferred, ""),
        api=lambda *_a, **_k: pytest.fail("файл нельзя отправлять как текст"),
        api_multipart=api_multipart,
    )
    monkeypatch.setitem(sys.modules, "funnel_workspace_store", fake_store)
    monkeypatch.setitem(sys.modules, "tg_agent", tg)
    _patch_uploads(monkeypatch, document)

    gateway._process_outbox_item(item, worker_id="worker")

    method, kwargs = calls[0]
    assert method == "sendDocument"
    assert kwargs["chat_id"] == 123
    assert kwargs["business_connection_id"] == "connection-A"
    assert kwargs["caption"] == "Держите договор"
    assert fake_store.finishes[0][1]["result"] == "sent"
    assert fake_store.finishes[0][1]["provider_message_id"] == "456"


def test_delivered_document_becomes_a_visible_attachment_in_the_feed(tmp_path, monkeypatch):
    item, document = _file_outbox(tmp_path)
    fake_store = FakeStoreForOutbox(item)

    tg = SimpleNamespace(
        _business_connection_id=lambda preferred: (preferred, ""),
        api_multipart=lambda _method, **_kwargs: {
            "message_id": 456,
            "document": {
                "file_id": "AgAC-file-id",
                "file_unique_id": "uniq-1",
                "file_name": "Договор №7.pdf",
                "mime_type": "application/pdf",
                "file_size": 17,
            },
        },
    )
    monkeypatch.setitem(sys.modules, "funnel_workspace_store", fake_store)
    monkeypatch.setitem(sys.modules, "tg_agent", tg)
    _patch_uploads(monkeypatch, document)

    gateway._process_outbox_item(item, worker_id="worker")

    provider_media = fake_store.finishes[0][1]["provider_media"]
    assert provider_media["file_id"] == "AgAC-file-id"

    # Тот же путь показа, что и у входящих файлов: оператор открывает вложение из ленты.
    descriptor = media.attachment_descriptor({"telegram_media": provider_media}, 88)
    assert descriptor["file_name"] == "Договор №7.pdf"
    assert descriptor["url"] == "/api/funnel-workspace/messages/88/attachment"


def test_undelivered_file_is_never_silent(tmp_path, monkeypatch):
    item, document = _file_outbox(tmp_path)
    fake_store = FakeStoreForOutbox(item)

    def api_multipart(_method, **_kwargs):
        raise RuntimeError("sendDocument: {'ok': False, 'description': 'Bad Request: PEER_ID_INVALID'}")

    tg = SimpleNamespace(
        _business_connection_id=lambda preferred: (preferred, ""),
        api_multipart=api_multipart,
    )
    monkeypatch.setitem(sys.modules, "funnel_workspace_store", fake_store)
    monkeypatch.setitem(sys.modules, "tg_agent", tg)
    _patch_uploads(monkeypatch, document)

    gateway._process_outbox_item(item, worker_id="worker")

    outbox_id, finish = fake_store.finishes[0]
    assert outbox_id == 8
    assert finish["result"] == "failed"
    assert str(finish["error"]).strip()


def test_missing_file_on_disk_fails_the_message_instead_of_sending_an_empty_one(
    tmp_path, monkeypatch
):
    item, document = _file_outbox(tmp_path)
    document.unlink()
    fake_store = FakeStoreForOutbox(item)

    tg = SimpleNamespace(
        _business_connection_id=lambda preferred: (preferred, ""),
        api=lambda *_a, **_k: pytest.fail("потерянный файл нельзя подменять текстом"),
        api_multipart=lambda *_a, **_k: pytest.fail("файла нет на диске"),
    )
    monkeypatch.setitem(sys.modules, "funnel_workspace_store", fake_store)
    monkeypatch.setitem(sys.modules, "tg_agent", tg)
    monkeypatch.setattr(
        uploads,
        "resolve_upload",
        lambda _token: (_ for _ in ()).throw(uploads.UploadError("Файл не найден.", code="upload_not_found")),
    )

    gateway._process_outbox_item(item, worker_id="worker")

    assert fake_store.finishes[0][1]["result"] == "failed"
    assert "файл" in str(fake_store.finishes[0][1]["error"]).lower()


def test_text_only_message_still_goes_out_as_a_plain_message(monkeypatch):
    item = {
        "id": 8,
        "message_id": 88,
        "conversation_id": 7,
        "conversation_version": 3,
        "author_type": "operator",
        "external_chat_id": "123",
        "business_connection_id": "connection-A",
        "text": "Ответ менеджера",
        "payload": {},
    }
    fake_store = FakeStoreForOutbox(item)
    calls: list[str] = []

    tg = SimpleNamespace(
        _business_connection_id=lambda preferred: (preferred, ""),
        api=lambda method, **_kwargs: (calls.append(method), {"message_id": 456})[1],
        api_multipart=lambda *_a, **_k: pytest.fail("без файла multipart не нужен"),
    )
    monkeypatch.setitem(sys.modules, "funnel_workspace_store", fake_store)
    monkeypatch.setitem(sys.modules, "tg_agent", tg)

    gateway._process_outbox_item(item, worker_id="worker")

    assert calls == ["sendMessage"]
    assert fake_store.finishes[0][1]["result"] == "sent"


# --------------------------------------------------------------------------- рабочее окно


ORIGIN = "http://localhost"


@pytest.fixture
def client(monkeypatch, tmp_path):
    from flask import Flask
    from werkzeug.security import generate_password_hash

    import funnel_workspace as workspace

    monkeypatch.setenv("FUNNEL_WORKSPACE_ENABLED", "1")
    monkeypatch.setenv("FUNNEL_WORKSPACE_OUTGOING_DIR", str(tmp_path / "outgoing"))
    monkeypatch.setenv(
        "FUNNEL_WORKSPACE_PASSWORD_HASH",
        generate_password_hash("correct horse battery staple"),
    )
    workspace._LOGIN_ATTEMPTS.clear()
    app = Flask(__name__)
    app.secret_key = "test-secret-" * 8
    app.config.update(TESTING=True)
    workspace.register_funnel_workspace(app)
    return app.test_client()


def _login(client):
    response = client.post(
        "/api/funnel-workspace/session",
        json={"password": "correct horse battery staple", "operator_name": "Александр"},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 200
    return response.get_json()


def test_upload_without_csrf_is_rejected(client):
    _login(client)
    response = client.post(
        "/api/funnel-workspace/uploads",
        data={"file": (io.BytesIO(b"data"), "счёт.pdf")},
        content_type="multipart/form-data",
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 403


def test_uploaded_file_is_sent_to_the_client_by_its_token(client, monkeypatch):
    import funnel_workspace as workspace

    session = _login(client)
    uploaded = client.post(
        "/api/funnel-workspace/uploads",
        data={"file": (io.BytesIO(b"%PDF-1.4 contract"), "Договор №7.pdf")},
        content_type="multipart/form-data",
        headers={"Origin": ORIGIN, "X-CSRF-Token": session["csrf_token"]},
    )
    assert uploaded.status_code == 201
    token = uploaded.get_json()["upload"]["token"]
    assert uploaded.get_json()["upload"]["file_name"] == "Договор №7.pdf"

    captured = {}

    def enqueue(conversation_id, **kwargs):
        captured.update({"conversation_id": conversation_id, **kwargs})
        return {
            "conversation": {"id": conversation_id, "state_version": 6},
            "message": {"id": 90, "text": kwargs["text"]},
            "outbox": {"id": 91, "delivery_status": "pending"},
            "duplicate": False,
        }

    monkeypatch.setattr(workspace.store, "enqueue_outgoing_operator", enqueue)
    sent = client.post(
        "/api/funnel-workspace/conversations/41/messages",
        json={"text": "", "expected_version": 5, "upload_token": token},
        headers={
            "Origin": ORIGIN,
            "X-CSRF-Token": session["csrf_token"],
            "Idempotency-Key": "browser-file-1",
        },
    )

    assert sent.status_code == 201
    # Имя и тип берутся с сервера: браузер передаёт только токен.
    assert captured["attachment"]["file_name"] == "Договор №7.pdf"
    assert captured["attachment"]["token"] == token


def test_unknown_upload_token_is_a_clear_refusal(client):
    session = _login(client)
    response = client.post(
        "/api/funnel-workspace/conversations/41/messages",
        json={"text": "", "expected_version": 5, "upload_token": "no-such-token"},
        headers={"Origin": ORIGIN, "X-CSRF-Token": session["csrf_token"]},
    )
    assert response.status_code == 400
    assert response.get_json()
