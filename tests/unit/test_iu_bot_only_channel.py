from __future__ import annotations

# Единственный источник обращений — бот. Личка аккаунта менеджера больше не заводит
# лиды и не попадает в рабочее окно. Плюс новый пункт меню и смайлики на кнопках.

import sys
import time
from types import SimpleNamespace

import pytest

import funnel_telegram_gateway as gateway
import iu_client_bot as bot


class FakeStore:
    def __init__(self):
        self.ingested: list[dict] = []
        self.queued: list[dict] = []
        self.transitions: list[dict] = []

    def ingest_business_message(self, **kwargs):
        self.ingested.append(kwargs)
        return {"conversation": {"id": 5, "state_version": 7}, "message": {"id": 50}}

    def get_conversation(self, conversation_id):
        return {"id": conversation_id, "state_version": 7,
                "control_mode": "ai", "source_key": "telegram_bot"}

    def count_agent_replies(self, conversation_id):
        return 0

    def enqueue_outgoing_agent(self, conversation_id, **kwargs):
        self.queued.append({"conversation_id": conversation_id, **kwargs})
        return {"outbox": {"id": 90}, "message": {"id": 91}}

    def mark_waiting_human(self, conversation_id, **kwargs):
        self.transitions.append({"conversation_id": conversation_id, **kwargs})
        return {"conversation": {"id": conversation_id, "status": "waiting"}}


def _tg(monkeypatch):
    tg = SimpleNamespace(
        is_owner=lambda _sender: False,
        _workers=SimpleNamespace(submit=lambda *_a: None),
        _handle_update_safely=lambda _u: None,
        api=lambda method, **kwargs: {},
        terms_text=lambda: "Условия ИУ: комиссия, сроки, документы.",
        _strip_markup=lambda value: str(value),
        _business_owner_id=lambda _connection: 777,
        handle_business_connection=lambda _payload: None,
    )
    monkeypatch.setitem(sys.modules, "tg_agent", tg)
    return tg


def _business_update(*, from_bot=False):
    return {
        "business_message": {
            "message_id": 11,
            "date": int(time.time()),
            "business_connection_id": "conn-A",
            "chat": {"id": 4242, "type": "private"},
            "from": {"id": 4242, "first_name": "Клиент"},
            "text": "Здравствуйте, пишу менеджеру напрямую",
            **({"sender_business_bot": {"id": 99}} if from_bot else {}),
        }
    }


@pytest.fixture(autouse=True)
def _bot_channel_on(monkeypatch):
    monkeypatch.setenv("IU_CLIENT_BOT_ENABLED", "1")
    monkeypatch.setenv("IU_CLIENT_BOT_AI", "1")


def test_message_to_the_manager_account_no_longer_creates_a_lead(monkeypatch):
    monkeypatch.setenv("FUNNEL_WORKSPACE_BUSINESS_INTAKE", "0")
    store = FakeStore()
    monkeypatch.setattr(gateway, "_store", lambda: store)
    _tg(monkeypatch)

    assert gateway.route_captured_update(_business_update()) == (None, None)
    assert store.ingested == [], "личка менеджера больше не источник обращений"


def test_business_intake_still_works_while_it_is_switched_on(monkeypatch):
    monkeypatch.delenv("FUNNEL_WORKSPACE_BUSINESS_INTAKE", raising=False)
    store = FakeStore()
    monkeypatch.setattr(gateway, "_store", lambda: store)
    _tg(monkeypatch)

    gateway.route_captured_update(_business_update())

    assert store.ingested, "выключать канал должен только явный флаг — это путь отката"


def test_our_own_echo_is_still_reconciled_when_intake_is_off(monkeypatch):
    """Ответ, отправленный до отключения, обязан закрыться своим эхом.

    Эхо бизнес-бота — не новое обращение, а подтверждение уже сделанной отправки:
    без него сообщение навсегда осталось бы «в пути».
    """

    monkeypatch.setenv("FUNNEL_WORKSPACE_BUSINESS_INTAKE", "0")
    store = FakeStore()
    monkeypatch.setattr(gateway, "_store", lambda: store)
    _tg(monkeypatch)

    gateway.route_captured_update(_business_update(from_bot=True))

    assert store.ingested, "эхо собственной отправки продолжает обрабатываться"


def test_menu_has_the_calculator_and_emoji_on_every_item():
    menu = bot.main_menu()
    titles = [button["text"] for row in menu["keyboard"] for button in row]

    assert titles == [bot.BUTTON_TERMS, bot.BUTTON_JOIN, bot.BUTTON_CALCULATOR, bot.BUTTON_ASK]
    for title in titles:
        assert title[0] not in "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ", f"«{title}» без смайлика"
    assert bot.BUTTON_OPERATOR[0] not in "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ"


def test_calculator_answers_with_a_stub_and_calls_a_human(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(gateway, "_store", lambda: store)
    _tg(monkeypatch)

    gateway.route_captured_update({"message": {
        "message_id": 3, "date": int(time.time()),
        "chat": {"id": 555, "type": "private"},
        "from": {"id": 555, "first_name": "Пётр"},
        "text": bot.BUTTON_CALCULATOR,
    }})

    assert store.ingested[0]["schedule_ai"] is False
    text = store.queued[0]["text"].lower()
    assert "расч" in text
    assert store.transitions, "клиент, которому нужен расчёт, ждёт человека"


def test_old_menu_titles_are_still_recognised():
    """У клиентов, начавших разговор раньше, меню закреплено без смайликов.

    Нажатие такой кнопки обязано остаться выбором пункта, а не превратиться в вопрос к ИИ.
    """

    assert bot.menu_action("Условия присоединения к ИУ") == bot.CB_TERMS
    assert bot.menu_action("Присоединиться к ИУ") == bot.CB_JOIN
    assert bot.menu_action("Задать вопрос") == bot.CB_ASK
    assert bot.menu_action("Позвать оператора") == bot.CB_OPERATOR
    # И новые подписи со смайликами тоже.
    assert bot.menu_action(bot.BUTTON_TERMS) == bot.CB_TERMS
    assert bot.menu_action(bot.BUTTON_CALCULATOR) == bot.CB_CALCULATOR
    # Обычный текст пунктом меню не считается.
    assert bot.menu_action("Какая у вас комиссия?") == ""
