"""Реакции: 👀 когда взяли в работу, 👍 когда ответили — и отказ обязан быть слышен.

Разобрано 17.08.2026 по жалобе владельца. В системе ДВА разных доступа к порталу:

* токен из события вебхука — контекст бота, у него есть доступ к приватному чату
  «сотрудник ↔ бот», и через него im.v2 ставит любой эмодзи (так и работал 👀);
* постоянный токен приложения ходит от лица технического пользователя 22, который в
  этих чатах не участник — портал отвечает ACCESS_DENIED. Им пользуется отложенная
  доставка, поэтому финальный 👍 молча не ставился.

Первая попытка починки заменила ВСЕ реакции ботовым imbot.message.like и тем самым
убила рабочий 👀: замер был снят только со сломанной половины. Здесь закреплено и то,
что штатный путь остаётся основным, и то, что у лайка есть запасной.
"""
from __future__ import annotations

import logging

import pytest

import b24bot

DENIED = 'HTTP 400 {"error":"ACCESS_DENIED","error_description":"ACCESS_DENIED"}'


@pytest.fixture()
def calls(monkeypatch):
    seen: list[tuple[str, dict]] = []

    def fake_call(endpoint, token, method, payload=None, **kw):
        seen.append((method, dict(payload or {})))
        return {"result": True}

    monkeypatch.setattr(b24bot, "_b24_app_call", fake_call)
    return seen


@pytest.fixture()
def denied_calls(monkeypatch):
    """Портал отказывает штатному пути — как с постоянным токеном приложения."""
    seen: list[tuple[str, dict]] = []

    def fake_call(endpoint, token, method, payload=None, **kw):
        seen.append((method, dict(payload or {})))
        if method.startswith("im.v2.Chat.Message.Reaction"):
            raise RuntimeError(f"{method}: {DENIED}")
        return {"result": True}

    monkeypatch.setattr(b24bot, "_b24_app_call", fake_call)
    return seen


def test_eye_reaction_uses_the_normal_path(calls):
    """👀 — главный признак «сообщение увидели»; ботового аналога у него нет."""
    b24bot._b24_app_react("https://portal", "event-token", 46212, "eyes", add=True, bot_id=70)

    assert [m for m, _ in calls] == ["im.v2.Chat.Message.Reaction.add"]
    assert calls[0][1] == {"messageId": 46212, "reaction": "eyes"}


def test_like_uses_the_normal_path_when_access_allows(calls):
    b24bot._b24_app_react("https://portal", "event-token", 46212, "like", add=True, bot_id=70)

    assert [m for m, _ in calls] == ["im.v2.Chat.Message.Reaction.add"]


def test_like_falls_back_to_the_bot_when_access_is_denied(denied_calls):
    """Отложенная доставка ходит токеном приложения — там штатный путь закрыт."""
    b24bot._b24_app_react("https://portal", "app-token", 46212, "like", add=True, bot_id=70)

    methods = [m for m, _ in denied_calls]
    assert methods == ["im.v2.Chat.Message.Reaction.add", "imbot.message.like"]
    assert denied_calls[1][1] == {"MESSAGE_ID": 46212, "BOT_ID": 70}


def test_denied_eye_reaction_is_reported_not_silent(denied_calls, caplog):
    """У 👀 ботового пути нет — значит отказ обязан быть виден, а не проглочен."""
    with caplog.at_level(logging.WARNING):
        b24bot._b24_app_react("https://portal", "app-token", 46212, "eyes", add=True, bot_id=70)

    assert any(r.levelno >= logging.WARNING for r in caplog.records), (
        "молчащий отказ — это то, из-за чего поломку нашёл владелец, а не мониторинг"
    )


def test_failure_of_the_bot_fallback_is_reported(monkeypatch, caplog):
    def boom(endpoint, token, method, payload=None, **kw):
        raise RuntimeError(f"{method}: {DENIED}")

    monkeypatch.setattr(b24bot, "_b24_app_call", boom)
    with caplog.at_level(logging.WARNING):
        b24bot._b24_app_react("https://portal", "app-token", 46212, "like", add=True, bot_id=70)

    assert any(r.levelno >= logging.WARNING for r in caplog.records)


def test_non_access_errors_do_not_trigger_the_fallback(monkeypatch, caplog):
    """Сеть моргнула — это не повод менять способ: иначе мы прячем настоящую причину."""
    seen: list[str] = []

    def fake_call(endpoint, token, method, payload=None, **kw):
        seen.append(method)
        raise RuntimeError(f"{method}: HTTP 500 internal")

    monkeypatch.setattr(b24bot, "_b24_app_call", fake_call)
    with caplog.at_level(logging.WARNING):
        b24bot._b24_app_react("https://portal", "event-token", 46212, "like", add=True, bot_id=70)

    assert seen == ["im.v2.Chat.Message.Reaction.add"]
    assert any(r.levelno >= logging.WARNING for r in caplog.records)
