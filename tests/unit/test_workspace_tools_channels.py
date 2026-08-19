"""Инструменты рабочего окна одинаково работают со всеми каналами, включая Авито.

Переписки Авито лежат в тех же таблицах, что и телеграмные, и набор инструментов у них ОДИН:
второй комплект «для Авито» означал бы два места, где чинят одно и то же. Но одинаковый набор
обязан говорить о канале правду — иначе агент примет лида с Авито за телеграмного, а ответ
поставит в очередь, которую некому разгрести.
"""
from __future__ import annotations

import pytest

import funnel_workspace_tools as tools


@pytest.fixture(autouse=True)
def canonical_host(monkeypatch):
    monkeypatch.setenv("FUNNEL_WORKSPACE_PUBLIC_BASE", "https://www.m4s.ru")


def _avito(**over):
    row = {
        "id": 648,
        "source_key": "avito",
        "source_name": "Авито",
        "business_connection_id": "main",
        "display_name": "Александр",
        "username": None,
        "external_chat_id": "u2i-myxxGegbwRTLcBnF1h15Uw",
        "external_user_id": None,
        "deal_id": None,
        "stage_id": None,
        "status": "open",
        "control_mode": "ai",
        "state_version": 3,
        "unread_count": 1,
        "last_message_text": "Да, продаю",
        "last_message_at": None,
        "awaiting_reply_since": None,
        "metadata": {"listing": {"id": "4297041572", "title": "Генератор бензиновый 3,3 кВт",
                                 "price": "13 999 ₽",
                                 "url": "https://www.avito.ru/4297041572"}},
    }
    row.update(over)
    return row


def _telegram(**over):
    row = {
        "id": 12,
        "source_key": "telegram",
        "source_name": "Telegram",
        "business_connection_id": "",
        "display_name": "Иван",
        "username": "ivan",
        "external_chat_id": "9001",
        "external_user_id": 9001,
        "deal_id": 188,
        "stage_id": "C16:NEW",
        "status": "open",
        "control_mode": "ai",
        "state_version": 4,
        "unread_count": 2,
        "last_message_text": "Здравствуйте",
        "last_message_at": None,
        "awaiting_reply_since": None,
        "metadata": {},
    }
    row.update(over)
    return row


def test_an_avito_lead_is_not_shown_as_a_telegram_one():
    brief = tools._conversation_brief(_avito(display_name=None))

    assert brief["channel"] == "Авито"
    assert brief["channel_key"] == "avito"
    assert "Telegram" not in brief["client"]
    # Телеграмного id у собеседника с Авито нет — поля быть не должно вовсе.
    assert "telegram_id" not in brief


def test_the_listing_of_an_avito_conversation_is_visible_to_the_agent():
    """Разговор на Авито идёт вокруг объявления: без него агент не поймёт, о чём речь."""
    brief = tools._conversation_brief(_avito())

    assert brief["listing"]["id"] == "4297041572"
    assert brief["listing"]["title"].startswith("Генератор")
    assert brief["listing"]["url"].endswith("4297041572")


def test_a_telegram_conversation_keeps_its_own_fields():
    brief = tools._conversation_brief(_telegram())

    assert brief["channel"] == "Telegram"
    assert brief["telegram_id"] == 9001
    assert "listing" not in brief


def test_conversations_can_be_asked_for_one_channel(monkeypatch):
    seen = {}
    monkeypatch.setattr(tools.store, "list_conversations",
                        lambda **kw: seen.update(kw) or {"items": [], "total": 0})

    tools.list_conversations({"channel": "avito"})

    assert seen["source"] == "avito"


def test_an_unknown_channel_is_refused_instead_of_silently_showing_everything(monkeypatch):
    monkeypatch.setattr(tools.store, "list_conversations",
                        lambda **kw: {"items": [], "total": 0})

    with pytest.raises(tools.WorkspaceToolError):
        tools.list_conversations({"channel": "whatsapp"})


def test_reply_is_refused_when_the_avito_transport_cannot_deliver(monkeypatch):
    """Строка в очереди, которую никто не разгребёт, выглядит как отправленный ответ."""
    monkeypatch.setattr(tools.store, "get_conversation", lambda cid: _avito())
    monkeypatch.setattr(tools, "_transport_block_reason",
                        lambda conversation: "Сессия аккаунта «Основной»: нужен повторный вход.")
    monkeypatch.setattr(tools.store, "enqueue_outgoing_agent",
                        lambda *a, **k: pytest.fail("ставить в очередь было нельзя"))

    with pytest.raises(tools.WorkspaceToolError) as failure:
        tools.reply({"conversation_id": 648, "text": "Добрый день!"})

    assert "повторный вход" in str(failure.value)


def test_reply_goes_through_when_the_avito_transport_is_alive(monkeypatch):
    queued = {}
    monkeypatch.setattr(tools.store, "get_conversation", lambda cid: _avito())
    monkeypatch.setattr(tools, "_transport_block_reason", lambda conversation: None)
    monkeypatch.setattr(
        tools.store, "enqueue_outgoing_agent",
        lambda cid, **kw: queued.update({"cid": cid, **kw})
        or {"message": {"id": 7}, "outbox": {"delivery_status": "pending"}})

    answer = tools.reply({"conversation_id": 648, "text": "Добрый день!"})

    assert answer["sent"] is True and queued["cid"] == 648


def test_handing_an_avito_dialog_to_the_ai_does_not_ask_the_telegram_rollout(monkeypatch):
    """У Авито нет ни telegram-id, ни раскатки: телеграмный рубильник отказал бы всегда."""
    import funnel_telegram_gateway

    monkeypatch.setattr(tools.store, "get_conversation", lambda cid: _avito(control_mode="human"))
    monkeypatch.setattr(tools, "_transport_block_reason", lambda conversation: None)
    monkeypatch.setattr(funnel_telegram_gateway, "ai_allowed_in_channel",
                        lambda *a, **k: pytest.fail("телеграмный рубильник тут ни при чём"))
    monkeypatch.setattr(tools.store, "transition_control",
                        lambda cid, **kw: {"control_mode": kw["mode"]})

    answer = tools.set_control({"conversation_id": 648, "mode": "ai"})

    assert answer["control"] == "отвечает ИИ"


def test_the_ai_is_not_handed_a_dialog_it_cannot_answer_in(monkeypatch):
    monkeypatch.setattr(tools.store, "get_conversation", lambda cid: _avito(control_mode="human"))
    monkeypatch.setattr(tools, "_transport_block_reason",
                        lambda conversation: "Транспорт Авито выключен — сообщение никто не доставит.")
    monkeypatch.setattr(tools.store, "transition_control",
                        lambda *a, **k: pytest.fail("передавать разговор ИИ было нельзя"))

    with pytest.raises(tools.WorkspaceToolError):
        tools.set_control({"conversation_id": 648, "mode": "ai"})


def test_telegram_still_obeys_its_own_rollout(monkeypatch):
    import funnel_telegram_gateway

    monkeypatch.setattr(tools.store, "get_conversation", lambda cid: _telegram(control_mode="human"))
    monkeypatch.setattr(funnel_telegram_gateway, "ai_allowed_in_channel", lambda *a, **k: False)
    monkeypatch.setattr(tools.store, "transition_control",
                        lambda *a, **k: pytest.fail("раскатка запретила передачу"))

    with pytest.raises(tools.WorkspaceToolError) as failure:
        tools.set_control({"conversation_id": 12, "mode": "ai"})

    assert "Telegram" in str(failure.value)
