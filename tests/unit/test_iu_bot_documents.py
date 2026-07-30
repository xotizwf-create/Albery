from __future__ import annotations

import sys
from types import SimpleNamespace

import iu_bot_documents as documents


class FakeRequest:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class FakeDriveFiles:
    def __init__(self, *, metadata=None, listed=None, exported=None, downloaded=None):
        self.metadata = metadata or {}
        self.listed = listed or []
        self.exported = exported
        self.downloaded = downloaded
        self.calls: list[tuple[str, dict]] = []

    def get(self, **kwargs):
        self.calls.append(("get", kwargs))
        return FakeRequest(self.metadata)

    def list(self, **kwargs):
        self.calls.append(("list", kwargs))
        return FakeRequest({"files": self.listed})

    def export(self, **kwargs):
        self.calls.append(("export", kwargs))
        return FakeRequest(self.exported)

    def get_media(self, **kwargs):
        self.calls.append(("get_media", kwargs))
        return FakeRequest(self.downloaded)


class FakeDrive:
    def __init__(self, files: FakeDriveFiles):
        self._files = files

    def files(self):
        return self._files


def test_company_document_names_match_exactly(monkeypatch):
    fake_cs = SimpleNamespace(
        TOOLS={
            "list_company_files": {
                "handler": lambda _args: {
                    "items": [
                        {
                            "name": "Архив — Ответы на частые вопросы",
                            "google_file_id": "archive",
                        },
                        {
                            "name": "Ответы на частые вопросы",
                            "google_file_id": "approved",
                        },
                    ]
                }
            }
        }
    )
    import mcp

    monkeypatch.setitem(sys.modules, "mcp.context_server", fake_cs)
    monkeypatch.setattr(mcp, "context_server", fake_cs, raising=False)

    found = documents._company_file("Ответы на частые вопросы")

    assert found and found["google_file_id"] == "approved"


def test_uploaded_offer_pdf_is_downloaded_without_re_rendering(monkeypatch):
    files = FakeDriveFiles(
        metadata={"parents": ["knowledge-folder"]},
        listed=[
            {
                "id": "offer-id",
                "name": "Договор оферты",
                "mimeType": "application/pdf",
                "size": "150215",
            }
        ],
        downloaded=b"%PDF-1.7 original-offer",
    )
    monkeypatch.setattr(
        documents,
        "_company_file",
        lambda name: (
            {"google_file_id": "faq-id"}
            if name == documents.FAQ_DOCUMENT_NAME
            else None
        ),
    )
    monkeypatch.setattr(documents, "_drive_client", lambda: FakeDrive(files))

    data = documents._original_pdf_bytes("Договор оферты")

    assert data == b"%PDF-1.7 original-offer"
    assert [name for name, _args in files.calls] == ["get", "list", "get_media"]
    assert "'knowledge-folder' in parents" in files.calls[1][1]["q"]


def test_google_faq_is_exported_as_the_original_pdf(monkeypatch):
    files = FakeDriveFiles(
        metadata={
            "id": "faq-id",
            "name": "Ответы на частые вопросы",
            "mimeType": "application/vnd.google-apps.document",
        },
        exported=b"%PDF-1.7 exported-google-doc",
    )
    monkeypatch.setattr(
        documents,
        "_company_file",
        lambda _name: {"google_file_id": "faq-id"},
    )
    monkeypatch.setattr(documents, "_drive_client", lambda: FakeDrive(files))

    data = documents._original_pdf_bytes("Ответы на частые вопросы")

    assert data == b"%PDF-1.7 exported-google-doc"
    assert [name for name, _args in files.calls] == ["get", "export"]


def test_terms_pdf_uses_the_current_google_document_export(monkeypatch):
    requested: list[str] = []

    def original_pdf(name):
        requested.append(name)
        return b"%PDF-1.7 current-terms-with-original-layout"

    monkeypatch.setattr(documents, "_original_pdf_bytes", original_pdf)
    monkeypatch.setattr(
        documents,
        "render_pdf",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("terms must not be rebuilt from plain text")
        ),
    )
    documents._cache.clear()

    data = documents.pdf_bytes("terms")

    assert data == b"%PDF-1.7 current-terms-with-original-layout"
    assert requested == [documents.TERMS_DOCUMENT_NAME]


def test_client_attachment_names_describe_the_new_documents(monkeypatch):
    stored: list[str] = []
    fake_uploads = SimpleNamespace(
        UploadError=RuntimeError,
        resolve_upload=lambda _token: None,
        store_upload=lambda _stream, *, file_name, mime_type: (
            stored.append(file_name)
            or {
                "token": f"token-{len(stored)}-abcdefghijkl",
                "file_name": file_name,
                "mime_type": mime_type,
                "file_size": 20,
            }
        ),
    )
    monkeypatch.setitem(sys.modules, "funnel_workspace_uploads", fake_uploads)
    monkeypatch.setattr(documents, "pdf_bytes", lambda _kind: b"%PDF-1.7 test")
    documents._attachment_cache.clear()

    documents.attachment("terms")
    documents.attachment("contract")
    documents.attachment("faq")

    assert stored == [
        "Условия присоединения к ИУ.pdf",
        "Договор оферты.pdf",
        "Ответы на частые вопросы.pdf",
    ]
