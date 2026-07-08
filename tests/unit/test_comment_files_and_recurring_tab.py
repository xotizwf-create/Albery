"""Unit tests for 2026-07-08 evening features: (1) task-comment attachments become readable
(file_ids extraction, comment normalization, absolute links preserved in cleaned text);
(2) recurring Bitrix tasks are rendered as kind='task' rows of the «Автоматизации» tab.
Pure-logic, DB-free."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))


def test_comment_file_ids_extracted_from_params():
    from mcp import context_server as cs

    item = {"id": 10406, "author_id": 36, "text": "", "params": {"FILE_ID": [1316, "1317", "junk"]}}
    assert cs.comment_file_ids(item) == [1316, 1317]
    assert cs.comment_file_ids({"params": {}}) == []
    assert cs.comment_file_ids({}) == []


def test_screenshot_only_comment_is_human_and_carries_file_ids():
    from mcp import context_server as cs

    # Sofia's case: empty text + FILE_ID = a human comment with a screenshot, NOT a service row.
    item = {"id": 10406, "author_id": 36, "text": "", "date": "2026-07-06T12:07:57+03:00",
            "params": {"FILE_ID": [1316]}}
    c = cs.normalize_task_comment(item, {36: "Софья Погорелова"})
    assert c["is_service"] is False
    assert c["file_ids"] == [1316]
    assert c["author_name"] == "Софья Погорелова"


def test_clean_bitrix_text_keeps_absolute_links_drops_relative():
    from mcp import context_server as cs

    s = cs.clean_bitrix_text("см. [URL=https://example.com/doc]инструкцию[/URL] и "
                             "[URL=/company/personal/user/36/]задачу[/URL]")
    assert "инструкцию (https://example.com/doc)" in s
    assert "/company/personal" not in s
    assert "задачу" in s


def test_recurring_json_maps_registry_row_to_task_automation():
    import app  # noqa: F401 — project rule: app first, so agent_automations isn't half-imported

    import agent_automations as aa

    row = {
        "id": 8, "title": "Выполнить первостепенные задачи", "responsible_name": "Александр Никитенко",
        "schedule_desc": "каждый день, создание в 09:00, дедлайн 18:00 того же дня",
        "deadline_desc": "18:00 того же дня", "result_criteria": "Выполнено",
        "active": True, "next_run_at": datetime(2026, 7, 9, 9, 0, tzinfo=MSK),
        "last_created_at": None, "last_task_id": None, "last_error": None,
        "spec": {"checklist": ["a", "b", "c"], "responsible_bitrix_id": 16},
        "agent_slug": "main",
    }
    j = aa._recurring_json(row)
    assert j["kind"] == "task"
    assert j["id"] == -8 and j["recurring_id"] == 8
    assert j["agent_slug"] == "main"
    assert j["is_active"] is True
    assert "09.07 09:00" == j["next_run"]
    assert "чек-лист из 3 пунктов" in j["prompt"]
    assert "Александр Никитенко" in j["prompt"]
    assert j["last_status"] == ""  # never fired yet


def test_recurring_json_status_reflects_last_fire():
    import app  # noqa: F401

    import agent_automations as aa

    base = {
        "id": 5, "title": "Т", "responsible_name": None, "schedule_desc": "", "deadline_desc": "",
        "result_criteria": "", "active": False, "next_run_at": None,
        "last_created_at": datetime(2026, 7, 8, 9, 0, tzinfo=MSK),
        "last_task_id": 1184, "last_error": None, "spec": None, "agent_slug": None,
    }
    ok = aa._recurring_json(base)
    assert ok["last_status"] == "ok" and "1184" in ok["last_result"]
    assert ok["agent_slug"] == "main"  # legacy NULL rows fall back to main
    assert ok["is_active"] is False

    err = aa._recurring_json({**base, "last_error": "boom"})
    assert err["last_status"] == "error" and err["last_error"] == "boom"
