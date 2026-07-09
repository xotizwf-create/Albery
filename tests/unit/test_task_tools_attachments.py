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


def test_task_comment_event_binding_self_heal(monkeypatch):
    # The manual portal step was never done (event.get was empty) — the binding must be
    # created programmatically and be idempotent (2026-07-09, task 1152).
    import b24bot

    monkeypatch.setenv("BITRIX_EVENT_SECRET", "sec123")
    monkeypatch.setattr(b24bot, "_B24_TASK_EVENT_BIND_CHECKED", False)
    calls = []

    def fake_call(endpoint, token, method, payload=None):
        calls.append((method, payload))
        if method == "event.get":
            return {"result": []}
        return {"result": True}

    monkeypatch.setattr(b24bot, "_b24_app_call", fake_call)
    b24bot._b24_ensure_task_comment_event_bound("https://portal/rest/", "tok")
    binds = [p for m, p in calls if m == "event.bind"]
    assert [p["event"] for p in binds] == ["ONTASKCOMMENTADD", "ONTASKCOMMENTUPDATE"]
    assert all(p["handler"] == "https://mcp.m4s.ru/bitrix/events/tasks/sec123" for p in binds)
    # once-per-process guard: the second call must not touch the API at all
    calls.clear()
    b24bot._b24_ensure_task_comment_event_bound("https://portal/rest/", "tok")
    assert calls == []


def test_task_comment_event_binding_skips_when_already_bound(monkeypatch):
    import b24bot

    monkeypatch.setenv("BITRIX_EVENT_SECRET", "sec123")
    monkeypatch.setattr(b24bot, "_B24_TASK_EVENT_BIND_CHECKED", False)
    handler = "https://mcp.m4s.ru/bitrix/events/tasks/sec123"
    seen = []

    def fake_call(endpoint, token, method, payload=None):
        seen.append(method)
        if method == "event.get":
            return {"result": [{"event": "ONTASKCOMMENTADD", "handler": handler},
                               {"event": "ONTASKCOMMENTUPDATE", "handler": handler}]}
        raise AssertionError("event.bind must not be called when already bound")

    monkeypatch.setattr(b24bot, "_b24_app_call", fake_call)
    b24bot._b24_ensure_task_comment_event_bound("https://portal/rest/", "tok")
    assert seen == ["event.get"]


def test_task_comment_event_binding_requires_secret(monkeypatch):
    import b24bot

    monkeypatch.delenv("BITRIX_EVENT_SECRET", raising=False)
    monkeypatch.setattr(b24bot, "_B24_TASK_EVENT_BIND_CHECKED", False)

    def boom(*args, **kwargs):
        raise AssertionError("no API calls without the endpoint secret")

    monkeypatch.setattr(b24bot, "_b24_app_call", boom)
    b24bot._b24_ensure_task_comment_event_bound("https://portal/rest/", "tok")  # silent no-op


def test_native_bitrix_mention_triggers_agent(monkeypatch):
    # The task-card «упомянуть» button inserts [USER=<bot>]Имя[/USER] — must trigger.
    import b24bot

    monkeypatch.setattr(b24bot, "_b24_task_targets", lambda: [
        {"slug": None, "bot_id": 24, "name": "Агент Албери",
         "triggers": {"албери", "агент албери"}, "is_main": True},
    ])
    picked = b24bot._b24_task_pick_agent("[USER=24]Агент Албери[/USER] О чем эта задача?")
    assert picked is not None and picked["is_main"] is True


def test_strip_task_bbcode():
    import b24bot

    cleaned = b24bot._b24_strip_task_bbcode("[USER=24]Агент Албери[/USER] привет [B]жирный[/B]")
    assert cleaned == "Агент Албери привет жирный"


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


def test_export_document_incremental_assembly(tmp_path, monkeypatch):
    """Long docs are built in small sections so no single tool output is huge (the real cause of
    the contract failures). Each section call returns a token; finalize renders the whole thing."""
    import mcp.context_server as cs

    monkeypatch.setattr(cs, "_DOC_DRAFT_DIR", tmp_path / "drafts")
    rendered = {}
    monkeypatch.setattr(cs, "_render_and_save_doc",
                        lambda title, html, a: rendered.update(title=title, html=html) or "https://x/doc.docx")

    r1 = cs.tool_export_document({"title": "Договор", "section": "<h1>ДОГОВОР</h1><p>Часть 1.</p>"})
    assert r1["doc_token"].startswith("doc_") and r1["finalized"] is False
    tok = r1["doc_token"]

    r2 = cs.tool_export_document({"doc_token": tok, "section": "<p>Часть 2 — предмет.</p>"})
    assert r2["chars_total"] > r1["chars_total"]

    r3 = cs.tool_export_document({"doc_token": tok, "finalize": True})
    assert r3["url"] == "https://x/doc.docx"
    assert "Часть 1" in rendered["html"] and "Часть 2" in rendered["html"]
    # draft cleaned up after finalize
    assert not cs._doc_draft_path(tok).exists()


def test_export_document_oneshot_still_works(tmp_path, monkeypatch):
    import mcp.context_server as cs

    monkeypatch.setattr(cs, "_render_and_save_doc", lambda title, html, a: "https://x/one.docx")
    r = cs.tool_export_document({"title": "Справка", "html": "<p>Короткий документ.</p>"})
    assert r["url"] == "https://x/one.docx"


def test_export_document_unknown_token_rejected(tmp_path, monkeypatch):
    import mcp.context_server as cs
    import pytest

    monkeypatch.setattr(cs, "_DOC_DRAFT_DIR", tmp_path / "drafts")
    with pytest.raises(cs.McpError):
        cs.tool_export_document({"doc_token": "doc_nope", "section": "<p>x</p>"})


def test_recurring_tools_registered(ctx=None):
    import mcp.context_server as cs

    for name in ("create_recurring_task", "list_recurring_tasks"):
        assert name in cs.TOOLS, f"{name} not registered"
    assert {"create_recurring_task", "list_recurring_tasks"} <= set(cs.CORE_TOOL_NAMES)
    # viewing is read-only -> ok on FAQ; creating is not
    # (list is fine to expose broadly; create must not be on FAQ)
    assert "create_recurring_task" not in cs.FAQ_TOOL_NAMES


def test_full_task_capability_tools_registered():
    import mcp.context_server as cs

    new_tools = {"update_bitrix_task", "add_task_checklist", "log_task_time", "link_tasks",
                 "add_task_reminder", "list_task_userfields", "delete_recurring_task"}
    assert new_tools <= set(cs.TOOLS), f"missing: {sorted(new_tools - set(cs.TOOLS))}"
    # High-frequency ones reachable via the chat bot core; mutating ones off the read-only FAQ tier.
    assert {"update_bitrix_task", "add_task_checklist", "log_task_time", "link_tasks",
            "delete_recurring_task"} <= set(cs.CORE_TOOL_NAMES)
    assert not (new_tools & set(cs.FAQ_TOOL_NAMES))
    # create_bitrix_task now exposes the full field palette.
    props = cs.TOOLS["create_bitrix_task"]["inputSchema"]["properties"]
    for field in ("accomplice_names", "parent_task_id", "group_id", "start_plan", "crm_elements",
                  "custom_fields", "attachment_ids", "checklist"):
        assert field in props, f"create_bitrix_task missing {field}"


def test_recurring_next_run_weekly_friday():
    import mcp.context_server as cs
    from datetime import datetime

    after = datetime(2026, 7, 8, 12, 0, tzinfo=cs._MSK_TZ)  # Wednesday noon
    nxt = cs._recurring_next_run("weekly", 1, [5], None, "10:00", after=after)
    assert (nxt.year, nxt.month, nxt.day) == (2026, 7, 10)  # next Friday
    assert (nxt.hour, nxt.minute) == (10, 0)
    assert nxt.tzinfo is not None


def test_recurring_next_run_daily_same_then_next_day():
    import mcp.context_server as cs
    from datetime import datetime

    after = datetime(2026, 7, 8, 12, 0, tzinfo=cs._MSK_TZ)
    same = cs._recurring_next_run("daily", 1, [], None, "15:00", after=after)
    assert (same.day, same.hour) == (8, 15)  # later today
    nextday = cs._recurring_next_run("daily", 1, [], None, "09:00", after=after)
    assert (nextday.day, nextday.hour) == (9, 9)  # 09:00 already passed today -> tomorrow


def test_recurring_next_run_monthly_and_interval_skip():
    import mcp.context_server as cs
    from datetime import datetime, date

    after = datetime(2026, 7, 8, 12, 0, tzinfo=cs._MSK_TZ)
    monthly = cs._recurring_next_run("monthly", 1, [], 15, "09:30", after=after)
    assert (monthly.month, monthly.day, monthly.hour, monthly.minute) == (7, 15, 9, 30)

    # every 2 weeks from Fri 10th -> skip the 17th, fire the 24th
    biweekly = cs._recurring_next_run("weekly", 2, [5], None, "10:00",
                                      after=datetime(2026, 7, 10, 12, 0, tzinfo=cs._MSK_TZ),
                                      anchor=date(2026, 7, 10))
    assert (biweekly.month, biweekly.day) == (7, 24)


def test_recurring_monthly_day31_clamps_to_short_month():
    import mcp.context_server as cs
    from datetime import datetime

    after = datetime(2026, 6, 1, 0, 0, tzinfo=cs._MSK_TZ)  # June has 30 days
    nxt = cs._recurring_next_run("monthly", 1, [], 31, "10:00", after=after)
    assert (nxt.month, nxt.day) == (6, 30)  # 31 clamped to last day of June


def test_assemble_task_fields_full_and_minimal():
    import mcp.context_server as cs

    f = cs._assemble_task_fields(
        title="T", description="D", responsible_id=5, deadline_iso="2026-07-10T19:00:00+03:00",
        priority=2, auditor_ids=[7], accomplice_ids=[8, 9], creator_id=3, tags=["a"],
        parent_task_id=100, group_id=12, start_plan="2026-07-10T10:00:00+03:00",
        end_plan="2026-07-11T10:00:00+03:00", time_estimate_seconds=3600,
        crm_elements=["D_5"], custom_fields={"UF_X": "v"})
    assert f["RESPONSIBLE_ID"] == 5 and f["ACCOMPLICES"] == [8, 9] and f["AUDITORS"] == [7]
    assert f["CREATED_BY"] == 3 and f["PARENT_ID"] == 100 and f["GROUP_ID"] == 12
    assert f["TIME_ESTIMATE"] == 3600 and f["UF_CRM_TASK"] == ["D_5"] and f["UF_X"] == "v"
    assert f["SE_PARAMETER"] == [{"CODE": 3, "VALUE": "Y"}]  # result always required

    minimal = cs._assemble_task_fields(title="T", description="D", responsible_id=5,
                                       deadline_iso="2026-07-10T19:00:00+03:00")
    for k in ("ACCOMPLICES", "AUDITORS", "PARENT_ID", "GROUP_ID", "UF_CRM_TASK", "TAGS", "CREATED_BY"):
        assert k not in minimal


def test_clean_crm_and_custom_fields():
    import mcp.context_server as cs
    import pytest

    assert cs._clean_crm_elements(["D_5", "lead_9", "CO_3"]) == ["D_5", "L_9", "CO_3"]
    assert cs._clean_custom_fields({"uf_auto_1": "x"}) == {"UF_AUTO_1": "x"}
    with pytest.raises(cs.McpError):
        cs._clean_custom_fields({"BADKEY": 1})
    with pytest.raises(cs.McpError):
        cs._clean_crm_elements(["nonsense!!"])


def test_recurring_schedule_desc_friday_1000_1900():
    import mcp.context_server as cs

    d = cs._recurring_schedule_desc("weekly", 1, [5], None, "10:00", "19:00 того же дня")
    assert "пятница" in d and "10:00" in d and "19:00" in d


def test_parse_hhmm_validation():
    import mcp.context_server as cs
    import pytest

    assert cs._parse_hhmm("9:5" if False else "09:00", "t") == "09:00"
    assert cs._parse_hhmm("", "t", default="10:00") == "10:00"
    with pytest.raises(cs.McpError):
        cs._parse_hhmm("25:00", "t")
    with pytest.raises(cs.McpError):
        cs._parse_hhmm("abc", "t")
