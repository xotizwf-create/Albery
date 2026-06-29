"""Safety contract tests for Bitrix task creation MCP tool."""
from __future__ import annotations

import pytest


def test_create_bitrix_task_requires_explicit_confirm_before_side_effects(ctx, monkeypatch):
    """Creating a Bitrix task is an external action and must be confirmation-gated."""

    def fail_if_resolved(*args, **kwargs):
        raise AssertionError("responsible resolution must not run before confirm=true")

    def fail_if_called(*args, **kwargs):
        raise AssertionError("Bitrix API must not be called before confirm=true")

    monkeypatch.setattr(ctx, "_resolve_active_bitrix_user", fail_if_resolved)
    monkeypatch.setattr(ctx, "_bitrix_call_with_fallback", fail_if_called)

    with pytest.raises(ctx.McpError) as exc:
        ctx.tool_create_bitrix_task(
            {
                "title": "Проверить отчёт",
                "responsible_bitrix_user_id": 123,
                "deadline": "2026-06-10",
            }
        )

    assert exc.value.code == -32602
    assert "confirm=true" in exc.value.message
