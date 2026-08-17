"""Реакцию под сообщением ставит САМ БОТ, а отказ обязан быть слышен.

17.08.2026 владелец сообщил, что агенты перестали ставить реакции. Причина: реакции шли
через `im.v2.Chat.Message.Reaction.add`, у которого нет параметра бота, поэтому портал
выполнял вызов от лица технического пользователя приложения. Тот не участник приватных
чатов «сотрудник ↔ бот» и получал ACCESS_DENIED — проверено на проде на ВСЕХ сообщениях
(и свежих, и недельной давности, у всех ботов) при живом scope `im`.

Вторая половина проблемы — тишина: ошибка глушилась в `logging.debug`, поэтому поломка
жила несколько дней и её заметил владелец, а не мониторинг.
"""
from __future__ import annotations

import logging

import pytest

import b24bot


@pytest.fixture()
def calls(monkeypatch):
    seen: list[tuple[str, dict]] = []

    def fake_call(endpoint, token, method, payload=None, **kw):
        seen.append((method, dict(payload or {})))
        return {"result": True}

    monkeypatch.setattr(b24bot, "_b24_app_call", fake_call)
    return seen


def test_like_goes_through_the_bot_not_the_service_user(calls):
    """Ботовый imbot.message.like отрабатывает там, где im.v2 отвечает ACCESS_DENIED."""
    b24bot._b24_app_react("https://portal", "token", 46212, "like", add=True, bot_id=70)

    assert len(calls) == 1
    method, payload = calls[0]
    assert method == "imbot.message.like"
    assert payload["MESSAGE_ID"] == 46212
    assert payload["BOT_ID"] == 70


def test_service_user_reaction_api_is_never_used(calls):
    """Именно этот вызов и отваливался молча — он не должен вернуться."""
    b24bot._b24_app_react("https://portal", "token", 46212, "like", add=True, bot_id=70)

    assert not any(method.startswith("im.v2.Chat.Message.Reaction") for method, _ in calls)


def test_without_bot_id_nothing_is_sent_and_it_is_logged(calls, caplog):
    """Без бота вызов ушёл бы от техпользователя и снова получил бы отказ."""
    with caplog.at_level(logging.WARNING):
        b24bot._b24_app_react("https://portal", "token", 46212, "like", add=True)

    assert calls == []
    assert any("bot_id" in r.message or "bot_id" in r.getMessage() for r in caplog.records)


def test_failure_is_visible_not_swallowed_in_debug(monkeypatch, caplog):
    """Молчащая реакция — это то, из-за чего поломку нашёл владелец, а не мониторинг."""
    def boom(*a, **kw):
        raise RuntimeError('imbot.message.like: HTTP 400 {"error":"ACCESS_DENIED"}')

    monkeypatch.setattr(b24bot, "_b24_app_call", boom)
    with caplog.at_level(logging.WARNING):
        b24bot._b24_app_react("https://portal", "token", 46212, "like", add=True, bot_id=70)

    assert any(r.levelno >= logging.WARNING for r in caplog.records), (
        "отказ реакции обязан быть виден на уровне WARNING, а не тонуть в debug"
    )


@pytest.mark.parametrize("reaction,add", [("eyes", True), ("eyes", False), ("like", False)])
def test_unsupported_reactions_do_nothing_instead_of_pretending(calls, reaction, add):
    """Произвольные эмодзи ботом на этом портале недоступны.

    Раньше такие вызовы уходили в портал и молча получали отказ — то есть код делал вид,
    что ставит 👀. Сигнал «работаю» даёт индикатор «печатает…».
    """
    b24bot._b24_app_react("https://portal", "token", 46212, reaction, add=add, bot_id=70)
    assert calls == []
