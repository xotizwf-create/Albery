from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

import bitrix_inbound
from scripts import ensure_postgres


def test_migration_and_selfcheck_are_registered():
    name = "087_durable_bitrix_inbound.sql"
    assert ensure_postgres.REQUIRED_TABLE_MIGRATIONS["bitrix_inbound_jobs"] == name
    assert name in ensure_postgres.ALWAYS_APPLY_MIGRATIONS
    sql = (Path(__file__).resolve().parents[2] / "database" / "migrations" / name).read_text(
        encoding="utf-8",
    )
    for marker in (
        "event_key text NOT NULL UNIQUE",
        "'brain_running'",
        "'answer_ready'",
        "'sending'",
        "'delivery_retry'",
        "'review'",
        "brain_started_at",
        "delivery_started_at",
    ):
        assert marker in sql
    selfcheck = (Path(__file__).resolve().parents[2] / "scripts" / "albery_selfcheck.py").read_text(
        encoding="utf-8",
    )
    assert "inspect_bitrix_inbound_health" in selfcheck


def test_token_free_payload_removes_flattened_credentials():
    clean = bitrix_inbound.token_free_payload({
        "event": "ONIMBOTMESSAGEADD",
        "author_id": 16,
        "auth[access_token]": "secret-a",
        "AUTH[REFRESH_TOKEN]": "secret-r",
        "auth[application_token]": "secret-app",
        "nested": {
            "client_secret": "secret-c",
            "author_id": 17,
            "items": [{"authorization": "Bearer secret", "name": "kept"}],
        },
        "data[PARAMS][MESSAGE]": "hello",
    })
    assert clean == {
        "event": "ONIMBOTMESSAGEADD",
        "author_id": 16,
        "nested": {"author_id": 17, "items": [{"name": "kept"}]},
        "data[PARAMS][MESSAGE]": "hello",
    }


def test_content_free_health_reports_only_status_and_count():
    now = datetime(2026, 8, 14, tzinfo=timezone.utc)

    def query(_sql, _params):
        return [
            {"status": "queued", "n": 2, "oldest": now - timedelta(minutes=7)},
            {"status": "review", "n": 1, "oldest": now - timedelta(seconds=1)},
        ]

    problems = bitrix_inbound.inspect_health(now=now, query=query)

    assert len(problems) == 2
    assert "Bitrix inbound queued overdue: 2" in problems

    # Формулировка про зависший ход изменена 19.08.2026: «Bitrix inbound review: 1» не
    # объясняло ни что случилось, ни что делать, и владелец спрашивал об этом отдельно.
    # Суть теста прежняя и она в названии — в тревогу не должно утекать СОДЕРЖИМОЕ
    # переписки: только состояние и количество.
    review = next(p for p in problems if "без ответа" in p)
    assert "1" in review and "вручную" in review
    for leak in ("dialog", "payload", "message_text", "@"):
        assert leak not in review.lower(), f"в тревогу утекло содержимое: {leak}"


def test_chat_webhook_fails_closed_when_durable_capture_is_unavailable(client, monkeypatch):
    import b24bot

    monkeypatch.setattr(
        b24bot, "_b24_load_state",
        lambda: {"application_token": "app-token", "bot_id": "24", "client_endpoint": "https://portal/rest"},
    )
    monkeypatch.setattr(b24bot.bitrix_inbound, "enabled", lambda: True)
    monkeypatch.setattr(
        b24bot.bitrix_inbound, "enqueue",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("postgres down")),
    )
    response = client.post("/bitrix/imbot/app", data={
        "event": "ONIMBOTMESSAGEADD",
        "auth[application_token]": "app-token",
        "auth[access_token]": "event-token",
        "auth[client_endpoint]": "https://portal/rest",
        "data[PARAMS][BOT_ID]": "24",
        "data[PARAMS][DIALOG_ID]": "16",
        "data[PARAMS][FROM_USER_ID]": "16",
        "data[PARAMS][MESSAGE_ID]": "9001",
        "data[PARAMS][MESSAGE]": "test",
    })
    assert response.status_code == 503
    assert response.get_json()["retry"] is True


def test_chat_webhook_acks_only_the_durable_insert(client, monkeypatch):
    import b24bot

    captured = {}
    monkeypatch.setattr(
        b24bot, "_b24_load_state",
        lambda: {"application_token": "app-token", "bot_id": "24", "client_endpoint": "https://portal/rest"},
    )
    monkeypatch.setattr(b24bot.bitrix_inbound, "enabled", lambda: True)

    def enqueue(**kwargs):
        captured.update(kwargs)
        return {"id": "job-1", "inserted": True, "status": "queued"}

    monkeypatch.setattr(b24bot.bitrix_inbound, "enqueue", enqueue)
    monkeypatch.setattr(
        b24bot, "_b24_message_claim",
        lambda *_args: (_ for _ in ()).throw(AssertionError("legacy claim must not run")),
    )
    response = client.post("/bitrix/imbot/app", data={
        "event": "ONIMBOTMESSAGEADD",
        "auth[application_token]": "app-token",
        "auth[access_token]": "event-token",
        "auth[client_endpoint]": "https://portal/rest",
        "data[PARAMS][BOT_ID]": "24",
        "data[PARAMS][DIALOG_ID]": "16",
        "data[PARAMS][FROM_USER_ID]": "16",
        "data[PARAMS][MESSAGE_ID]": "9002",
        "data[PARAMS][MESSAGE]": "test",
    })
    assert response.status_code == 200
    assert response.get_json()["accepted"] is True
    assert captured["event_key"] == "chat:24:9002"
    assert not any("token" in key.lower() for key in captured["payload"]["event_payload"])


def test_chat_webhook_without_provider_id_does_not_process_unsafely(client, monkeypatch):
    import b24bot

    monkeypatch.setattr(
        b24bot, "_b24_load_state",
        lambda: {"application_token": "app-token", "bot_id": "24", "client_endpoint": "https://portal/rest"},
    )
    monkeypatch.setattr(b24bot.bitrix_inbound, "enabled", lambda: True)
    response = client.post("/bitrix/imbot/app", data={
        "event": "ONIMBOTMESSAGEADD",
        "auth[application_token]": "app-token",
        "auth[access_token]": "event-token",
        "auth[client_endpoint]": "https://portal/rest",
        "data[PARAMS][BOT_ID]": "24",
        "data[PARAMS][DIALOG_ID]": "16",
        "data[PARAMS][FROM_USER_ID]": "16",
        "data[PARAMS][MESSAGE]": "test",
    })
    assert response.status_code == 503
    assert response.get_json()["error"] == "durable_message_id_required"


def test_task_comment_webhook_fails_closed_when_capture_is_unavailable(client, monkeypatch):
    import bitrix

    monkeypatch.setattr(bitrix, "bitrix_event_secret_valid", lambda _secret: True)
    monkeypatch.setattr(bitrix.bitrix_inbound, "enabled", lambda: True)
    monkeypatch.setattr(
        bitrix.bitrix_inbound, "enqueue",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("postgres down")),
    )
    response = client.post("/bitrix/events/tasks/test", data={
        "event": "OnTaskCommentAdd",
        "data[FIELDS_AFTER][TASK_ID]": "77",
        "data[FIELDS_AFTER][MESSAGE_ID]": "88",
    })
    assert response.status_code == 503
    assert response.get_json()["retry"] is True


def test_task_comment_webhook_does_not_spawn_legacy_thread_after_capture(client, monkeypatch):
    import bitrix

    captured = {}
    monkeypatch.setattr(bitrix, "bitrix_event_secret_valid", lambda _secret: True)
    monkeypatch.setattr(bitrix.bitrix_inbound, "enabled", lambda: True)

    def enqueue(**kwargs):
        captured.update(kwargs)
        return {"id": "job-2", "inserted": True, "status": "queued"}

    monkeypatch.setattr(bitrix.bitrix_inbound, "enqueue", enqueue)
    response = client.post("/bitrix/events/tasks/test", data={
        "event": "OnTaskCommentAdd",
        "data[FIELDS_AFTER][TASK_ID]": "77",
        "data[FIELDS_AFTER][MESSAGE_ID]": "88",
    })
    assert response.status_code == 200
    assert response.get_json()["queued"] is True
    assert captured["event_key"] == "task-comment:88"


def test_durable_chat_delivery_classifies_transport_ambiguity(monkeypatch):
    import b24bot

    monkeypatch.setattr(b24bot, "_b24_disclaimer", lambda: "")
    monkeypatch.setattr(
        b24bot, "_b24_app_call",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(requests.Timeout("lost response")),
    )
    outcome = {}
    result = b24bot._b24_app_reply(
        "https://portal/rest", "token", 24, "16", "answer", _durable_outcome=outcome,
    )
    assert result is None
    assert outcome["status"] == "ambiguous"


def test_durable_chat_delivery_classifies_known_rejection(monkeypatch):
    import b24bot

    monkeypatch.setattr(b24bot, "_b24_disclaimer", lambda: "")
    monkeypatch.setattr(
        b24bot, "_b24_app_call",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("HTTP 400 rejected")),
    )
    outcome = {}
    result = b24bot._b24_app_reply(
        "https://portal/rest", "token", 24, "16", "answer", _durable_outcome=outcome,
    )
    assert result is None
    assert outcome["status"] == "known_failure"
