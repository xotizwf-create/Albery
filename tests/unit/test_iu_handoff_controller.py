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


def test_event_key_depends_on_source_event_not_later_classification(tg):
    author = {"id": 123}

    first = tg._handoff_event_key(
        author,
        "Когда подключите?",
        "model_failure",
        deal_id=78,
        source_message_ids=[9001],
    )
    rerouted = tg._handoff_event_key(
        author,
        "Когда подключите?",
        "knowledge_gap",
        deal_id=99,
        source_message_ids=[9001],
    )
    next_message = tg._handoff_event_key(
        author,
        "Когда подключите?",
        "knowledge_gap",
        deal_id=99,
        source_message_ids=[9002],
    )

    assert first == rerouted
    assert first != next_message


def test_replayed_failed_event_is_not_automatically_retried(tg, monkeypatch):
    duplicate = {
        **_open_event(),
        "event_created": False,
        "customer_delivery_status": "failed",
        "internal_delivery_status": "failed",
    }
    monkeypatch.setattr(tg.handoff_store, "open_handoff_event", lambda *a, **kw: duplicate)
    monkeypatch.setattr(tg.handoff_store, "claim_delivery", lambda *a, **kw: False)
    monkeypatch.setattr(
        tg,
        "send_html",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("ambiguous retry")),
    )
    monkeypatch.setattr(
        tg,
        "escalate_to_human",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("duplicate card")),
    )

    assert tg._visible_handoff(
        {"id": 123},
        "Когда подключите?",
        "нет утверждённого срока",
        tg.HUMAN_HANDOFF_REPLY,
        reason_code="knowledge_gap",
        source_message_ids=[9001],
    ) is False


def test_existing_delivery_failure_is_recorded_without_second_customer_send(tg, monkeypatch):
    outcomes = []
    monkeypatch.setattr(tg.handoff_store, "open_handoff_event", _open_event)
    monkeypatch.setattr(tg.handoff_store, "claim_delivery", lambda *a, **kw: True)
    monkeypatch.setattr(
        tg.handoff_store,
        "complete_delivery",
        lambda *a, target, sent, error_code="", **kw:
        outcomes.append((target, sent, error_code)),
    )
    monkeypatch.setattr(
        tg,
        "send_html",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("second customer send")),
    )
    monkeypatch.setattr(
        tg,
        "escalate_to_human",
        lambda *_a, **_kw: {
            "sent": True,
            "destination": "bitrix:iu-group",
            "message_id": 100,
        },
    )
    monkeypatch.setattr(tg, "journal", lambda *a, **kw: None)

    ok = tg._visible_handoff(
        {"id": 123},
        "Когда подключите?",
        "обычный ответ не доставлен",
        "Ответ менеджера",
        reason_code="delivery_failure",
        source_message_ids=[9003],
        deliver_customer=False,
        customer_error_code="timeout",
    )

    assert ok is False
    assert ("customer", False, "timeout") in outcomes
    assert ("internal", True, "") in outcomes


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


def test_direct_human_business_reply_closes_handoff_but_bot_echo_does_not(
        tg, monkeypatch, tmp_path):
    resolved = []
    journal_rows = []
    monkeypatch.setattr(tg, "BUSINESS_LOG_PATH", tmp_path / "business.jsonl")
    monkeypatch.setattr(tg, "business_autoreply_enabled", lambda: False)
    monkeypatch.setattr(
        tg.handoff_store,
        "resolve_for_dialog",
        lambda *a, **kw: resolved.append(kw) or 1,
    )
    monkeypatch.setattr(
        tg,
        "journal",
        lambda *a, **kw: journal_rows.append((a, kw)),
    )
    message = {
        "message_id": 701,
        "business_connection_id": "C1",
        "chat": {"id": 123, "type": "private", "first_name": "Клиент"},
        "from": {"id": 871, "first_name": "Владелец"},
        "text": "Точный ответ менеджера",
    }

    tg.handle_business_message(message)
    tg.handle_business_message({
        **message,
        "message_id": 702,
        "sender_business_bot": {"id": 999, "is_bot": True},
    })

    assert resolved == [{
        "bot": tg.MANAGER_CHANNEL,
        "dialog_id": 123,
        "resolution_code": "human_reply_delivered",
    }]
    assert len(journal_rows) == 1
    args, kwargs = journal_rows[0]
    assert args[2] == "out" and kwargs["kind"] == "lead_chat"
    assert kwargs["meta"]["customer_visible"] is True


def test_overdue_handoff_reminds_owner_without_copying_client_text(tg, monkeypatch):
    cards = []
    recorded = []
    monkeypatch.setattr(
        tg.handoff_store,
        "overdue_handoffs",
        lambda *a, **kw: [{
            "id": 17,
            "bot": tg.MANAGER_CHANNEL,
            "dialog_id": "123",
            "deal_id": 78,
            "reason_code": "knowledge_gap",
            "owner_id": "iu-group",
            "owner_name": "Группа «Работа с ИУ»",
            "due_at": "2026-07-25T19:30:00+03:00",
            "customer_notified": True,
        }],
    )
    monkeypatch.setattr(
        tg,
        "_deliver_handoff_reminder",
        lambda card: cards.append(card) or {
            "sent": True,
            "destination": "bitrix:iu-group",
            "message_id": 901,
            "error_code": "",
        },
    )
    monkeypatch.setattr(
        tg.handoff_store,
        "record_reminder_result",
        lambda *a, **kw: recorded.append((a, kw)),
    )

    result = tg.check_overdue_handoffs()

    assert result == {"checked": 1, "notified": 1, "failed": 0}
    assert "Handoff #17" in cards[0] and "Ответственный:" in cards[0]
    assert "Диалог:" in cards[0] and "Когда подключите?" not in cards[0]
    assert recorded[0][1]["sent"] is True
    assert recorded[0][1]["external_message_id"] == 901


def test_handoff_reminder_uses_telegram_if_bitrix_has_no_delivery_proof(tg, monkeypatch):
    monkeypatch.setattr(tg, "mcp_call", lambda *a, **kw: {"sent": True})
    monkeypatch.setenv("TG_ESCALATION_CHAT_ID", "871")
    monkeypatch.setattr(
        tg,
        "api",
        lambda method, **kw: {"message_id": 902},
    )

    result = tg._deliver_handoff_reminder("[b]Handoff #17[/b]")

    assert result["sent"] is True
    assert result["destination"] == "telegram:escalation"
    assert result["message_id"] == 902


def test_handoff_reminder_failure_is_bounded_and_recorded(tg, monkeypatch):
    recorded = []
    monkeypatch.setattr(
        tg.handoff_store,
        "overdue_handoffs",
        lambda *a, **kw: [{
            "id": 18,
            "bot": tg.MANAGER_CHANNEL,
            "dialog_id": "124",
            "reason_code": "model_failure",
            "owner_id": "iu-group",
            "owner_name": "Группа «Работа с ИУ»",
            "due_at": "2026-07-25T19:30:00+03:00",
            "customer_notified": False,
        }],
    )
    monkeypatch.setattr(
        tg,
        "_deliver_handoff_reminder",
        lambda card: {
            "sent": False,
            "destination": "telegram:escalation",
            "message_id": "",
            "error_code": "network_error",
        },
    )
    monkeypatch.setattr(
        tg.handoff_store,
        "record_reminder_result",
        lambda *a, **kw: recorded.append(kw),
    )

    result = tg.check_overdue_handoffs()

    assert result == {"checked": 1, "notified": 0, "failed": 1}
    assert recorded == [{
        "sent": False,
        "destination": "telegram:escalation",
        "external_message_id": "",
        "error_code": "network_error",
    }]


def test_unexpected_business_update_failure_becomes_record_only_handoff(tg, monkeypatch):
    captured = []
    monkeypatch.setattr(
        tg,
        "handle_business_message",
        lambda _msg: (_ for _ in ()).throw(RuntimeError("secret stack")),
    )
    monkeypatch.setattr(
        tg,
        "_visible_handoff",
        lambda author, client_text, question, reply, **kw:
        captured.append({
            "author": author,
            "client_text": client_text,
            "question": question,
            "reply": reply,
            **kw,
        }) or False,
    )

    tg._handle_update_safely({
        "business_message": {
            "message_id": 9004,
            "business_connection_id": "C1",
            "chat": {"id": 123, "type": "private"},
            "from": {"id": 123, "username": "lead"},
            "text": "Когда подключите?",
        },
    })

    assert len(captured) == 1
    assert captured[0]["reason_code"] == "unexpected_failure"
    assert captured[0]["source_message_ids"] == [9004]
    assert captured[0]["deliver_customer"] is False
    assert captured[0]["customer_error_code"] == "processing_outcome_unknown"
    assert "secret stack" not in captured[0]["reply"]
