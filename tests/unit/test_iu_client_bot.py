from __future__ import annotations

# Клиентский вход в воронку ИУ: /start, три кнопки, ответы ИИ и вызов оператора.

import sys
from types import SimpleNamespace

import pytest

import funnel_telegram_gateway as gateway
import iu_client_bot as bot


class FakeStore:
    """Журнал обращений в объёме, который нужен сценарию бота."""

    def __init__(self, *, agent_replies=0, control_mode="ai"):
        self.ingested: list[dict] = []
        self.queued: list[dict] = []
        self.transitions: list[dict] = []
        self.agent_replies = agent_replies
        self.control_mode = control_mode

    def ingest_business_message(self, **kwargs):
        self.ingested.append(kwargs)
        return {"conversation": {"id": 5, "state_version": 7}, "message": {"id": 50}}

    def get_conversation(self, conversation_id):
        return {
            "id": conversation_id,
            "state_version": 7,
            "control_mode": self.control_mode,
            "source_key": "telegram_bot",
        }

    def count_agent_replies(self, conversation_id):
        return self.agent_replies

    def enqueue_outgoing_agent(self, conversation_id, **kwargs):
        self.queued.append({"conversation_id": conversation_id, **kwargs})
        return {"outbox": {"id": 90}, "message": {"id": 91}}

    def mark_waiting_human(self, conversation_id, **kwargs):
        self.transitions.append({"conversation_id": conversation_id, **kwargs})
        return {"conversation": {"id": conversation_id, "status": "waiting"}}


def _tg(monkeypatch, answered=None):
    tg = SimpleNamespace(
        is_owner=lambda _sender: False,
        _workers=SimpleNamespace(submit=lambda *_a: None),
        _handle_update_safely=lambda _u: None,
        api=lambda method, **kwargs: (answered.append((method, kwargs)) if answered is not None else None) or {},
        terms_text=lambda: "Условия ИУ: комиссия, сроки, документы.",
        _strip_markup=lambda value: str(value),
    )
    monkeypatch.setitem(sys.modules, "tg_agent", tg)
    return tg


def _start_update():
    return {
        "message": {
            "message_id": 1,
            "date": 1785600000,
            "chat": {"id": 555, "type": "private"},
            "from": {"id": 555, "first_name": "Пётр", "username": "petr"},
            "text": "/start",
        }
    }


def _callback_update(data):
    return {
        "callback_query": {
            "id": "cb-1",
            "data": data,
            "from": {"id": 555, "first_name": "Пётр", "username": "petr"},
            "message": {"message_id": 2, "date": 1785600100, "chat": {"id": 555, "type": "private"}},
        }
    }


@pytest.fixture(autouse=True)
def _channel_on(monkeypatch):
    monkeypatch.setenv("IU_CLIENT_BOT_ENABLED", "1")


def test_start_creates_a_lead_and_shows_three_buttons(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(gateway, "_store", lambda: store)
    _tg(monkeypatch)

    gateway.route_captured_update(_start_update(), provider_update_id=10)

    # Клиент попал в общий журнал обращений — отсюда же заводится сделка в CRM.
    assert store.ingested and store.ingested[0]["source_key"] == "telegram_bot"
    assert store.ingested[0]["author_type"] == "client"
    # Приветствие с кнопками ушло durable-очередью, а не мимо журнала.
    assert store.queued, "ответ на /start обязан попасть в ленту обращения"
    reply = store.queued[0]
    keyboard = reply["metadata"]["reply_markup"]["inline_keyboard"]
    assert [row[0]["text"] for row in keyboard] == [
        bot.BUTTON_TERMS,
        bot.BUTTON_JOIN,
        bot.BUTTON_ASK,
    ]


def test_terms_button_sends_the_real_terms(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(gateway, "_store", lambda: store)
    monkeypatch.setattr(gateway, "_conversation_for_bot_chat", lambda *_a, **_k: 5)
    _tg(monkeypatch)

    gateway.route_captured_update(_callback_update(bot.CB_TERMS))

    # Нажатие видно команде как реплика клиента, иначе лента обрывается.
    assert store.ingested[0]["text"] == bot.BUTTON_TERMS
    assert "Условия ИУ" in store.queued[0]["text"]


def test_join_button_answers_honestly_while_the_form_is_missing(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(gateway, "_store", lambda: store)
    monkeypatch.setattr(gateway, "_conversation_for_bot_chat", lambda *_a, **_k: 5)
    _tg(monkeypatch)

    gateway.route_captured_update(_callback_update(bot.CB_JOIN))

    text = store.queued[0]["text"]
    assert "менеджер" in text.lower()
    # Пока ссылки на анкету нет, обещать её нельзя.
    assert "http" not in text


def test_operator_button_appears_only_after_the_third_ai_reply():
    assert bot.should_offer_operator(0) is False
    assert bot.should_offer_operator(2) is False
    assert bot.should_offer_operator(3) is True
    # Диалог уже у человека — звать его второй раз незачем.
    assert bot.should_offer_operator(5, control_mode="human") is False


def test_calling_the_operator_hands_the_dialog_to_a_human(monkeypatch):
    store = FakeStore(agent_replies=3)
    monkeypatch.setattr(gateway, "_store", lambda: store)
    monkeypatch.setattr(gateway, "_conversation_for_bot_chat", lambda *_a, **_k: 5)
    _tg(monkeypatch)

    gateway.route_captured_update(_callback_update(bot.CB_OPERATOR))

    assert store.transitions, "обращение обязано встать в очередь к человеку"
    assert "оператор" in store.transitions[0]["reason"].lower()
    assert "менеджер" in store.queued[0]["text"].lower()


def test_ai_reply_carries_the_operator_button_after_the_threshold(monkeypatch):
    import iu_contract

    store = FakeStore(agent_replies=3)
    monkeypatch.setattr(gateway, "_store", lambda: store)
    _tg(monkeypatch)

    outcome = SimpleNamespace(
        reply="Комиссия зависит от категории товара.",
        action=iu_contract.REPLY_ONLY,
        escalate=False,
        reason="",
        stage_move="",
        answered_client=True,
        sources=(),
        trace={},
    )
    prepared = gateway.prepare_reply(
        outcome,
        telegram_user_id=555,
        facts=None,
        conversation={"id": 5, "source_key": "telegram_bot", "control_mode": "ai"},
    )

    keyboard = prepared.metadata.get("reply_markup", {}).get("inline_keyboard")
    assert keyboard, "после третьего ответа ИИ клиент должен видеть кнопку вызова оператора"
    assert keyboard[0][0]["text"] == bot.BUTTON_OPERATOR


def test_ai_reply_has_no_button_before_the_threshold(monkeypatch):
    import iu_contract

    store = FakeStore(agent_replies=1)
    monkeypatch.setattr(gateway, "_store", lambda: store)
    _tg(monkeypatch)

    outcome = SimpleNamespace(
        reply="Комиссия зависит от категории товара.",
        action=iu_contract.REPLY_ONLY,
        escalate=False,
        reason="",
        stage_move="",
        answered_client=True,
        sources=(),
        trace={},
    )
    prepared = gateway.prepare_reply(
        outcome,
        telegram_user_id=555,
        facts=None,
        conversation={"id": 5, "source_key": "telegram_bot", "control_mode": "ai"},
    )

    assert "reply_markup" not in prepared.metadata


def test_business_dialogs_never_get_bot_buttons(monkeypatch):
    import iu_contract

    store = FakeStore(agent_replies=9)
    monkeypatch.setattr(gateway, "_store", lambda: store)
    _tg(monkeypatch)

    outcome = SimpleNamespace(
        reply="Отвечаю по вашему вопросу.",
        action=iu_contract.REPLY_ONLY,
        escalate=False,
        reason="",
        stage_move="",
        answered_client=True,
        sources=(),
        trace={},
    )
    prepared = gateway.prepare_reply(
        outcome,
        telegram_user_id=555,
        facts=None,
        conversation={"id": 5, "source_key": "telegram", "control_mode": "ai"},
    )

    # У переписки менеджера кнопок бота быть не должно — там отвечает человек.
    assert "reply_markup" not in prepared.metadata


def test_keyboard_reaches_telegram_on_delivery(monkeypatch):
    calls: list[tuple[str, dict]] = []

    class Store:
        def __init__(self):
            self.item = {
                "id": 12,
                "message_id": 92,
                "conversation_id": 5,
                "conversation_version": 3,
                "author_type": "agent",
                "source_key": "telegram_bot",
                "external_chat_id": "555",
                "business_connection_id": "",
                "text": "Здравствуйте!",
                "payload": {"reply_markup": bot.main_keyboard()},
            }
            self.finishes = []

        def outbox_send_guard(self, _id, *, worker_id):
            return {"allowed": True, "outbox": self.item}

        def begin_outbox_send(self, _id, *, worker_id, lease_seconds):
            return {"allowed": True, "outbox": self.item}

        def finish_outbox(self, outbox_id, **kwargs):
            self.finishes.append(kwargs)
            return {"outbox": {**self.item, "delivery_status": kwargs["result"]}, "message": {}}

    store = Store()
    tg = SimpleNamespace(
        _business_connection_id=lambda preferred: ("", "нет подключения"),
        api=lambda method, **kwargs: (calls.append((method, kwargs)) or {"message_id": 1}),
    )
    monkeypatch.setitem(sys.modules, "funnel_workspace_store", store)
    monkeypatch.setitem(sys.modules, "tg_agent", tg)

    gateway._process_outbox_item(store.item, worker_id="worker")

    assert calls[0][1]["reply_markup"]["inline_keyboard"][0][0]["text"] == bot.BUTTON_TERMS
    assert store.finishes[0]["result"] == "sent"


def test_operator_confirmation_survives_the_handover(monkeypatch):
    """Клиент нажал «Позвать оператора» — он обязан получить подтверждение.

    Передача человеку меняет режим и версию диалога, и обычный ответ ИИ после этого
    отменяется очередью. На живом прогоне 28.07.2026 клиент из-за этого не получал
    вообще ничего: кнопка выглядела нерабочей.
    """

    class HumanTakenStore(FakeStore):
        def enqueue_outgoing_agent(self, conversation_id, **kwargs):
            if not kwargs.get("service"):
                raise WorkspaceControlError("ИИ больше не управляет этим диалогом.")
            return super().enqueue_outgoing_agent(conversation_id, **kwargs)

    class WorkspaceControlError(Exception):
        pass

    store = HumanTakenStore(agent_replies=3, control_mode="paused")
    store.WorkspaceControlError = WorkspaceControlError
    monkeypatch.setattr(gateway, "_store", lambda: store)
    monkeypatch.setattr(gateway, "_conversation_for_bot_chat", lambda *_a, **_k: 5)
    _tg(monkeypatch)

    gateway.route_captured_update(_callback_update(bot.CB_OPERATOR))

    assert store.queued, "подтверждение клиенту обязано быть отправлено, а не проглочено"
    assert store.queued[0].get("service") is True
    assert "менеджер" in store.queued[0]["text"].lower()
