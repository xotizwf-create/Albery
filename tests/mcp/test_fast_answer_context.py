"""Fast-answer MCP path: compact start tool + one composite context call.

These tests guard the speed optimization: ordinary Q&A should not require full
instruction bodies or a fan-out of many manual tool calls.
"""
from __future__ import annotations


def test_start_here_is_compact_by_default(ctx, monkeypatch):
    monkeypatch.setattr(ctx, "load_ai_instruction_index", lambda: [{"path": "A", "content_chars": 100}])

    def fail_full_load(*args, **kwargs):
        raise AssertionError("full live instructions should not be loaded in compact mode")

    monkeypatch.setattr(ctx, "load_ai_instructions", fail_full_load)
    result = ctx.tool_start_here_always_read_ai_instructions({})
    assert result["mode"] == "compact"
    assert result["live_ai_instructions"] == []
    assert result["live_ai_instructions_index"] == [{"path": "A", "content_chars": 100}]
    assert result["fast_path"]["preferred_tool"] == "get_answer_context"


def test_start_here_full_mode_loads_instruction_bodies(ctx, monkeypatch):
    monkeypatch.setattr(ctx, "load_ai_instruction_index", lambda: [{"path": "A", "content_chars": 100}])
    monkeypatch.setattr(ctx, "load_ai_instructions", lambda: [{"path": "A", "content": "full"}])
    result = ctx.tool_start_here_always_read_ai_instructions({"full": True})
    assert result["mode"] == "full"
    assert result["live_ai_instructions"] == [{"path": "A", "content": "full"}]


def test_get_answer_context_compacts_sources_and_suggests_followups(ctx, monkeypatch):
    original_handlers = {name: spec["handler"] for name, spec in ctx.TOOLS.items()}

    def install(name, result):
        ctx.TOOLS[name]["handler"] = lambda args, result=result: result

    install("search_company_knowledge", {"items": [{"id": "c1", "path": "Регламенты", "name": "Оплата", "content": "x" * 1200}]})
    install("search_tasks", {"items": [{"bitrix_task_id": 318241, "title": "Сделать КП", "comments_human_count": 2, "description": "описание"}]})
    install("get_period_index", {"messages_count": 10, "tasks_count": 1})
    install("search_messages", {"items": [{"dialog_id": "chat1", "chat_title": "Продажи", "message_text": "обсуждали КП", "files": []}]})
    install("search_zoom_transcripts", {"items": [{"call_id": "z1", "topic": "План", "text": "обсудили КП"}]})
    install("get_owner_reports", {"reports": [{"id": "r1", "summary": "контекст"}]})

    try:
        result = ctx.tool_get_answer_context(
            {
                "query": "КП",
                "intent": "recommendation_answer",
                "date_from": "2026-05-26",
                "date_to": "2026-05-26",
                "per_source_limit": 3,
            }
        )
    finally:
        for name, handler in original_handlers.items():
            ctx.TOOLS[name]["handler"] = handler

    assert result["confidence"] == "high"
    assert result["answer_context"]["company_knowledge"][0]["snippet"].endswith("…")
    assert result["answer_context"]["bitrix_tasks"][0]["bitrix_task_id"] == 318241
    assert result["answer_context"]["chat_messages"][0]["dialog_id"] == "chat1"
    assert result["answer_context"]["zoom_transcripts"][0]["call_id"] == "z1"
    assert result["answer_context"]["owner_reports"]["daily"]
    assert {item["tool"] for item in result["recommended_followup_tools"]} >= {"get_task_comments", "get_chat_transcript", "get_zoom_call_transcript"}


def test_get_answer_context_without_dates_skips_chat_search(ctx, monkeypatch):
    original_handlers = {name: spec["handler"] for name, spec in ctx.TOOLS.items()}
    called = []

    def handler(name, result):
        def _inner(args):
            called.append(name)
            return result
        return _inner

    ctx.TOOLS["search_company_knowledge"]["handler"] = handler("search_company_knowledge", {"items": []})
    ctx.TOOLS["search_tasks"]["handler"] = handler("search_tasks", {"items": []})
    ctx.TOOLS["search_messages"]["handler"] = handler("search_messages", {"items": []})
    ctx.TOOLS["search_zoom_transcripts"]["handler"] = handler("search_zoom_transcripts", {"items": []})

    try:
        result = ctx.tool_get_answer_context({"query": "регламент"})
    finally:
        for name, handler_fn in original_handlers.items():
            ctx.TOOLS[name]["handler"] = handler_fn

    assert "search_messages" not in called
    assert result["answer_context"]["chat_messages"] == []
    assert result["confidence"] == "low"
    assert any("date_from/date_to" in item for item in result["missing_context"])
