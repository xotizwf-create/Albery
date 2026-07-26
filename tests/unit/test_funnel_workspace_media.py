from __future__ import annotations

import json
from contextlib import contextmanager

import pytest
from flask import Flask, Response
from werkzeug.security import generate_password_hash

import funnel_workspace as workspace
import funnel_workspace_media as media


ORIGIN = "http://localhost"


class FakeCursor:
    def __init__(self, row):
        self.row = row
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, _query, params):
        self.params = params

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, row):
        self.row = row

    def cursor(self):
        return FakeCursor(self.row)


def connect_for(row):
    @contextmanager
    def connect():
        yield FakeConnection(row)

    return connect


class FakeHttpResponse:
    def __init__(self, body: bytes, *, status_code: int = 200, headers=None):
        self.body = body
        self.status_code = status_code
        self.headers = dict(headers or {})
        self.closed = False

    def iter_content(self, chunk_size=8192):
        for offset in range(0, len(self.body), chunk_size):
            yield self.body[offset : offset + chunk_size]

    def close(self):
        self.closed = True


def telegram_getter(*responses):
    remaining = list(responses)
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return remaining.pop(0)

    get.calls = calls
    return get


def telegram_file_response(path="photos/file_1.jpg", *, file_size=None):
    result = {"file_path": path}
    if file_size is not None:
        result["file_size"] = file_size
    return FakeHttpResponse(json.dumps({"ok": True, "result": result}).encode())


def test_descriptor_accepts_namespaced_and_legacy_flat_metadata():
    nested = media.attachment_descriptor(
        {
            "telegram_media_type": "photo",
            "telegram_media": {
                "file_id": "provider-id",
                "file_name": "../../портрет.jpg",
                "mime_type": "image/jpeg",
                "file_size": 123,
            },
        },
        77,
    )
    flat = media.attachment_descriptor(
        {
            "media_type": "voice",
            "file_id": "legacy-provider-id",
            "mime_type": "audio/ogg; codecs=opus",
        },
        78,
    )

    assert nested == {
        "media_type": "photo",
        "file_name": "портрет.jpg",
        "mime_type": "image/jpeg",
        "file_size": 123,
        "url": "/api/funnel-workspace/messages/77/attachment",
        "download_url": "/api/funnel-workspace/messages/77/attachment?download=1",
    }
    assert flat["media_type"] == "voice"
    assert flat["mime_type"] == "audio/ogg"
    assert "file_id" not in flat


def test_photo_is_downloaded_server_side_with_safe_inline_headers(monkeypatch):
    secret = "123456:super-secret-bot-token"
    monkeypatch.setenv("TG_AGENT_BOT_TOKEN", secret)
    row = {
        "source_key": "telegram",
        "metadata": {
            "telegram_media_type": "photo",
            "telegram_media": {
                "file_id": "provider-file-id",
                "file_name": "фото.jpg",
                "mime_type": "image/jpeg",
                "file_size": 4,
            },
        },
    }
    getter = telegram_getter(
        telegram_file_response(file_size=4),
        FakeHttpResponse(b"jpeg", headers={"Content-Length": "4"}),
    )
    app = Flask(__name__)

    with app.test_request_context("/api/funnel-workspace/messages/9/attachment"):
        response = media.build_attachment_response(
            9,
            connect=connect_for(row),
            http_get=getter,
        )
        response.direct_passthrough = False
        assert response.get_data() == b"jpeg"
        assert response.status_code == 200
        assert response.mimetype == "image/jpeg"
        assert response.headers["Content-Disposition"].startswith("inline;")
        assert response.headers["Cache-Control"] == "no-store, private"
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["Cross-Origin-Resource-Policy"] == "same-origin"
        assert secret not in str(response.headers)
        response.close()

    assert len(getter.calls) == 2
    assert secret in getter.calls[0][0]
    assert secret in getter.calls[1][0]


def test_invalid_provider_path_is_rejected_without_leaking_token(monkeypatch):
    secret = "123456:must-not-leak"
    monkeypatch.setenv("TG_AGENT_BOT_TOKEN", secret)
    row = {
        "source_key": "telegram",
        "metadata": {
            "telegram_media_type": "document",
            "telegram_media": {"file_id": "provider-file-id"},
        },
    }
    getter = telegram_getter(telegram_file_response("../private.env"))
    app = Flask(__name__)

    with app.test_request_context("/"):
        with pytest.raises(media.AttachmentProxyError) as caught:
            media.build_attachment_response(
                9,
                connect=connect_for(row),
                http_get=getter,
            )

    assert caught.value.code == "telegram_invalid_file_path"
    assert caught.value.status_code == 502
    assert secret not in str(caught.value)
    assert len(getter.calls) == 1


def test_streaming_download_stops_at_configured_size_limit(monkeypatch):
    monkeypatch.setenv("TG_AGENT_BOT_TOKEN", "123456:test-token")
    monkeypatch.setenv("FUNNEL_WORKSPACE_ATTACHMENT_MAX_BYTES", "8")
    row = {
        "source_key": "telegram",
        "metadata": {
            "telegram_media_type": "document",
            "telegram_media": {"file_id": "provider-file-id"},
        },
    }
    getter = telegram_getter(
        telegram_file_response(),
        FakeHttpResponse(b"ninebytes"),
    )
    app = Flask(__name__)

    with app.test_request_context("/"):
        with pytest.raises(media.AttachmentProxyError) as caught:
            media.build_attachment_response(
                9,
                connect=connect_for(row),
                http_get=getter,
            )

    assert caught.value.code == "attachment_too_large"
    assert caught.value.status_code == 413


@pytest.fixture
def workspace_client(monkeypatch):
    monkeypatch.setenv("FUNNEL_WORKSPACE_ENABLED", "1")
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


def test_attachment_route_requires_workspace_session(workspace_client, monkeypatch):
    monkeypatch.setattr(
        workspace.workspace_media,
        "build_attachment_response",
        lambda message_id, *, force_download: Response(
            f"attachment:{message_id}:{force_download}",
            mimetype="application/octet-stream",
        ),
    )
    anonymous = workspace_client.get(
        "/api/funnel-workspace/messages/42/attachment"
    )
    assert anonymous.status_code == 401

    login = workspace_client.post(
        "/api/funnel-workspace/session",
        json={
            "password": "correct horse battery staple",
            "operator_name": "Оператор",
        },
        headers={"Origin": ORIGIN},
    )
    assert login.status_code == 200
    attachment = workspace_client.get(
        "/api/funnel-workspace/messages/42/attachment?download=1"
    )

    assert attachment.status_code == 200
    assert attachment.get_data(as_text=True) == "attachment:42:True"
    assert attachment.headers["Cache-Control"] == "no-store"
    assert attachment.headers["X-Content-Type-Options"] == "nosniff"
