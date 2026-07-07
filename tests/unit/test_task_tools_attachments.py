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

    payload = {
        "event": "ONTASKCOMMENTADD",
        "data[FIELDS_AFTER][ID]": "12766",
        "data[FIELDS_AFTER][TASK_ID]": "1082",
    }
    assert bitrix._extract_bitrix_event_comment_id(payload) == 12766
    assert bitrix.extract_bitrix_comment_event_task_id(payload) == 1082


def test_task_bot_author_ids_default():
    import b24bot

    assert 22 in b24bot._b24_task_bot_author_ids()
