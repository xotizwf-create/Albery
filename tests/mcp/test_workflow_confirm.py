"""Regression tests for workflow tools that mutate local/project state."""

from __future__ import annotations

import pytest


def test_process_chat_ocr_requires_explicit_confirm_before_workflow(ctx, monkeypatch):
    """OCR processing updates derived DB state and must be confirmation-gated."""

    def fail_if_called(*args, **kwargs):
        raise AssertionError("OCR workflow must not be resolved or called before confirm=true")

    monkeypatch.setattr(ctx, "app_workflow_function", fail_if_called)

    with pytest.raises(ctx.McpError) as exc:
        ctx.tool_process_chat_ocr(
            {
                "date_from": "2026-06-10",
                "dialog_id": "chat-1",
                "force": True,
            }
        )

    assert exc.value.code == -32602
    assert "confirm=true" in exc.value.message
