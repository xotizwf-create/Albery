"""Safety contract for live AI-instruction edits."""
from __future__ import annotations

import contextlib

import pytest


class _FakeCursor:
    def __init__(self, responder):
        self.executed = []
        self._responder = responder
        self._last = None

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        self._last = self._responder(sql, params)

    def fetchone(self):
        return self._last

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    @contextlib.contextmanager
    def transaction(self):
        yield self

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_upsert_ai_instruction_requires_confirm_before_db(ctx, monkeypatch):
    def fail_if_connected(*args, **kwargs):
        raise AssertionError("upsert_ai_instruction must not connect before confirm=true")

    monkeypatch.setattr(ctx, "connect", fail_if_connected)

    with pytest.raises(ctx.McpError) as exc:
        ctx.tool_upsert_ai_instruction(
            {
                "path": "FAQ/Поведение",
                "content": "Новый текст",
            }
        )

    assert exc.value.code == -32602
    assert "confirm=true" in exc.value.message


def test_upsert_ai_instruction_schema_requires_confirm(ctx):
    spec = ctx.TOOLS["upsert_ai_instruction"]

    assert "confirm" in spec["inputSchema"]["required"]
    assert spec["risk_metadata"]["requires_confirm"] is True


def test_upsert_ai_instruction_rejects_stale_expected_content(ctx, monkeypatch):
    monkeypatch.setattr(ctx, "safe_table_exists", lambda cur, table: True)

    def responder(sql, params):
        if "SELECT id, name, content" in sql:
            return {"id": "11111111-1111-1111-1111-111111111111", "name": "Поведение", "content": "текущий текст"}
        return {"sort_order": 0}

    cur = _FakeCursor(responder)
    monkeypatch.setattr(ctx, "connect", lambda: _FakeConn(cur))

    with pytest.raises(ctx.McpError) as exc:
        ctx.tool_upsert_ai_instruction(
            {
                "path": "Поведение",
                "content": "новый текст",
                "expected_current_content": "старый preview",
                "confirm": True,
            }
        )

    assert exc.value.code == -32000
    assert "changed since preview" in exc.value.message
    assert not any("UPDATE ai_instruction_folders" in sql for sql, _ in cur.executed)
