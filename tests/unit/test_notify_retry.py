"""Личное сообщение не должно теряться из-за кратковременного сбоя портала.

20.07.2026, обход задач 12:00: три сообщения (Евгений Палей, Олеся Тагирова, Дмитрий
Строгонов) не ушли — портал вернул HTTP 500 INTERNAL_SERVER_ERROR на imbot.message.add.
Через полчаса тот же канал работал. Код записал строчку в лог и пошёл дальше: люди ничего
не получили, и никто об этом не узнал.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def bot(app_module, monkeypatch):
    import b24bot

    monkeypatch.setattr(b24bot.time, "sleep", lambda *_: None)   # тест не ждёт
    monkeypatch.setattr(b24bot, "_b24_app_access_token", lambda: ("endpoint", "token"))
    monkeypatch.setattr(b24bot, "_b24_load_state", lambda: {"bot_id": 24})
    return b24bot


def test_transient_500_is_retried_until_delivered(bot, monkeypatch):
    calls = []

    def flaky(endpoint, token, method, params):
        calls.append(method)
        if len(calls) < 3:
            raise RuntimeError('imbot.message.add: HTTP 500 {"error":"INTERNAL_SERVER_ERROR"}')
        return {"result": 1}

    monkeypatch.setattr(bot, "_b24_app_call", flaky)

    ok, err = bot._albery_bitrix_notify("привет", dialog_id="14")

    assert ok is True and err is None
    assert len(calls) == 3, "должно быть две неудачные попытки и третья успешная"


def test_gives_up_after_the_last_attempt(bot, monkeypatch):
    calls = []

    def always_500(endpoint, token, method, params):
        calls.append(method)
        raise RuntimeError('imbot.message.add: HTTP 500 {"error":"INTERNAL_SERVER_ERROR"}')

    monkeypatch.setattr(bot, "_b24_app_call", always_500)

    ok, err = bot._albery_bitrix_notify("привет", dialog_id="14")

    assert ok is False and "500" in err
    assert len(calls) == 3, "ровно три попытки, без бесконечного цикла"


def test_permanent_error_is_not_retried(bot, monkeypatch):
    """Ошибку прав или неверный диалог повторять бессмысленно — только тратить время."""
    calls = []

    def bad_request(endpoint, token, method, params):
        calls.append(method)
        raise RuntimeError("imbot.message.add: ERROR_ACCESS_DENIED")

    monkeypatch.setattr(bot, "_b24_app_call", bad_request)

    ok, err = bot._albery_bitrix_notify("привет", dialog_id="14")

    assert ok is False and "ACCESS_DENIED" in err
    assert len(calls) == 1


def test_success_on_first_try_sends_once(bot, monkeypatch):
    calls = []
    monkeypatch.setattr(bot, "_b24_app_call",
                        lambda e, t, m, p: (calls.append(m), {"result": 1})[1])

    ok, _ = bot._albery_bitrix_notify("привет", dialog_id="14")

    assert ok is True and len(calls) == 1, "успешная отправка не должна дублироваться"


def test_undelivered_messages_are_surfaced_to_the_owner(app_module, monkeypatch):
    """Потеря сообщения обязана попасть в отчёт владельцу, а не только в лог."""
    import task_checkin

    task_checkin._UNDELIVERED.clear()
    task_checkin._UNDELIVERED.append("Евгений Палей: HTTP 500 INTERNAL_SERVER_ERROR")
    sent = {}

    import b24bot
    monkeypatch.setattr(b24bot, "_albery_bitrix_notify",
                        lambda text, **kw: (sent.update(text=text), (True, None))[1])
    monkeypatch.setattr("mcp.context_server._task_deep_link", lambda tid: f"https://b24/{tid}",
                        raising=False)

    task_checkin._report_to_owner({"scanned": 1}, {}, [], [], 24)

    assert "Не доставлено" in sent["text"]
    assert "Евгений Палей" in sent["text"]
    task_checkin._UNDELIVERED.clear()
