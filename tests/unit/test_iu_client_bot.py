from __future__ import annotations

# Клиентский вход в воронку ИУ: /start, три кнопки, ответы ИИ и вызов оператора.

import sys
from types import SimpleNamespace

import pytest

import funnel_telegram_gateway as gateway
import tg_agent as real_tg
import iu_client_bot as bot


class FakeStore:
    """Журнал обращений в объёме, который нужен сценарию бота."""

    def __init__(self, *, agent_replies=0, control_mode="ai", messages=None):
        self.ingested: list[dict] = []
        self.queued: list[dict] = []
        self.transitions: list[dict] = []
        self.agent_replies = agent_replies
        self.control_mode = control_mode
        self.messages = list(messages or [])

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

    def list_messages(self, conversation_id, **kwargs):
        return self.messages

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
        _strip_markup=lambda value, **_kw: str(value),
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


def _message_update(text: str = "", *, document: dict | None = None, caption: str = ""):
    message = {
        "message_id": 3,
        "date": 1785600200,
        "chat": {"id": 555, "type": "private"},
        "from": {"id": 555, "first_name": "Пётр", "username": "petr"},
    }
    if text:
        message["text"] = text
    if document:
        message["document"] = document
    if caption:
        message["caption"] = caption
    return {"message": message}


@pytest.fixture(autouse=True)
def _channel_on(monkeypatch):
    monkeypatch.setenv("IU_CLIENT_BOT_ENABLED", "1")


def test_start_creates_a_lead_and_shows_the_menu(monkeypatch):
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
    keyboard = reply["metadata"]["reply_markup"]["keyboard"]
    assert [row[0]["text"] for row in keyboard] == [
        bot.BUTTON_TERMS,
        bot.BUTTON_JOIN,
        bot.BUTTON_CALCULATOR,
        bot.BUTTON_ASK,
    ]


def test_terms_button_sends_the_real_terms(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(gateway, "_store", lambda: store)
    monkeypatch.setattr(gateway, "_conversation_for_bot_chat", lambda *_a, **_k: 5)
    _tg(monkeypatch)
    import iu_bot_documents
    monkeypatch.setattr(
        iu_bot_documents,
        "attachment",
        lambda kind: {
            "token": f"token-{kind}-abcdefghijkl",
            "file_name": f"{kind}.pdf",
            "mime_type": "application/pdf",
            "file_size": 100,
        },
    )

    gateway.route_captured_update(_callback_update(bot.CB_TERMS))

    # Нажатие видно команде как реплика клиента, иначе лента обрывается.
    assert store.ingested[0]["text"] == bot.BUTTON_TERMS
    assert [item["file_name"] for item in store.queued[0]["attachments"]] == [
        "terms.pdf",
        "contract.pdf",
    ]
    assert "калькуляторе ИУ" in store.queued[0]["text"]
    assert len(store.queued) == 1


def test_support_entry_sends_faq_and_prompt_as_one_message(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(gateway, "_store", lambda: store)
    monkeypatch.setattr(gateway, "_conversation_for_bot_chat", lambda *_a, **_k: 5)
    _tg(monkeypatch)
    import iu_bot_documents

    monkeypatch.setattr(
        iu_bot_documents,
        "attachment",
        lambda kind: {
            "token": f"token-{kind}-abcdefghijkl",
            "file_name": f"{kind}.pdf",
            "mime_type": "application/pdf",
            "file_size": 100,
        },
    )
    monkeypatch.setitem(
        sys.modules,
        "iu_bot_reminders",
        SimpleNamespace(
            cancel_all=lambda *_a, **_k: None,
            schedule_waiting_question=lambda *_a, **_k: None,
        ),
    )

    gateway.route_captured_update(_callback_update(bot.CB_ASK))

    assert len(store.queued) == 1
    assert store.queued[0]["text"] == bot.ASK_PROMPT
    assert store.queued[0]["attachment"]["file_name"] == "faq.pdf"
    assert store.queued[0]["metadata"]["iu_event"] == "support_enter"
    assert "keyboard" in store.queued[0]["metadata"]["reply_markup"]


def test_file_handover_always_sets_badge_and_bitrix_notification(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(gateway, "_store", lambda: store)
    monkeypatch.setattr(gateway, "_conversation_for_bot_chat", lambda *_a, **_k: 5)
    _tg(monkeypatch)

    gateway.route_captured_update(
        _message_update(
            document={
                "file_id": "document-id",
                "file_unique_id": "document-unique",
                "file_name": "договор.pdf",
                "mime_type": "application/pdf",
            },
            caption="Проверьте, пожалуйста, пункт про комиссию.",
        )
    )

    assert store.queued[0]["text"] == bot.FILE_SENT_TO_MANAGER
    assert store.queued[0]["metadata"]["notify_manager_after_delivery"] is True
    assert store.queued[0]["metadata"]["manager_notification_recipient"] == "16"
    assert store.queued[0]["metadata"]["manager_notification_bot_id"] == 86
    assert store.transitions[0]["manager_requested"] is True


def test_calculator_message_requests_form_without_starting_ai(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(gateway, "_store", lambda: store)
    monkeypatch.setattr(gateway, "_conversation_for_bot_chat", lambda *_a, **_k: 5)
    monkeypatch.setattr(
        gateway,
        "_join_body",
        lambda *_a, **_k: (
            bot.join_reply("https://www.m4s.ru/iu/personal"),
            False,
        ),
    )
    _tg(monkeypatch)

    gateway.route_captured_update(_message_update(bot.CALCULATOR_DISCUSSION_TEXT))

    assert store.ingested[0]["schedule_ai"] is False
    assert "https://www.m4s.ru/iu/personal" in store.queued[0]["text"]
    assert store.queued[0]["metadata"]["iu_event"] == "calculator_discussion_unfilled"
    assert store.transitions == []


def test_calculator_message_with_filled_form_calls_manager(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(gateway, "_store", lambda: store)
    monkeypatch.setattr(gateway, "_conversation_for_bot_chat", lambda *_a, **_k: 5)
    monkeypatch.setattr(gateway, "_join_body", lambda *_a, **_k: ("filled", True))
    _tg(monkeypatch)

    gateway.route_captured_update(_message_update(bot.CALCULATOR_DISCUSSION_TEXT))

    assert store.queued[0]["text"] == bot.CALCULATOR_MANAGER_READY
    assert store.queued[0]["metadata"]["notify_manager_after_delivery"] is True
    assert store.queued[0]["metadata"]["iu_event"] == "calculator_discussion_filled"
    assert store.transitions[0]["manager_requested"] is True


def test_any_ai_escalation_enqueues_the_same_bitrix_manager_alert(monkeypatch):
    import iu_contract

    store = FakeStore()
    monkeypatch.setattr(gateway, "_store", lambda: store)
    _tg(monkeypatch)
    outcome = SimpleNamespace(
        reply="Передал вопрос менеджеру.",
        action=iu_contract.REPLY_ONLY,
        escalate=True,
        reason="Нужна ручная проверка.",
        stage_move="",
        answered_client=True,
        sources=(),
        trace={},
    )

    prepared = gateway.prepare_reply(
        outcome,
        telegram_user_id=555,
        conversation={
            "id": 5,
            "source_key": gateway.BOT_SOURCE_KEY,
            "control_mode": "ai",
        },
    )

    assert prepared.metadata["notify_manager_after_delivery"] is True
    assert prepared.metadata["manager_notification_recipient"] == "16"
    assert prepared.metadata["manager_notification_bot_id"] == 86


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


def test_calculator_button_sends_the_public_url_without_handover(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(gateway, "_store", lambda: store)
    monkeypatch.setattr(gateway, "_conversation_for_bot_chat", lambda *_a, **_k: 5)
    _tg(monkeypatch)

    gateway.route_captured_update(_callback_update(bot.CB_CALCULATOR))

    assert bot.CALCULATOR_URL in store.queued[0]["text"]
    assert store.transitions == []


def test_operator_button_appears_when_the_client_asks_a_third_question():
    assert bot.should_offer_operator(0) is False
    assert bot.should_offer_operator(1) is False
    assert bot.should_offer_operator(2) is True
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
    assert store.transitions[0]["manager_requested"] is True
    assert "менеджер" in store.queued[0]["text"].lower()
    assert store.queued[0]["metadata"]["notify_manager_after_delivery"] is True
    assert store.queued[0]["metadata"]["manager_notification_recipient"] == "16"
    assert store.queued[0]["metadata"]["manager_notification_bot_id"] == 86


def test_third_ai_reply_carries_support_exit_and_operator(monkeypatch):
    import iu_contract

    messages = [
        {"id": 1, "author_type": "client", "text": bot.BUTTON_ASK},
        {"id": 2, "author_type": "agent", "direction": "outbound",
         "delivery_status": "sent", "text": "Ответ 1", "metadata": {}},
        {"id": 3, "author_type": "agent", "direction": "outbound",
         "delivery_status": "sent", "text": "Ответ 2", "metadata": {}},
        {"id": 4, "author_type": "client", "text": "Третий вопрос"},
    ]
    store = FakeStore(agent_replies=2, messages=messages)
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

    keyboard = prepared.metadata.get("reply_markup", {}).get("keyboard")
    assert keyboard, "клавиатура поддержки приходит вместе с ответом ИИ"
    titles = [button["text"] for row in keyboard for button in row]
    assert bot.BUTTON_OPERATOR in titles
    assert bot.BUTTON_EXIT_SUPPORT in titles
    assert bot.BUTTON_TERMS not in titles


def test_ai_reply_has_only_exit_before_the_threshold(monkeypatch):
    import iu_contract

    store = FakeStore(
        agent_replies=1,
        messages=[
            {"id": 1, "author_type": "client", "text": bot.BUTTON_ASK},
            {"id": 2, "author_type": "agent", "direction": "outbound",
             "delivery_status": "sent", "text": "Ответ 1", "metadata": {}},
            {"id": 3, "author_type": "client", "text": "Второй вопрос"},
        ],
    )
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

    titles = [
        button["text"]
        for row in prepared.metadata["reply_markup"]["keyboard"]
        for button in row
    ]
    assert titles == [bot.BUTTON_EXIT_SUPPORT]


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
                "payload": {"reply_markup": bot.main_menu()},
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
        telegram_html=real_tg.telegram_html,
    )
    monkeypatch.setitem(sys.modules, "funnel_workspace_store", store)
    monkeypatch.setitem(sys.modules, "tg_agent", tg)

    gateway._process_outbox_item(store.item, worker_id="worker")

    assert calls[0][1]["reply_markup"]["keyboard"][0][0]["text"] == bot.BUTTON_TERMS
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


def test_handover_does_not_cancel_the_service_confirmation():
    """Передача человеку снимает недоставленные ответы ИИ — но не подтверждение кнопки."""

    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "funnel_workspace_store.py").read_text(
        encoding="utf-8")
    cancel_block = source.split("def _cancel_queued_ai", 1)[1].split("def ", 1)[0]
    # Обе отмены (ожидающие и уже взятые в работу) обязаны обходить служебные ответы.
    assert cancel_block.count("COALESCE(payload->>'service_reply', 'false') <> 'true'") == 2


def test_service_reply_survives_every_gate_on_the_way_to_telegram():
    """Барьеров на пути ответа четыре, и пропуск любого гасит подтверждение.

    Найдено живым прогоном 28.07.2026: сначала подтверждение отменяла передача диалога
    человеку, потом — фоновая чистка «устаревших» ответов ИИ, и только после этого стало
    видно, что очередь и граница вызова Telegram проверяют условие каждая по-своему.
    """

    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "funnel_workspace_store.py").read_text(
        encoding="utf-8")
    # Пропуск: выборка в работу и граница вызова Telegram.
    assert source.count("COALESCE(o.payload->>'service_reply', 'false') = 'true'") == 2
    # Отмена: снятие при передаче человеку (два запроса) и чистка устаревших.
    assert source.count("COALESCE(payload->>'service_reply', 'false') <> 'true'") == 2
    assert source.count("COALESCE(o.payload->>'service_reply', 'false') <> 'true'") == 1


def test_lead_is_created_for_a_client_who_came_through_the_bot():
    """Клиент из бота — такой же лид, как написавший менеджеру.

    Живой прогон 28.07.2026: сделка не заводилась вовсе — адаптер CRM отвергал новый
    источник, и обращение оставалось без лида в Битриксе.
    """

    import funnel_workspace_crm as crm

    # Диалог из бота проходит проверку источника наравне с перепиской менеджера.
    crm._validate_conversation({"id": 5, "source_key": "telegram_bot", "external_user_id": 555}, 5)
    crm._validate_conversation({"id": 5, "source_key": "telegram", "external_user_id": 555}, 5)

    with pytest.raises(crm.WorkspaceCrmError):
        crm._validate_conversation({"id": 5, "source_key": "whatsapp", "external_user_id": 555}, 5)


def test_ai_job_for_a_bot_dialog_is_not_cancelled_by_the_business_rollout(monkeypatch):
    """Пустой список тестовых ID бизнес-контура не должен глушить ответы бота.

    Живой прогон 28.07.2026: клиент задал вопрос в боте и не получил ничего — задание
    ИИ отменялось проверкой, которая относится к другому каналу.
    """

    monkeypatch.setenv("FUNNEL_WORKSPACE_AI_ALLOW_IDS", "")  # бизнес-rollout закрыт
    monkeypatch.setenv("FUNNEL_WORKSPACE_AI_ENABLED", "1")
    monkeypatch.setenv("IU_CLIENT_BOT_ENABLED", "1")
    monkeypatch.setenv("IU_CLIENT_BOT_AI", "1")

    bot_dialog = {"source_key": "telegram_bot", "external_user_id": 555}
    business_dialog = {"source_key": "telegram", "external_user_id": 555}

    assert gateway.ai_allowed_in_channel(bot_dialog, 555) is True
    assert gateway.ai_allowed_in_channel(business_dialog, 555) is False

    # Выключенный рубильник бота молчит так же явно.
    monkeypatch.setenv("IU_CLIENT_BOT_AI", "0")
    assert gateway.ai_allowed_in_channel(bot_dialog, 555) is False


def test_ai_worker_runs_for_the_bot_channel_alone(monkeypatch):
    """Обработчик заданий ИИ обязан работать, даже когда бизнес-контур выключен.

    Живой прогон 28.07.2026: задание клиента висело «ожидает» до бесконечности —
    воркер сверялся с общим рубильником, который относится к другому каналу.
    """

    monkeypatch.setenv("FUNNEL_WORKSPACE_AI_ENABLED", "0")
    monkeypatch.setenv("IU_CLIENT_BOT_ENABLED", "1")
    monkeypatch.setenv("IU_CLIENT_BOT_AI", "1")
    assert gateway.ai_worker_needed() is True

    monkeypatch.setenv("IU_CLIENT_BOT_AI", "0")
    assert gateway.ai_worker_needed() is False

    monkeypatch.setenv("FUNNEL_WORKSPACE_AI_ENABLED", "1")
    assert gateway.ai_worker_needed() is True
