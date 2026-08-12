from __future__ import annotations

import base64
import time
from pathlib import Path


def _artifact_url(channel_artifacts, root, data=b"DOCX", name="contract.docx"):
    channel_artifacts.ZOOM_EXPORT_DIR = root
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_bytes(data)
    (root / f"{name}.name").write_text("Договор.docx", encoding="utf-8")
    expires = int(time.time()) + 1800
    token = channel_artifacts.export_token(name, expires)
    return f"https://www.m4s.ru/zoom-export/{expires}/{token}/{name}"


def test_handoff_resolves_exact_bytes_and_removes_bearer_url(tmp_path, monkeypatch):
    import shared.channel_artifacts as artifacts

    monkeypatch.setattr(artifacts, "ZOOM_EXPORT_DIR", tmp_path)
    url = _artifact_url(artifacts, tmp_path, data=b"EXACT-DOCX")

    text, files, invalid = artifacts.extract_export_artifacts(f"Готово: {url}\n(ссылка действует 30 минут)")

    assert invalid == 0
    assert len(files) == 1
    assert files[0]["data"] == b"EXACT-DOCX"
    assert files[0]["display_name"] == "Договор.docx"
    assert "/zoom-export/" not in text
    assert "прикреплён" in text


def test_invalid_handoff_fails_closed_and_is_never_visible(tmp_path, monkeypatch):
    import shared.channel_artifacts as artifacts

    monkeypatch.setattr(artifacts, "ZOOM_EXPORT_DIR", tmp_path)
    bad = f"https://www.m4s.ru/zoom-export/{int(time.time()) + 1800}/{'0' * 32}/gone.docx"

    text, files, invalid = artifacts.extract_export_artifacts(f"Файл: {bad}")

    assert files == [] and invalid == 1
    assert "/zoom-export/" not in text
    assert "Не удалось безопасно" in text


def test_bitrix_reply_uploads_native_file_from_selected_bot(app_module, tmp_path, monkeypatch):
    import b24bot
    import shared.channel_artifacts as artifacts

    monkeypatch.setenv("CHANNEL_NATIVE_ARTIFACTS", "1")
    monkeypatch.setattr(artifacts, "ZOOM_EXPORT_DIR", tmp_path)
    monkeypatch.setattr(app_module.zoom, "ZOOM_EXPORT_DIR", tmp_path)
    url = _artifact_url(artifacts, tmp_path, data=b"WORD-BYTES")
    calls = []
    journal = []

    def fake_call(endpoint, token, method, payload=None, **kwargs):
        calls.append((method, payload, kwargs))
        assert method == "imbot.v2.File.upload"
        return {"result": {"messageId": 1234, "file": {"id": 55}}}

    monkeypatch.setattr(b24bot, "_b24_app_call", fake_call)
    monkeypatch.setattr(b24bot, "_b24_disclaimer", lambda: "")
    monkeypatch.setattr(b24bot, "log_bot_message", lambda **kwargs: journal.append(kwargs))

    message_id = b24bot._b24_app_reply("https://portal/rest", "oauth", 70, "94", f"Готово: {url}")

    assert message_id == 1234
    assert len(calls) == 1
    method, payload, kwargs = calls[0]
    assert method == "imbot.v2.File.upload" and kwargs["_log"] is False
    assert payload["botId"] == 70 and payload["dialogId"] == "94"
    assert base64.b64decode(payload["fields"]["content"]) == b"WORD-BYTES"
    assert "/zoom-export/" not in payload["fields"]["message"]
    assert journal[0]["meta"]["native_artifact"] is True


def test_generated_document_capture_retains_exact_original_bytes(app_module, tmp_path, monkeypatch):
    import b24bot
    import attachments

    monkeypatch.setattr(b24bot, "ZOOM_EXPORT_DIR", tmp_path)
    monkeypatch.setattr(app_module.zoom, "ZOOM_EXPORT_DIR", tmp_path)
    name = "1234_abcd.docx"
    (tmp_path / name).write_bytes(b"ORIGINAL-DOCX")
    (tmp_path / f"{name}.name").write_text("Оригинал.docx", encoding="utf-8")
    captured = []
    monkeypatch.setattr(b24bot, "_b24_extract_document", lambda *_args: "полный текст")
    monkeypatch.setattr(attachments, "store_attachment", lambda **kwargs: captured.append(kwargs) or "att_x")
    expires = int(time.time()) + 1800
    token = __import__("shared.channel_artifacts", fromlist=["export_token"]).export_token(name, expires)

    b24bot._b24_capture_generated_doc(
        "94", "agent-sklad", 94, f"/zoom-export/{expires}/{token}/{name}",
    )

    assert captured[0]["data"] == b"ORIGINAL-DOCX"
    assert captured[0]["file_name"] == "Оригинал.docx"


def test_bitrix_can_redeliver_scoped_stored_document(app_module, monkeypatch):
    import agent_center
    import attachments
    import b24bot

    calls = []
    monkeypatch.setenv("CHANNEL_NATIVE_ARTIFACTS", "1")
    monkeypatch.setattr(agent_center, "agent_for_bot_id", lambda _bot: {"slug": "agent-sklad"})
    monkeypatch.setattr(attachments, "get_attachment", lambda _token: {
        "dialog_id": "94", "agent_slug": "agent-sklad", "kind": "agent_doc",
    })
    monkeypatch.setattr(attachments, "attachment_bytes", lambda _token: (b"EXACT", "Договор.docx"))
    monkeypatch.setattr(b24bot, "_b24_app_call", lambda ep, tok, method, payload=None, **kw:
                        calls.append((method, payload)) or {"result": {"messageId": 77}})
    monkeypatch.setattr(b24bot, "_b24_disclaimer", lambda: "")
    monkeypatch.setattr(b24bot, "log_bot_message", lambda **_kw: None)

    message_id = b24bot._b24_app_reply(
        "https://portal/rest", "oauth", 70, "94", "Вот файл\n[[DELIVER_STORED: att_abcdefghij]]",
    )

    assert message_id == 77
    assert calls[0][0] == "imbot.v2.File.upload"
    assert base64.b64decode(calls[0][1]["fields"]["content"]) == b"EXACT"


def test_stored_document_marker_is_scoped_to_the_same_dialog(app_module, monkeypatch):
    import agent_center
    import attachments
    import b24bot

    sent = []
    monkeypatch.setenv("CHANNEL_NATIVE_ARTIFACTS", "1")
    monkeypatch.setattr(agent_center, "agent_for_bot_id", lambda _bot: {"slug": "agent-sklad"})
    monkeypatch.setattr(attachments, "get_attachment", lambda _token: {
        "dialog_id": "another", "agent_slug": "agent-sklad", "kind": "agent_doc",
    })
    monkeypatch.setattr(b24bot, "_b24_app_call", lambda ep, tok, method, payload=None, **kw:
                        sent.append((method, payload)) or {"result": 88})
    monkeypatch.setattr(b24bot, "_b24_disclaimer", lambda: "")

    b24bot._b24_app_reply(
        "https://portal/rest", "oauth", 70, "94", "[[DELIVER_STORED: att_abcdefghij]]",
    )

    assert all(method != "imbot.v2.File.upload" for method, _payload in sent)
    assert "Не удалось безопасно" in sent[-1][1]["MESSAGE"]


def test_old_document_without_bytes_is_rebuilt_from_full_text(app_module):
    import b24bot

    prompt = b24bot._b24_compose_user_text(
        "Пришли этот файл ещё раз", [], "", [], [],
        recent_doc={
            "token": "att_abcdefghij",
            "file_name": "Договор.docx",
            "extracted_text": "ПОЛНЫЙ ТЕКСТ ДОГОВОРА",
            "stored_bytes_available": False,
        },
    )

    assert "обязательно создай новый документ через export_document" in prompt
    assert "не используй DELIVER_STORED" in prompt
    assert "ПОЛНЫЙ ТЕКСТ ДОГОВОРА" in prompt


def test_telegram_api_uses_multipart_for_native_document(monkeypatch):
    import tg_multi as multi
    seen = {}

    class Response:
        content = b"1"
        status_code = 200

        @staticmethod
        def json():
            return {"ok": True, "result": {"message_id": 9}}

    def fake_post(url, **kwargs):
        seen.update(kwargs)
        return Response()

    monkeypatch.setattr(multi.requests, "post", fake_post)

    result = multi.api("secret", "sendDocument", chat_id=55, document=("Отчёт.docx", b"BYTES"))

    assert result["message_id"] == 9
    assert seen["data"]["chat_id"] == "55"
    assert seen["files"]["document"][1] == b"BYTES"


def test_telegram_outbox_sends_stored_artifact_not_a_link(monkeypatch):
    import attachments
    import tg_multi as multi

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def execute(self, *_args, **_kwargs): return None

    class Conn:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def cursor(self): return Cursor()

    calls = []
    statuses = []
    monkeypatch.setattr(multi, "_agent_for_slug", lambda _slug: AGENT_FOR_TEST)
    monkeypatch.setattr(multi.core, "_db", lambda: Conn())
    monkeypatch.setattr(attachments, "attachment_bytes", lambda _token: (b"DOCX", "Файл.docx"))
    monkeypatch.setattr(multi, "api", lambda token, method, **kwargs: calls.append((method, kwargs)) or {"message_id": 77})
    monkeypatch.setattr(multi, "_set_outbox_status", lambda oid, status, **kwargs: statuses.append((status, kwargs)))
    monkeypatch.setattr(multi.core, "journal", lambda *_args, **_kwargs: None)

    multi._process_outbox({
        "id": 1,
        "agent_slug": "prodazhi-bot",
        "chat_id": "55",
        "text": "",
        "attachment_token": "att_x",
        "attempts": 0,
    })

    assert calls == [("sendDocument", {"chat_id": "55", "document": ("Файл.docx", b"DOCX")})]
    assert statuses[-1][0] == "sent"


def test_native_artifact_migration_keeps_text_and_files_as_independent_parts():
    from scripts import ensure_postgres

    name = "085_channel_native_artifacts.sql"
    assert name in ensure_postgres.ALWAYS_APPLY_MIGRATIONS
    sql = (Path(__file__).resolve().parents[2] / "database" / "migrations" / name).read_text(
        encoding="utf-8"
    )
    assert "part_no integer NOT NULL DEFAULT 0" in sql
    assert "uq_tao_update_part" in sql
    assert "uq_aad_run_target_part" in sql
    assert "attachment_token" in sql

    automation_source = (Path(__file__).resolve().parents[2] / "agent_automations.py").read_text(
        encoding="utf-8"
    )
    assert "actual automation result" in automation_source
    assert "part_no, attachment_token, rendered_text" in automation_source


def test_attachment_cleanup_never_removes_an_open_delivery(tmp_path, monkeypatch):
    import attachments

    protected = tmp_path / "att_open__report.docx"
    expired = tmp_path / "att_old__report.docx"
    protected.write_bytes(b"OPEN")
    expired.write_bytes(b"OLD")
    old = time.time() - 10 * 86400
    __import__("os").utime(protected, (old, old))
    __import__("os").utime(expired, (old, old))
    updates = []

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def execute(self, sql, params=None):
            if sql.lstrip().startswith("UPDATE"):
                updates.append(params)
        def fetchall(self): return [{"attachment_token": "att_open"}]

    class Conn:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def cursor(self): return Cursor()

    monkeypatch.setattr(attachments, "ATTACH_DIR", tmp_path)
    monkeypatch.setattr(attachments, "ATTACH_RETENTION_DAYS", 1)
    monkeypatch.setattr(attachments, "connect", lambda: Conn())

    assert attachments.cleanup_attachment_bytes(force=True) == 1
    assert protected.exists()
    assert not expired.exists()
    assert updates and updates[0][0] == ["att_old"]


AGENT_FOR_TEST = {
    "slug": "prodazhi-bot",
    "bot_token": "111:AAA",
}
