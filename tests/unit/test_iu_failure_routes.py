from __future__ import annotations

import json
import subprocess

import pytest


@pytest.fixture
def tg(monkeypatch, tmp_path):
    import tg_agent

    state_file = tmp_path / "state.json"
    state_file.write_text(
        json.dumps({"business": {"C1": {"user_id": 871}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(tg_agent, "STATE_PATH", state_file)
    monkeypatch.setattr(
        tg_agent,
        "load_state",
        lambda: json.loads(state_file.read_text(encoding="utf-8")),
    )
    monkeypatch.setattr(tg_agent, "save_state", lambda _state: None)
    monkeypatch.setattr(tg_agent, "_MODEL_RETRY_PAUSE_S", 0)
    monkeypatch.setattr(tg_agent, "_REPLY_DEBOUNCE_S", 0)
    monkeypatch.setattr(tg_agent.funnel_scenario, "agent_enabled", lambda *a, **kw: True)
    monkeypatch.setattr(tg_agent, "crm_leads_reachable", lambda: True)
    monkeypatch.setattr(tg_agent, "chat_history", lambda *a, **kw: "")
    monkeypatch.setattr(tg_agent, "journal", lambda *a, **kw: None)
    monkeypatch.setattr(tg_agent, "react", lambda *a, **kw: None)
    monkeypatch.setattr(tg_agent, "_dialog_out_watermark", lambda *a, **kw: 0)
    monkeypatch.setattr(tg_agent, "_out_messages_after", lambda *a, **kw: 0)
    monkeypatch.setattr(tg_agent, "funnel_step_block", lambda *a, **kw: "Шаг: консультация")
    monkeypatch.setattr(tg_agent, "_deal_has_form", lambda *a, **kw: False)
    monkeypatch.setenv("TG_BUSINESS_AUTOREPLY", "1")
    monkeypatch.setenv("TG_LEAD_INVITE", "1")
    tg_agent._inbox.clear()
    tg_agent._inbox_last.clear()
    return tg_agent


def _open_event(captured, **kwargs):
    captured.append(kwargs)
    return {
        "handoff_id": 17,
        "event_id": 51,
        "event_created": True,
        "customer_delivery_status": "pending",
        "internal_delivery_status": "pending",
        "due_at": "2026-07-26T12:05:00+04:00",
        "owner_id": "iu-group",
        "owner_name": "Группа «Работа с ИУ»",
    }


@pytest.mark.parametrize("route", ["tg-new", "tg-biz"])
@pytest.mark.parametrize(
    "failure",
    ["http500", "http503", "timeout", "exception", "error_payload", "empty"],
)
def test_model_terminal_failure_is_one_visible_durable_handoff(
        tg, monkeypatch, route, failure):
    model_calls = []
    customer_messages = []
    owner_calls = []
    opened = []
    completed = []

    def model(*_args, **_kwargs):
        model_calls.append(1)
        if failure == "http500":
            raise RuntimeError("HTTP 500 secret-request-id=abc")
        if failure == "http503":
            raise RuntimeError("HTTP 503 secret-request-id=abc")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(["hermes"], 420)
        if failure == "exception":
            raise ValueError("provider secret stack")
        if failure == "error_payload":
            return "Internal Server Error request-id=raw"
        return ""

    monkeypatch.setattr(tg, "hermes_answer", model)
    monkeypatch.setattr(
        tg,
        "lead_deal_for_username",
        (lambda _username: None) if route == "tg-new" else (lambda _username: 80),
    )
    monkeypatch.setattr(
        tg.handoff_store,
        "open_handoff_event",
        lambda *a, **kw: _open_event(opened, **kw),
    )
    monkeypatch.setattr(tg.handoff_store, "claim_delivery", lambda *a, **kw: True)
    monkeypatch.setattr(
        tg.handoff_store,
        "complete_delivery",
        lambda *a, target, sent, **kw: completed.append((target, sent, kw)),
    )
    monkeypatch.setattr(
        tg,
        "send_html",
        lambda _uid, _html, plain: customer_messages.append(plain) or (True, ""),
    )
    monkeypatch.setattr(
        tg,
        "escalate_to_human",
        lambda *a, **kw: owner_calls.append((a, kw)) or {
            "sent": True,
            "destination": "bitrix:iu-group",
            "message_id": 901,
        },
    )

    tg.maybe_autoreply({
        "message_id": 9001,
        "business_connection_id": "C1",
        "chat": {"id": 555, "type": "private"},
        "from": {"id": 555, "username": "lead", "first_name": "Пётр"},
        "text": "Здравствуйте",
    })

    assert len(model_calls) == 2
    assert len(customer_messages) == 1
    assert "технический сбой" in customer_messages[0]
    assert all(
        raw not in customer_messages[0]
        for raw in ("500", "503", "420", "request-id", "provider", "stack",
                    "Internal Server Error")
    )
    assert len(owner_calls) == 1
    assert len(opened) == 1
    assert opened[0]["reason_code"] == "model_failure"
    assert opened[0]["source_message_id"] == 9001
    assert opened[0]["owner_id"] and opened[0]["owner_name"]
    assert opened[0]["sla_seconds"] >= 30
    assert [(target, sent) for target, sent, _kw in completed] == [
        ("customer", True),
        ("internal", True),
    ]
