from __future__ import annotations

import subprocess
from contextlib import contextmanager

import pytest


def test_extract_json_object_accepts_wrappers_and_rejects_non_objects():
    import quality_llm as ql

    assert ql.extract_json_object('```json\n{"ok": true}\n```') == {"ok": True}
    assert ql.extract_json_object('Ответ: {"items": []}') == {"items": []}
    assert ql.extract_json_object('[1, 2]') == {}
    assert ql.extract_json_object('not json') == {}


def test_quality_runner_uses_stdin_global_slot_and_no_prompt_in_argv(monkeypatch):
    import quality_llm as ql

    events = []

    class Slots:
        @contextmanager
        def held(self, timeout):
            events.append(("slot", timeout))
            yield object()

    def fake_run(command, **kwargs):
        events.append(("run", command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout='{"tasks": []}', stderr='')

    monkeypatch.setattr(ql, "build_default", lambda: Slots())
    monkeypatch.setattr(ql.subprocess, "run", fake_run)
    monkeypatch.setenv("QUALITY_LLM_PYTHON", "/hermes/python")
    monkeypatch.setenv("QUALITY_LLM_RUNNER", "/app/quality_runner.py")
    monkeypatch.setenv("DATABASE_URL", "postgresql://must-not-leak")
    monkeypatch.setenv("GROQ_API_KEY", "must-not-leak")
    monkeypatch.setenv("BITRIX_WEBHOOK_BASE", "must-not-leak")
    monkeypatch.setenv("OPENAI_API_KEY", "provider-key")

    result = ql.run_quality_json("SECRET TASK CONTENT", purpose="task_checkin", retries=0)

    assert result == {"tasks": []}
    assert events[0] == ("slot", 180.0)
    command = events[1][1]
    kwargs = events[1][2]
    assert command == ["/hermes/python", "/app/quality_runner.py"]
    assert "SECRET TASK CONTENT" not in " ".join(command)
    assert "SECRET TASK CONTENT" in kwargs["input"]
    assert kwargs["cwd"] == "/tmp"
    assert kwargs["env"]["OPENAI_API_KEY"] == "provider-key"
    assert "DATABASE_URL" not in kwargs["env"]
    assert "GROQ_API_KEY" not in kwargs["env"]
    assert "BITRIX_WEBHOOK_BASE" not in kwargs["env"]


def test_quality_runner_retries_then_fails_closed(monkeypatch):
    import quality_llm as ql

    class Slots:
        @contextmanager
        def held(self, timeout):
            yield object()

    calls = []
    monkeypatch.setattr(ql, "build_default", lambda: Slots())
    monkeypatch.setattr(
        ql.subprocess,
        "run",
        lambda *a, **k: calls.append(1) or subprocess.CompletedProcess(a[0], 0, stdout="bad", stderr=""),
    )
    monkeypatch.setenv("QUALITY_LLM_RETRY_BACKOFF_S", "0")

    with pytest.raises(ql.QualityLLMError):
        ql.run_quality_json("prompt", purpose="novinki_batch", retries=1)
    assert len(calls) == 2


def test_quality_runner_kill_switch(monkeypatch):
    import quality_llm as ql

    calls = []
    monkeypatch.setattr(ql, "build_default", lambda: calls.append("slot"))
    monkeypatch.setattr(ql.subprocess, "run", lambda *a, **k: calls.append("run"))
    monkeypatch.setenv("QUALITY_LLM_ENABLED", "0")
    with pytest.raises(ql.QualityLLMError, match="disabled"):
        ql.run_quality_json("prompt", purpose="unit_test")
    assert calls == [], "kill switch must stop before acquiring a slot or spawning a process"
