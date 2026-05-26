"""Zoom call pull: sync orchestration (mocked Zoom API + fake DB) and processing
of an already-pulled call into operational tasks.
"""
from __future__ import annotations

from datetime import date

import pytest

pytestmark = pytest.mark.integration


def test_sync_zoom_calls_orchestration(app_module, fake_pg, monkeypatch):
    monkeypatch.setattr(app_module, "ensure_zoom_schema", lambda: None)
    monkeypatch.setattr(app_module, "zoom_session", lambda account_key: object())
    monkeypatch.setattr(app_module, "zoom_list_users", lambda session: [{"id": "u1", "email": "u1@x.ru"}])
    monkeypatch.setattr(
        app_module, "zoom_list_recordings",
        lambda session, user_id, start, end: [{"uuid": "abc123", "topic": "Планёрка"}],
    )
    monkeypatch.setattr(
        app_module, "upsert_zoom_recording_meeting",
        lambda cur, session, account_key, user, meeting, force=False: {
            "status": "synced", "transcript_files_synced": 2,
        },
    )
    monkeypatch.setattr(app_module, "record_integration_sync_success", lambda *a, **k: None)
    fake_pg(app_module)

    result = app_module.sync_zoom_calls(date(2026, 5, 1), date(2026, 5, 1))
    assert result["users_count"] == 1
    assert result["calls_synced"] == 1
    assert result["transcript_files_synced"] == 2


def test_sync_zoom_calls_dedups_uuids(app_module, fake_pg, monkeypatch):
    monkeypatch.setattr(app_module, "ensure_zoom_schema", lambda: None)
    monkeypatch.setattr(app_module, "zoom_session", lambda account_key: object())
    monkeypatch.setattr(app_module, "zoom_list_users", lambda session: [{"id": "u1"}, {"id": "u2"}])
    # Both users surface the SAME meeting uuid -> must be counted once.
    monkeypatch.setattr(
        app_module, "zoom_list_recordings",
        lambda session, user_id, start, end: [{"uuid": "same-uuid"}],
    )
    monkeypatch.setattr(
        app_module, "upsert_zoom_recording_meeting",
        lambda *a, **k: {"status": "synced", "transcript_files_synced": 0},
    )
    monkeypatch.setattr(app_module, "record_integration_sync_success", lambda *a, **k: None)
    fake_pg(app_module)

    result = app_module.sync_zoom_calls(date(2026, 5, 1), date(2026, 5, 1))
    assert result["calls_synced"] == 1


def test_dedupe_zoom_participants(app_module):
    participants = [
        {"name": "Иван", "email": "ivan@x.ru"},
        {"name": "Иван", "email": "ivan@x.ru"},  # exact dup
        {"name": "", "email": ""},  # empty -> dropped
        {"name": "Пётр", "email": ""},
    ]
    result = app_module.dedupe_zoom_participants(participants)
    assert len(result) == 2
    assert {p["name"] for p in result} == {"Иван", "Пётр"}


def test_zoom_call_operational_tasks_from_pulled_call(app_module):
    call = {
        "raw_json": {
            "ai_report": {
                "operational_tasks": [
                    {"task_text": "отправить договор клиенту", "responsible": "Иван"}
                ]
            }
        },
        "analytical_note": "",
    }
    tasks = app_module.zoom_call_operational_tasks(call)
    assert len(tasks) >= 1
    assert "договор" in tasks[0]["task_text"].lower()
