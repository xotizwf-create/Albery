from __future__ import annotations

import json

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
    monkeypatch.setattr(tg_agent, "_first_contact", lambda _uid: False)
    monkeypatch.setattr(tg_agent, "_name_for_uid", lambda _uid: "")
    return tg_agent


def _open_event(*_args, **_kwargs):
    return {
        "handoff_id": 17,
        "event_id": 51,
        "event_created": True,
        "customer_delivery_status": "pending",
        "internal_delivery_status": "pending",
        "due_at": "2026-07-25T19:30:00+03:00",
        "owner_id": "iu-group",
        "owner_name": "Группа «Работа с ИУ»",
    }


def test_handoff_is_persisted_before_visible_receipt_and_owner_dispatch(tg, monkeypatch):
    order = []
    completed = []
    journal_rows = []

    monkeypatch.setattr(
        tg.handoff_store,
        "open_handoff_event",
        lambda *a, **kw: order.append(("persist", kw)) or _open_event(),
    )
    monkeypatch.setattr(
        tg.handoff_store,
        "claim_delivery",
        lambda *a, target, **kw: order.append(("claim", target)) or True,
    )
    monkeypatch.setattr(
        tg.handoff_store,
        "complete_delivery",
        lambda *a, target, sent, **kw: completed.append((target, sent)),
    )
    monkeypatch.setattr(
        tg,
        "send_html",
        lambda *_a, **_kw: order.append(("client", None)) or (True, ""),
    )
    monkeypatch.setattr(
        tg,
        "escalate_to_human",
        lambda *_a, **_kw: order.append(("owner", None))
        or {"sent": True, "destination": "bitrix:iu-group", "message_id": 99},
    )
    monkeypatch.setattr(
        tg,
        "journal",
        lambda *a, **kw: journal_rows.append({"args": a, "kwargs": kw}),
    )

    ok = tg._visible_handoff(
        {"id": 123, "username": "lead"},
        "Когда подключите?",
        "нет утверждённого срока",
        tg.HUMAN_HANDOFF_REPLY,
        reason_code="knowledge_gap",
        deal_id=78,
        source_message_ids=[9001],
    )

    assert ok is True
    assert [item[0] for item in order].index("persist") < \
        [item[0] for item in order].index("client") < \
        [item[0] for item in order].index("owner")
    assert completed == [("customer", True), ("internal", True)]
    persisted = order[0][1]
    assert persisted["owner_id"] and persisted["owner_name"]
    assert persisted["reason_code"] == "knowledge_gap"
    assert "client_text" not in persisted
    assert any(
        row["kwargs"].get("meta", {}).get("handoff_id") == 17
        and row["kwargs"].get("meta", {}).get("customer_visible") is True
        for row in journal_rows
    )


def test_duplicate_event_sends_neither_second_receipt_nor_second_card(tg, monkeypatch):
    duplicate = {
        **_open_event(),
        "event_created": False,
        "customer_delivery_status": "sent",
        "internal_delivery_status": "sent",
    }
    monkeypatch.setattr(tg.handoff_store, "open_handoff_event", lambda *a, **kw: duplicate)
    monkeypatch.setattr(tg.handoff_store, "claim_delivery", lambda *a, **kw: False)
    monkeypatch.setattr(
        tg,
        "send_html",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("duplicate receipt")),
    )
    monkeypatch.setattr(
        tg,
        "escalate_to_human",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("duplicate card")),
    )
    monkeypatch.setattr(tg, "journal", lambda *a, **kw: None)

    assert tg._visible_handoff(
        {"id": 123},
        "Когда подключите?",
        "нет утверждённого срока",
        tg.HUMAN_HANDOFF_REPLY,
        reason_code="knowledge_gap",
        source_message_ids=[9001],
    )


def test_failed_customer_delivery_is_recorded_but_owner_is_still_notified(tg, monkeypatch):
    outcomes = []
    owner_calls = []
    journal_rows = []
    monkeypatch.setattr(tg.handoff_store, "open_handoff_event", _open_event)
    monkeypatch.setattr(tg.handoff_store, "claim_delivery", lambda *a, **kw: True)
    monkeypatch.setattr(
        tg.handoff_store,
        "complete_delivery",
        lambda *a, target, sent, error_code="", **kw:
        outcomes.append((target, sent, error_code)),
    )
    monkeypatch.setattr(tg, "send_html", lambda *_a, **_kw: (False, "chat not found"))
    monkeypatch.setattr(
        tg,
        "escalate_to_human",
        lambda *_a, **_kw: owner_calls.append(1)
        or {"sent": True, "destination": "bitrix:iu-group", "message_id": 100},
    )
    monkeypatch.setattr(
        tg,
        "journal",
        lambda *a, **kw: journal_rows.append({"args": a, "kwargs": kw}),
    )

    ok = tg._visible_handoff(
        {"id": 123},
        "Когда подключите?",
        "нет утверждённого срока",
        tg.HUMAN_HANDOFF_REPLY,
        reason_code="knowledge_gap",
        source_message_ids=[9002],
    )

    assert ok is False and owner_calls == [1]
    assert ("customer", False, "recipient_unreachable") in outcomes
    assert any(row["kwargs"].get("status") == "error" for row in journal_rows)


def test_successful_human_relay_closes_open_handoff(tg, monkeypatch):
    resolved = []
    monkeypatch.setattr(tg, "find_contact", lambda _who: {"id": 123, "username": "lead"})
    monkeypatch.setattr(tg, "send_html", lambda *_a, **_kw: (True, ""))
    monkeypatch.setattr(tg, "journal", lambda *a, **kw: None)
    monkeypatch.setattr(
        tg.handoff_store,
        "resolve_for_dialog",
        lambda *a, **kw: resolved.append(kw) or 1,
    )

    result = tg.telegram_send_as_account("123", "Ответ менеджера")

    assert result["sent"] is True
    assert resolved == [{
        "bot": tg.MANAGER_CHANNEL,
        "dialog_id": 123,
        "resolution_code": "human_reply_delivered",
    }]

