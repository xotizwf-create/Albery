"""Передача запроса Александру: «Готово, передал» обязано быть правдой.

Инцидент 29.07.2026, диалог 22. Агент сказал пользователю «Готово, передал Александру 🙌»,
маркер [[ESCALATE: …]] выставил честно — но доставка провалилась: запись 5 в access_requests
имеет delivered=False, delivery_error='Unauthorized'. Токен бота, единственный источник для
уведомлений (TELEGRAM_BOT_TOKEN в /root/.hermes/.env), отозван, и все эскалации молча гибли,
пока агент отчитывался об успехе.

Молчание при отказе — сломанная логика (CLAUDE.md п.10.2). Поэтому: сначала запасной канал
(уведомления Битрикса, он на другом токене), а если и он не смог — пользователю уходит честная
поправка в тот же диалог.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def bot(monkeypatch):
    import b24bot

    logged: list[dict] = []
    replies: list[str] = []
    monkeypatch.setattr(b24bot, "_b24_requester_name", lambda uid: "Александр Никитенко")
    monkeypatch.setattr(
        b24bot, "_b24_log_access_request",
        lambda dialog_id, from_user_id, requester_name, request_text, delivered, delivery_error:
            logged.append({"delivered": delivered, "error": delivery_error, "text": request_text}),
    )
    monkeypatch.setattr(
        b24bot, "_b24_app_reply",
        lambda endpoint, token, bot_id, dialog_id, text, **kw: replies.append(text),
    )
    return b24bot, logged, replies, monkeypatch


REQUEST = "выделить жирным вопросы в Google-документе"
CTX = {"client_endpoint": "https://portal/rest/", "access_token": "tok", "bot_id": 1}


class TestEscalationDelivery:
    def test_telegram_ok_user_is_not_bothered(self, bot):
        b24bot, logged, replies, mp = bot
        set_channels(mp, b24bot, tg=(True, None), bitrix=(True, None))
        b24bot._b24_forward_access_request("22", 22, REQUEST, **CTX)
        assert logged[-1]["delivered"] is True
        assert replies == []  # доставили — извиняться не за что

    def test_falls_back_to_bitrix_when_telegram_token_is_dead(self, bot):
        b24bot, logged, replies, mp = bot
        set_channels(mp, b24bot, tg=(False, "Unauthorized"), bitrix=(True, None))
        b24bot._b24_forward_access_request("22", 22, REQUEST, **CTX)
        assert logged[-1]["delivered"] is True  # запрос дошёл запасным каналом
        assert replies == []

    def test_both_channels_dead_user_is_told_the_truth(self, bot):
        b24bot, logged, replies, mp = bot
        set_channels(mp, b24bot, tg=(False, "Unauthorized"), bitrix=(False, "chat not found"))
        b24bot._b24_forward_access_request("22", 22, REQUEST, **CTX)
        assert logged[-1]["delivered"] is False
        assert len(replies) == 1, "отказ доставки не может быть молчаливым"
        assert "не получилось" in replies[0].lower() or "не удалось" in replies[0].lower()
        assert "Александр" in replies[0]  # человеку сказано, к кому идти самому

    def test_without_reply_context_it_still_logs_and_does_not_crash(self, bot):
        b24bot, logged, replies, mp = bot
        set_channels(mp, b24bot, tg=(False, "Unauthorized"), bitrix=(False, "no ctx"))
        b24bot._b24_forward_access_request("22", 22, REQUEST)
        assert logged[-1]["delivered"] is False
        assert replies == []


def set_channels(monkeypatch, b24bot, tg, bitrix):
    """Только через monkeypatch: прямое присваивание в модуль протекало в соседние тесты."""
    monkeypatch.setattr(b24bot, "_albery_tg_notify", lambda text, chat=None: tg)
    monkeypatch.setattr(b24bot, "_albery_bitrix_notify", lambda text, dialog_id=None, **kw: bitrix)
