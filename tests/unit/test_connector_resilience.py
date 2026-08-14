from __future__ import annotations

import json
import os
import stat
import inspect

import pytest


class Response:
    def __init__(self, status_code=200, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = headers or {}
        self.ok = 200 <= status_code < 300

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def test_zoom_read_retries_transient_failure(zoom_module, monkeypatch):
    responses = [Response(503), Response(429, headers={"Retry-After": "0"}), Response(200, {"ok": True})]

    class Session:
        def get(self, *args, **kwargs):
            return responses.pop(0)

    monkeypatch.setattr(zoom_module.time, "sleep", lambda *_: None)
    result = zoom_module.zoom_get(Session(), "https://zoom.invalid/read", timeout=1)
    assert result.status_code == 200
    assert responses == []


def test_zoom_failed_transcript_download_never_overwrites_existing_call(zoom_module, monkeypatch):
    class Cursor:
        def __init__(self):
            self.sql = []

        def execute(self, sql, params=None):
            self.sql.append(sql)

        def fetchone(self):
            return {"id": "call-1", "transcript_text": "known good", "raw_json": {"transcripts": []}}

    class Session:
        def get(self, *args, **kwargs):
            return Response(503)

    cursor = Cursor()
    monkeypatch.setattr(zoom_module.time, "sleep", lambda *_: None)
    meeting = {
        "uuid": "meeting-1",
        "start_time": "2026-08-14T10:00:00Z",
        "recording_files": [
            {"id": "transcript-1", "file_type": "TRANSCRIPT", "download_url": "https://zoom.invalid/file"}
        ],
    }

    with pytest.raises(RuntimeError, match="transcript download failed"):
        zoom_module.upsert_zoom_recording_meeting(cursor, Session(), "ZOOM_ACC2", {}, meeting)

    combined = "\n".join(cursor.sql).upper()
    assert "INSERT INTO ZOOM_CALLS" not in combined
    assert "DELETE FROM ZOOM_CALL_TRANSCRIPT_SEGMENTS" not in combined


def test_zoom_queue_has_atomic_claim_and_stale_lease_recovery(zoom_module):
    source = open(zoom_module.__file__, encoding="utf-8").read()
    assert "FOR UPDATE SKIP LOCKED" in source
    assert "status = 'processing' AND updated_at < now() - interval '30 minutes'" in source
    assert "processing lease expired after final attempt" in source


def test_google_sync_uses_post_body_and_bounded_read_retry(gdrive_module, monkeypatch):
    calls = []
    responses = [Response(503), Response(200, {"ok": True, "documents": [], "listing_complete": True})]

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return responses.pop(0)

    monkeypatch.setattr(gdrive_module, "google_drive_company_sync_config", lambda: ("https://script.invalid", "secret"))
    monkeypatch.setattr(gdrive_module.requests, "post", post)
    monkeypatch.setattr(gdrive_module.time, "sleep", lambda *_: None)

    payload = gdrive_module.fetch_google_drive_company_payload()

    assert payload["listing_complete"] is True
    assert len(calls) == 2
    assert all(call[0] == "https://script.invalid" for call in calls)
    assert all(call[1]["json"]["token"] == "secret" for call in calls)
    assert all("params" not in call[1] for call in calls)


def test_google_deletion_requires_explicit_complete_listing(gdrive_module):
    assert gdrive_module.google_drive_payload_allows_deletions({"ok": True, "documents": []}) is False
    assert gdrive_module.google_drive_payload_allows_deletions(
        {"ok": True, "documents": [], "listing_complete": True}
    ) is True
    assert gdrive_module.google_drive_payload_allows_deletions(
        {"ok": True, "documents": [], "listing_complete": "true"}
    ) is False


def test_refreshed_google_token_is_published_atomically_and_private(gdrive_module, tmp_path):
    target = tmp_path / "oauth.json"
    target.write_text(json.dumps({"old": True}), encoding="utf-8")

    class Credentials:
        def to_json(self):
            return json.dumps({"token": "new", "refresh_token": "kept"})

    gdrive_module._persist_google_user_credentials(str(target), Credentials())

    assert json.loads(target.read_text(encoding="utf-8"))["token"] == "new"
    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert list(tmp_path.glob("oauth.json.tmp.*")) == []


def test_google_objects_default_private_and_public_share_requires_confirmation(app_module, ctx):
    assert inspect.signature(app_module.create_google_sheet).parameters["share_anyone_writer"].default is False
    assert inspect.signature(app_module.create_google_doc).parameters["share_anyone_writer"].default is False
    with pytest.raises(ctx.McpError, match="confirm=true"):
        ctx.tool_share_drive_item_for_everyone({"item": "drive-item", "role": "reader"})
    sheet_schema = ctx.TOOLS["create_google_sheet"]["inputSchema"]
    doc_schema = ctx.TOOLS["create_google_doc"]["inputSchema"]
    assert "idempotency_key" in sheet_schema["required"]
    assert "idempotency_key" in doc_schema["required"]


def test_drive_folder_creation_never_grants_public_writer_implicitly(gdrive_module):
    source = open(gdrive_module.__file__, encoding="utf-8").read()
    start = source.index("def create_drive_folder(")
    end = source.index("def company_drive_root_folder_id(", start)
    implementation = source[start:end]
    assert "_share_drive_anyone" not in implementation
    assert '"access": "inherits_parent"' in implementation


def test_google_idempotency_key_is_hashed_before_provider_metadata(app_module):
    raw, stored = app_module._google_create_idempotency("turn-123:create-sheet")
    assert raw == "turn-123:create-sheet"
    assert stored != raw and len(stored) == 64
    with pytest.raises(ValueError, match="idempotency_key"):
        app_module._google_create_idempotency("")
