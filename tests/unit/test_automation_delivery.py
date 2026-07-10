"""Automation delivery: deliver_to supports several comma-separated targets (2026-07-10)."""
from __future__ import annotations


def _row(deliver_to):
    return {"id": 1, "name": "Тест", "deliver_to": deliver_to}


def test_deliver_fans_out_to_all_targets(monkeypatch):
    import agent_automations as aa
    import b24bot

    sent = []
    monkeypatch.setattr(b24bot, "_albery_bitrix_notify",
                        lambda text, dialog_id=None, **kw: (sent.append(dialog_id) or (True, None)))
    ok, err = aa._deliver({"bitrix_bot_id": 80}, _row("16, 22"), "текст")
    assert ok is True and err is None
    assert sent == ["16", "22"]


def test_deliver_partial_failure_still_succeeds(monkeypatch):
    import agent_automations as aa
    import b24bot

    def fake(text, dialog_id=None, **kw):
        return (False, "нет доступа") if dialog_id == "22" else (True, None)

    monkeypatch.setattr(b24bot, "_albery_bitrix_notify", fake)
    ok, err = aa._deliver({"bitrix_bot_id": 80}, _row("16,22"), "текст")
    assert ok is True
    assert "22" in (err or "")


def test_deliver_default_target_when_empty(monkeypatch):
    import agent_automations as aa
    import b24bot

    sent = []
    monkeypatch.setattr(b24bot, "_albery_bitrix_notify",
                        lambda text, dialog_id=None, **kw: (sent.append(dialog_id) or (True, None)))
    monkeypatch.setenv("ALBERY_BITRIX_NOTIFY_CHAT", "chat728")
    ok, _ = aa._deliver({"bitrix_bot_id": 80}, _row(""), "текст")
    assert ok is True and sent == ["chat728"]