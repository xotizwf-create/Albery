"""О закрытии сессии пользователь получает ровно одно предупреждение.

Владелец 20.07.2026: догоняющий баннер «🔄 Ваше сообщение начало новый разговор…» — лишний
флуд; достаточно планового «⏸️ Больше 3 ч без новых сообщений…», которое приходит в момент
самого закрытия контекста.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def bot(app_module, monkeypatch):
    import b24bot

    sent = []
    monkeypatch.setattr(b24bot, "_b24_app_access_token", lambda: ("endpoint", "token"))
    monkeypatch.setattr(b24bot, "_b24_load_state", lambda: {"bot_id": 24})
    monkeypatch.setattr(b24bot, "_b24_app_call",
                        lambda e, t, m, params: sent.append(params.get("MESSAGE")) or {"result": 1})
    return b24bot, sent


def test_scheduled_notice_is_sent(bot):
    b24bot, sent = bot

    b24bot._b24_notify_session_reset("16")

    assert len(sent) == 1
    assert sent[0].startswith("⏸️")
    assert "разговор завершён" in sent[0]


def test_late_notice_is_not_sent(bot):
    """Именно это сообщение владелец попросил убрать."""
    b24bot, sent = bot

    b24bot._b24_notify_session_reset("16", late=True)

    assert sent == [], "догоняющий баннер больше не отправляется"


def test_no_new_conversation_banner_anywhere(bot):
    b24bot, sent = bot

    for late in (False, True):
        b24bot._b24_notify_session_reset("30", late=late)

    assert all("начало новый разговор" not in (m or "") for m in sent)
    assert len(sent) == 1


def test_non_chat_dialogs_are_skipped(bot):
    """У тредов задач нет чата с ботом — туда писать некуда."""
    b24bot, sent = bot

    b24bot._b24_notify_session_reset("task-1752")

    assert sent == []
