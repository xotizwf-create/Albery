"""Regression tests for MCP quality-preserving performance work."""
from __future__ import annotations


def test_start_here_still_returns_full_instruction_bodies_by_default(ctx, monkeypatch):
    monkeypatch.setattr(ctx, "load_ai_instructions", lambda: [{"path": "A", "content": "full instruction"}])
    result = ctx.tool_start_here_always_read_ai_instructions({})
    assert result["live_ai_instructions"] == [{"path": "A", "content": "full instruction"}]
    assert "mode" not in result
    assert "fast_path" not in result


def test_hot_index_migrations_are_always_applied():
    import scripts.ensure_postgres as ensure_postgres

    assert "024_chat_report_hot_path_indexes.sql" in ensure_postgres.ALWAYS_APPLY_MIGRATIONS
    assert "025_mcp_search_indexes.sql" in ensure_postgres.ALWAYS_APPLY_MIGRATIONS
