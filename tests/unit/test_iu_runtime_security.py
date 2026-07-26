"""Runtime security boundary beyond the per-agent MCP manifest."""
from __future__ import annotations

from types import SimpleNamespace

import tg_agent as tg


def test_customer_toolset_never_includes_web(monkeypatch):
    class _Cursor:
        def execute(self, _sql, _params):
            pass

        def fetchone(self):
            return {"exists": 1}

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    class _Conn:
        def cursor(self):
            return _Cursor()

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(tg, "_db", lambda: _Conn())
    monkeypatch.setenv("TG_AGENT_EXTRA_TOOLSETS", "web")

    assert tg.channel_toolsets(tg.MANAGER_CHANNEL) == "agent-albery-ai-bot"
    assert "web" not in tg.channel_toolsets(tg.MANAGER_CHANNEL)


def test_owner_turn_uses_distinct_trusted_connector(monkeypatch):
    seen = {}

    monkeypatch.setenv("TG_AGENT_OWNER_TOOLSETS", "albery,web")
    monkeypatch.setattr(tg, "_history", lambda _chat_id: [])
    monkeypatch.setattr(tg, "_remember", lambda *_args: None)

    def fake_answer(prompt, session_prefix, toolsets=None, **_kwargs):
        seen.update(prompt=prompt, session_prefix=session_prefix, toolsets=toolsets)
        return "ok"

    monkeypatch.setattr(tg, "hermes_answer", fake_answer)

    assert tg.owner_turn(7, "покажи задачи") == "ok"
    assert seen["toolsets"] == "albery,web"
    assert seen["toolsets"] != f"agent-{tg.MANAGER_CHANNEL}"
    assert seen["session_prefix"] == "tg-owner-7"


def test_customer_turn_has_fail_closed_session_classification(monkeypatch):
    seen = {}

    def fake(prompt, session_prefix, toolsets=None, timeout_s=None):
        seen.update(
            prompt=prompt,
            session_prefix=session_prefix,
            toolsets=toolsets,
        )
        return "ok"

    monkeypatch.setattr(tg, "hermes_answer", fake)
    monkeypatch.setattr(
        tg,
        "customer_toolsets",
        lambda: "agent-iu-customer-runtime",
    )

    assert tg.customer_hermes_answer("hello", "tg-biz-1") == "ok"
    assert seen["toolsets"] == "agent-iu-customer-runtime"
    assert tg._is_customer_session("tg-biz-1") is True
    assert tg._is_customer_session("tg-new-1") is True
    assert tg._is_customer_session("answering-1") is True
    assert tg._is_customer_session("tg-owner-1") is False


def test_customer_turn_fails_closed_to_dedicated_zero_tool_connector(monkeypatch):
    seen = {}

    def fake_run(command, **_kwargs):
        seen["command"] = command
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setenv("TG_AGENT_TOOLSETS", "albery,web")
    monkeypatch.setattr(
        tg,
        "customer_toolsets",
        lambda: "agent-iu-customer-runtime",
    )
    monkeypatch.setattr(tg.subprocess, "run", fake_run)

    assert tg.hermes_answer("hello", "tg-biz-7", toolsets=None) == "ok"
    command = seen["command"]
    assert command[command.index("-t") + 1] == "agent-iu-customer-runtime"
    assert "web" not in command
    assert "--max-turns" not in command
