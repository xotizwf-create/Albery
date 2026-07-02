"""Google Drive company-documents pull with mocked HTTP (Apps Script endpoint)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


class FakeResponse:
    def __init__(self, payload, ok=True, status_code=200, text=""):
        self._payload = payload
        self.ok = ok
        self.status_code = status_code
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def test_fetch_company_documents_parses_payload(gdrive_module, monkeypatch):
    monkeypatch.setattr(gdrive_module, "google_drive_company_sync_config", lambda: ("https://fake.script", "tok"))
    payload = {
        "ok": True,
        "documents": [
            {"file_id": "f1", "name": "Р РµРіР»Р°РјРµРЅС‚", "content": "С‚РµРєСЃС‚"},
            {"file_id": "f2", "name": "РџРѕР»РёС‚РёРєР°"},
            "not-a-dict",  # filtered out
        ],
    }
    monkeypatch.setattr(gdrive_module.requests, "get", lambda *a, **k: FakeResponse(payload))

    docs = gdrive_module.fetch_google_drive_company_documents()
    assert [d["file_id"] for d in docs] == ["f1", "f2"]


def test_fetch_company_payload_raises_on_error_flag(gdrive_module, monkeypatch):
    monkeypatch.setattr(gdrive_module, "google_drive_company_sync_config", lambda: ("https://fake.script", "tok"))
    monkeypatch.setattr(
        gdrive_module.requests, "get",
        lambda *a, **k: FakeResponse({"ok": False, "error": "access denied"}),
    )
    with pytest.raises(RuntimeError, match="access denied"):
        gdrive_module.fetch_google_drive_company_payload()


def test_fetch_company_payload_raises_on_http_error(gdrive_module, monkeypatch):
    monkeypatch.setattr(gdrive_module, "google_drive_company_sync_config", lambda: ("https://fake.script", "tok"))
    monkeypatch.setattr(
        gdrive_module.requests, "get",
        lambda *a, **k: FakeResponse(None, ok=False, status_code=500, text="boom"),
    )
    with pytest.raises(RuntimeError, match="HTTP 500"):
        gdrive_module.fetch_google_drive_company_payload()


def test_google_drive_path_from_parts(gdrive_module):
    assert gdrive_module.google_drive_path_from_parts(["РџР°РїРєР°", "РџРѕРґРїР°РїРєР°"]) == "РџР°РїРєР° / РџРѕРґРїР°РїРєР°"
    assert gdrive_module.google_drive_path_from_parts("РџР°РїРєР°/РџРѕРґРїР°РїРєР°") == "РџР°РїРєР° / РџРѕРґРїР°РїРєР°"
    assert gdrive_module.google_drive_path_from_parts(["РџР°РїРєР°"], "С„Р°Р№Р».pdf") == "РџР°РїРєР° / С„Р°Р№Р».pdf"
    assert gdrive_module.google_drive_path_from_parts("") == ""

