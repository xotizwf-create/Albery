from __future__ import annotations

# Бот ИУ как бот-менеджер: постоянное меню под полем ввода, честный порядок ленты
# и один и тот же сценарий для всех, включая владельца.

import sys
import time
from types import SimpleNamespace

import pytest

import funnel_telegram_gateway as gateway
import iu_client_bot as bot


class FakeStore:
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
        return {"id": conversation_id, "state_version": 7,
                "control_mode": self.control_mode, "source_key": "telegram_bot"}

    def count_agent_replies(self, conversation_id):
        return self.agent_replies

    def enqueue_outgoing_agent(self, conversation_id, **kwargs):
        self.queued.append({"conversation_id": conversation_id, **kwargs})
        return {"outbox": {"id": 90}, "message": {"id": 91}}

    def mark_waiting_human(self, conversation_id, **kwargs):
        self.transitions.append({"conversation_id": conversation_id, **kwargs})
        return {"conversation": {"id": conversation_id, "status": "waiting"}}


def _tg(monkeypatch, *, owner=False):
    tg = SimpleNamespace(
        is_owner=lambda _sender: owner,
        _workers=SimpleNamespace(submit=lambda *_a: pytest.fail(
            "личный ассистент в этом боте больше не работает")),
        _handle_update_safely=lambda _u: None,
        api=lambda method, **kwargs: {},
        terms_text=lambda: "Условия ИУ: комиссия, сроки, документы.",
        _strip_markup=lambda value, **_kw: str(value),
    )
    monkeypatch.setitem(sys.modules, "tg_agent", tg)
    return tg


def _message(text, *, date=None, message_id=7):
    return {
        "message": {
            "message_id": message_id,
            "date": date or int(time.time()),
            "chat": {"id": 555, "type": "private"},
            "from": {"id": 555, "first_name": "Пётр", "username": "petr"},
            "text": text,
        }
    }


@pytest.fixture(autouse=True)
def _channel_on(monkeypatch):
    monkeypatch.setenv("IU_CLIENT_BOT_ENABLED", "1")
    monkeypatch.setenv("IU_CLIENT_BOT_AI", "1")


def test_menu_lives_under_the_input_field_not_inside_a_message():
    menu = bot.main_menu()

    # Reply-клавиатура: она остаётся под полем ввода, а не уезжает вверх с историей.
    assert "keyboard" in menu and "inline_keyboard" not in menu
    assert menu["resize_keyboard"] is True
    assert menu["is_persistent"] is True
    assert [row[0]["text"] if isinstance(row[0], dict) else row[0] for row in menu["keyboard"]] == [
        bot.BUTTON_TERMS,
        bot.BUTTON_JOIN,
        bot.BUTTON_CALCULATOR,
        bot.BUTTON_ASK,
    ]


def test_menu_has_no_operator_item():
    """Владелец убрал пункт 28.07.2026: человека зовут присоединение и сам агент."""

    titles = [button["text"] for row in bot.main_menu()["keyboard"] for button in row]
    with_flag = [button["text"] for row in bot.main_menu(offer_operator=True)["keyboard"]
                 for button in row]

    assert bot.BUTTON_OPERATOR not in titles
    assert with_flag == titles, "старый флаг больше ничего не добавляет"


def test_start_answers_with_the_menu(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(gateway, "_store", lambda: store)
    _tg(monkeypatch)

    gateway.route_captured_update(_message("/start"))

    assert store.queued, "на /start обязан прийти ответ с меню"
    markup = store.queued[0]["metadata"]["reply_markup"]
    assert "keyboard" in markup


def test_menu_item_is_handled_as_an_action_not_as_a_question(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(gateway, "_store", lambda: store)
    _tg(monkeypatch)

    gateway.route_captured_update(_message(bot.BUTTON_TERMS))

    # Пункт меню приходит обычным текстом — и должен выполнить действие, а не уйти в ИИ.
    assert store.ingested[0]["schedule_ai"] is False
    assert "Условия ИУ" in store.queued[0]["text"]


def test_free_question_still_goes_to_the_ai(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(gateway, "_store", lambda: store)
    _tg(monkeypatch)

    gateway.route_captured_update(_message("А какая у вас комиссия?"))

    assert store.ingested[0]["schedule_ai"] is True
    assert store.queued == [], "на свободный вопрос отвечает ИИ, а не сценарий"


def test_owner_gets_the_same_manager_bot_not_a_personal_assistant(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(gateway, "_store", lambda: store)
    # _tg падает, если кто-то попытается позвать личного ассистента.
    _tg(monkeypatch, owner=True)

    gateway.route_captured_update(_message("/start"))

    assert store.ingested and store.ingested[0]["source_key"] == "telegram_bot"
    assert store.queued, "владелец видит то же меню, что и клиент"


def test_press_on_an_old_inline_button_is_journaled_at_the_moment_it_happened(monkeypatch):
    """Порядок в рабочем окне обязан совпадать с реальным.

    Живой диалог 28.07.2026: нажатиям проставлялось время сообщения с кнопками, и в ленте
    они вставали раньше ответов — «/start, условия, вопрос» подряд, а ответы после. Кнопки
    внутри сообщений остались у клиентов, которые начали разговор до перехода на меню.
    """

    store = FakeStore()
    monkeypatch.setattr(gateway, "_store", lambda: store)
    monkeypatch.setattr(gateway, "_conversation_for_bot_chat", lambda *_a, **_k: 5)
    _tg(monkeypatch)

    an_hour_ago = int(time.time()) - 3600
    gateway.route_captured_update({
        "callback_query": {
            "id": "cb-old",
            "data": bot.CB_TERMS,
            "from": {"id": 555, "first_name": "Пётр", "username": "petr"},
            "message": {"message_id": 2, "date": an_hour_ago,
                        "chat": {"id": 555, "type": "private"}},
        }
    })

    occurred = store.ingested[0]["occurred_at"]
    assert occurred.timestamp() > an_hour_ago + 60, (
        "нажатие журналируется временем нажатия, а не временем старого сообщения"
    )


def test_plain_message_keeps_its_own_time(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(gateway, "_store", lambda: store)
    _tg(monkeypatch)

    sent_at = int(time.time()) - 5
    gateway.route_captured_update(_message("Здравствуйте", date=sent_at))

    assert abs(store.ingested[0]["occurred_at"].timestamp() - sent_at) < 2
