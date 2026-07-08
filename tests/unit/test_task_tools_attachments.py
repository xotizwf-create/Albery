"""Unit tests for the team-rollout features (2026-07-07): attachment prompt injection,
in-task mention detection, and task-comment event id extraction. Pure-logic, DB-free
(the DB-touching helpers are patched or degrade to the main-agent fallback)."""
from __future__ import annotations


def test_compose_injects_attachment_tokens_and_no_truncation_note():
    import b24bot

    # A short document: fully inline, its attachment_id shown, no truncation note.
    short = "Договор №1. " + ("текст " * 20)
    doc_blocks = [("dogovor.docx", short, "att_ABC123")]
    attachments = [{"token": "att_ABC123", "name": "dogovor.docx", "kind": "document", "char_len": len(short)}]
    out = b24bot._b24_compose_user_text("посмотри договор", [], "", doc_blocks, attachments)
    assert "att_ABC123" in out
    assert "dogovor.docx" in out
    assert "get_attachment_text" in out  # the forwardable/attachments hint always mentions the tool
    assert "показано начало документа" not in out  # not truncated


def test_compose_truncates_long_doc_with_full_read_pointer(monkeypatch):
    import b24bot

    monkeypatch.setattr(b24bot, "_B24_DOC_INLINE_CHARS", 100)
    long_text = "А" * 5000
    doc_blocks = [("contract.pdf", long_text, "att_LONG")]
    attachments = [{"token": "att_LONG", "name": "contract.pdf", "kind": "document", "char_len": 5000}]
    out = b24bot._b24_compose_user_text("", [], "", doc_blocks, attachments)
    assert "показано начало документа" in out
    assert "get_attachment_text(attachment_id='att_LONG'" in out
    # Only the inline slice is present, not the whole 5000 chars.
    assert out.count("А") <= 200


def test_compose_lists_forwardable_attachments():
    import b24bot

    attachments = [
        {"token": "att_IMG", "name": "screen.png", "kind": "image", "char_len": 12},
        {"token": "att_DOC", "name": "akt.docx", "kind": "document", "char_len": 900},
    ]
    out = b24bot._b24_compose_user_text("приложи к задаче", ["OCR text"], "", [], attachments)
    assert "att_IMG" in out and "att_DOC" in out
    assert "attach_files_to_task" in out


def test_pick_agent_detects_main_trigger(monkeypatch):
    import b24bot

    # Force a DB-free target list so the test doesn't need PostgreSQL.
    monkeypatch.setattr(b24bot, "_b24_task_targets", lambda: [
        {"slug": None, "bot_id": 24, "name": "Агент Албери",
         "triggers": {"албери", "агент албери"}, "is_main": True},
        {"slug": "agent-sklad", "bot_id": 70, "name": "Агент-юрист",
         "triggers": {"агент-юрист", "юрист"}, "is_main": False},
    ])
    assert b24bot._b24_task_pick_agent("Албери, поставь задачу Иванову")["is_main"] is True
    # longest-match wins: «юрист» picks the legal agent even though «албери» absent
    assert b24bot._b24_task_pick_agent("нужен юрист по этому договору")["slug"] == "agent-sklad"
    # no agent named -> None (the vast majority of company comments)
    assert b24bot._b24_task_pick_agent("обычный рабочий комментарий без агента") is None
    # substring must not false-trigger inside another word
    assert b24bot._b24_task_pick_agent("юристконсульт уже посмотрел") is None


def test_comment_event_id_extraction():
    import bitrix

    # Real portal shape: the chat message id is in MESSAGE_ID; ID is 0/unused.
    payload = {
        "event": "ONTASKCOMMENTADD",
        "data[FIELDS_AFTER][ID]": "0",
        "data[FIELDS_AFTER][MESSAGE_ID]": "12928",
        "data[FIELDS_AFTER][TASK_ID]": "1102",
    }
    assert bitrix._extract_bitrix_event_comment_id(payload) == 12928
    assert bitrix.extract_bitrix_comment_event_task_id(payload) == 1102
    # Fallback to ID when MESSAGE_ID absent.
    legacy = {"event": "ONTASKCOMMENTADD", "data[FIELDS_AFTER][ID]": "555",
              "data[FIELDS_AFTER][TASK_ID]": "9"}
    assert bitrix._extract_bitrix_event_comment_id(legacy) == 555


def test_task_bot_author_ids_default():
    import b24bot

    assert 22 in b24bot._b24_task_bot_author_ids()


def test_task_deep_link_builds_clickable_url(monkeypatch):
    import mcp.context_server as cs

    monkeypatch.setenv("BITRIX_WEBHOOK_BASE", "https://b24-0xrp3s.bitrix24.ru/rest/22/secret/")
    url = cs._task_deep_link(1076)
    assert url == "https://b24-0xrp3s.bitrix24.ru/company/personal/user/0/tasks/task/view/1076/"
    # bad ids -> no link (never a broken URL)
    assert cs._task_deep_link(None) is None
    assert cs._task_deep_link("abc") is None


def test_task_deep_link_none_without_portal(monkeypatch):
    import mcp.context_server as cs

    monkeypatch.delenv("BITRIX_WEBHOOK_BASE", raising=False)
    monkeypatch.delenv("BITRIX_PORTAL_URL", raising=False)
    assert cs._task_deep_link(1076) is None


def test_fetch_url_binary_doc_detection():
    import mcp.context_server as cs

    assert cs._binary_doc_ext("https://x.ru/f/report.docx", "") == "docx"
    assert cs._binary_doc_ext("https://x.ru/f/%D0%94%D0%BE%D0%B3%D0%BE%D0%B2%D0%BE%D1%80.docx", "") == "docx"
    assert cs._binary_doc_ext("https://x.ru/a", "application/pdf") == "pdf"
    assert cs._binary_doc_ext("https://x.ru/a.html", "text/html; charset=utf-8") is None


def test_reader_never_sees_private_hosts(monkeypatch):
    import mcp.context_server as cs

    monkeypatch.delenv("FETCH_URL_READER", raising=False)
    monkeypatch.delenv("FETCH_URL_READER_EXCLUDE", raising=False)
    # token-bearing export links and the Bitrix portal must never leak to the external reader
    assert cs._reader_allowed_for("https://mcp.m4s.ru/zoom-export/123/tok/file.docx") is False
    assert cs._reader_allowed_for("https://b24-0xrp3s.bitrix24.ru/company/personal/user/0/tasks/task/view/1/") is False
    assert cs._reader_allowed_for("http://127.0.0.1:5002/x") is False
    # public pages are allowed
    assert cs._reader_allowed_for("https://dzen.ru/a/abc") is True
    # kill-switch
    monkeypatch.setenv("FETCH_URL_READER", "0")
    assert cs._reader_allowed_for("https://dzen.ru/a/abc") is False


def test_auth_wall_detection():
    import mcp.context_server as cs

    assert cs._looks_like_auth_wall("https://sso.passport.yandex.ru/push?x=1", "x" * 5000) is True
    assert cs._looks_like_auth_wall("https://dzen.ru/a/abc", "короткий огрызок") is True
    assert cs._looks_like_auth_wall("https://dzen.ru/a/abc", "т" * 1000) is False


def test_extract_binary_document_docx_roundtrip(tmp_path):
    import mcp.context_server as cs
    from docx import Document

    p = tmp_path / "t.docx"
    d = Document()
    d.add_paragraph("Договор оказания услуг — тестовый абзац.")
    d.save(str(p))
    text = cs._extract_binary_document(p.read_bytes(), "docx")
    assert "Договор оказания услуг" in text
