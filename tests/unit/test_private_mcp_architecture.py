from __future__ import annotations

import subprocess
from contextlib import contextmanager


def test_migration_retires_shared_connectors_and_moves_tokens_to_headers():
    from scripts import migrate_private_mcp as migration

    source = (
        "model: x\n"
        "mcp_servers:\n"
        "  albery:\n"
        "    url: https://mcp.example/mcp/old-global\n"
        "  albery-ops-core:\n"
        "    url: https://mcp.example/mcp-ops-core/old-ops\n"
        "  agent-main:\n"
        "    url: https://mcp.example/mcp-agent/main/old-agent\n"
        "    enabled: true\n"
        "  external:\n"
        "    url: https://safe.example/mcp\n"
    )
    updated = migration.private_config(
        source,
        [{"slug": "main", "token": "rotated-agent-token"}],
        "http://127.0.0.1:5004",
    )

    assert "  albery:" not in updated
    assert "  albery-ops-core:" not in updated
    assert "url: http://127.0.0.1:5004/mcp-agent/main" in updated
    assert 'Authorization: "Bearer rotated-agent-token"' in updated
    assert "  automation-agent-main:" in updated
    assert 'X-Albery-Automation: "1"' in updated
    assert "rotated-agent-token" not in next(line for line in updated.splitlines() if "url:" in line and "agent/main" in line)
    assert "  external:" in updated


def test_main_agent_missing_connector_fails_closed(monkeypatch):
    import agent_center
    import b24bot

    monkeypatch.setattr(b24bot, "_b24_session_prepare", lambda *_args: ("session", None))
    monkeypatch.setattr(agent_center, "universal_main_connector", lambda: None)
    called = []
    monkeypatch.setattr(b24bot, "_hermes_run_guarded", lambda *a, **k: called.append(1))

    answer = b24bot.hermes_brain_answer("поставь задачу", "dialog", from_user_id=7)

    assert "безопасного отключения" in answer
    assert "Ничего не было выполнено" in answer
    assert called == []


def test_quality_text_runner_is_zero_tool_stdin_path(monkeypatch):
    import quality_llm as ql

    events = []

    class Slots:
        @contextmanager
        def held(self, timeout):
            events.append(("slot", timeout))
            yield object()

    def fake_run(command, **kwargs):
        events.append(("run", command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="Краткая сводка", stderr="")

    monkeypatch.setattr(ql, "build_default", lambda: Slots())
    monkeypatch.setattr(ql.subprocess, "run", fake_run)
    monkeypatch.setenv("QUALITY_LLM_PYTHON", "/hermes/python")
    monkeypatch.setenv("QUALITY_LLM_RUNNER", "/app/quality_runner.py")
    monkeypatch.setenv("DATABASE_URL", "postgresql://must-not-leak")

    result = ql.run_quality_text("PRIVATE DIALOG", purpose="dialog_summary", retries=0)

    assert result == "Краткая сводка"
    kwargs = events[1][2]
    assert "PRIVATE DIALOG" in kwargs["input"]
    assert "PRIVATE DIALOG" not in " ".join(events[1][1])
    assert "DATABASE_URL" not in kwargs["env"]


def test_nginx_blocks_mcp_on_both_public_hosts_without_logging_paths():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "deploy" / "nginx-albery.conf").read_text(encoding="utf-8")
    marker = "location ~ ^/(?:mcp(?:-|/|$)|sse(?:-|/|$))"
    assert source.count(marker) == 2
    webhook_marker = "location ~ ^/(bitrix/|zoom/events/|google-drive/events/)"
    assert source.count(webhook_marker) == 2
    assert "proxy_pass http://127.0.0.1:5004" not in source
    assert "location ^~ /zoom-export/" not in source
    assert source.count("access_log off;") >= 3
