from __future__ import annotations

# Клиентский вход в воронку ИУ: /start, три кнопки, ответы ИИ и вызов оператора.

import sys
from types import SimpleNamespace

import pytest

import funnel_telegram_gateway as gateway
import iu_filters
import iu_turn
import tg_agent as real_tg
import iu_client_bot as bot


class FakeStore:
    """Журнал обращений в объёме, который нужен сценарию бота."""

    def __init__(
        self,
        *,
        agent_replies=0,
        control_mode="ai",
        messages=None,
        deal_id=284,
        metadata=None,
        assigned_to=None,
    ):
        self.ingested: list[dict] = []
        self.queued: list[dict] = []
        self.transitions: list[dict] = []
        self.agent_replies = agent_replies
        self.control_mode = control_mode
        self.messages = list(messages or [])
        self.deal_id = deal_id
        self.metadata = dict(metadata or {})
        self.assigned_to = assigned_to

    def ingest_business_message(self, **kwargs):
        self.ingested.append(kwargs)
        return {"conversation": {"id": 5, "state_version": 7}, "message": {"id": 50}}

    def get_conversation(self, conversation_id):
        return {
            "id": conversation_id,
            "state_version": 7,
            "control_mode": self.control_mode,
            "source_key": "telegram_bot",
            "display_name": "Пётр Иванов",
            "deal_id": self.deal_id,
            "metadata": self.metadata,
            "assigned_to": self.assigned_to,
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

    def transition_control(self, conversation_id, **kwargs):
        self.transitions.append(
            {"kind": "control", "conversation_id": conversation_id, **kwargs}
        )
        self.control_mode = kwargs["mode"]
        return {
            **self.get_conversation(conversation_id),
            "state_version": 8,
        }


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
    # Existing scenario assertions describe the daytime path. Night behaviour
    # has its own explicit tests and must not depend on the wall clock of CI.
    monkeypatch.setattr(gateway, "_manager_notifications_open", lambda: True)


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


def test_closed_manager_question_restores_the_main_menu(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(gateway, "_store", lambda: store)

    gateway.restore_client_menu_after_closed_question(5, state_version=9)

    queued = store.queued[0]
    assert queued["text"] == bot.MENU_PROMPT
    assert queued["metadata"]["iu_event"] == "manager_question_closed"
    keyboard = queued["metadata"]["reply_markup"]["keyboard"]
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


def test_free_text_outside_support_always_gets_the_question_hint(monkeypatch):
    store = FakeStore(control_mode="ai", messages=[])
    monkeypatch.setattr(gateway, "_store", lambda: store)
    monkeypatch.setattr(gateway, "_conversation_for_bot_chat", lambda *_a, **_k: 5)
    _tg(monkeypatch)

    gateway.route_captured_update(_message_update("Подскажите, пожалуйста"))

    assert store.ingested[0]["schedule_ai"] is False
    assert len(store.queued) == 1
    assert store.queued[0]["text"] == bot.STRICT_QUESTION_HINT
    assert store.queued[0]["metadata"]["iu_event"] == "strict_question_hint"
    assert store.transitions == []


def test_free_text_does_not_wake_bot_after_a_real_manager_took_over(monkeypatch):
    store = FakeStore(control_mode="human", messages=[])
    monkeypatch.setattr(gateway, "_store", lambda: store)
    monkeypatch.setattr(gateway, "_conversation_for_bot_chat", lambda *_a, **_k: 5)
    _tg(monkeypatch)

    gateway.route_captured_update(_message_update("Менеджер, вы здесь?"))

    assert store.ingested[0]["schedule_ai"] is False
    assert store.queued == []


def test_file_handover_notifies_without_claiming_an_explicit_manager_call(monkeypatch):
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
    assert store.queued[0]["metadata"]["manager_notification_recipient"] == "chat2714"
    assert store.queued[0]["metadata"]["manager_notification_bot_id"] == 86
    assert store.queued[0]["metadata"]["manager_notification_client_name"] == "Пётр Иванов"
    assert store.queued[0]["metadata"]["manager_notification_kind"] == "manager_needed"
    assert store.transitions[0]["manager_requested"] is False
    assert store.transitions[0]["permanent_human"] is True


def test_calculator_message_uses_join_form_even_while_dialog_is_human(monkeypatch):
    store = FakeStore(control_mode="human")
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
    assert store.queued[0]["metadata"]["iu_event"] == "join_unfilled"
    assert store.transitions == []


def test_calculator_message_with_filled_form_shows_join_choices_without_handover(
    monkeypatch,
):
    store = FakeStore(control_mode="human")
    monkeypatch.setattr(gateway, "_store", lambda: store)
    monkeypatch.setattr(gateway, "_conversation_for_bot_chat", lambda *_a, **_k: 5)
    monkeypatch.setattr(gateway, "_join_body", lambda *_a, **_k: ("filled", True))
    _tg(monkeypatch)

    gateway.route_captured_update(_message_update(bot.CALCULATOR_DISCUSSION_TEXT))

    assert store.queued[0]["text"] == "filled"
    assert store.queued[0]["metadata"]["iu_event"] == "join_filled"
    assert store.queued[0]["metadata"]["join_filled_choice_pending"] is True
    assert store.queued[0]["metadata"]["reply_markup"] == bot.join_filled_menu()
    assert "notify_manager_after_delivery" not in store.queued[0]["metadata"]
    assert store.transitions == []


def _pending_form_message(*, calculator_origin=False):
    return {
        "id": 40,
        "author_type": "agent",
        "direction": "outbound",
        "delivery_status": "sent",
        "text": bot.FORM_RECEIVED,
        "metadata": {
            "iu_event": (
                "calculator_form_received" if calculator_origin else "form_received"
            ),
            "form_questions_pending": True,
            "form_deal_id": 264,
            "manager_notification_form_deal_id": 284,
            **({"calculator_origin": True} if calculator_origin else {}),
        },
    }


def test_form_yes_opens_menu_without_notifying_manager(monkeypatch):
    store = FakeStore(messages=[_pending_form_message()])
    monkeypatch.setattr(gateway, "_store", lambda: store)
    monkeypatch.setattr(gateway, "_conversation_for_bot_chat", lambda *_a, **_k: 5)
    _tg(monkeypatch)

    gateway.route_captured_update(_message_update(bot.BUTTON_FORM_QUESTIONS_YES))

    assert store.ingested[0]["schedule_ai"] is False
    assert store.queued[0]["text"] == bot.FORM_QUESTIONS_HINT
    assert store.queued[0]["metadata"]["iu_event"] == "form_questions_yes"
    assert "notify_manager_after_delivery" not in store.queued[0]["metadata"]
    assert store.queued[0]["metadata"]["reply_markup"] == bot.main_menu()
    assert store.transitions == []


def test_form_no_notifies_manager_and_hands_over(monkeypatch):
    store = FakeStore(messages=[_pending_form_message(calculator_origin=True)])
    monkeypatch.setattr(gateway, "_store", lambda: store)
    monkeypatch.setattr(gateway, "_conversation_for_bot_chat", lambda *_a, **_k: 5)
    _tg(monkeypatch)

    gateway.route_captured_update(_message_update(bot.BUTTON_FORM_QUESTIONS_NO))

    queued = store.queued[0]
    assert store.ingested[0]["schedule_ai"] is False
    assert queued["text"] == bot.FORM_MANAGER_READY
    assert queued["metadata"]["iu_event"] == "form_questions_no"
    assert queued["metadata"]["notify_manager_after_delivery"] is True
    assert queued["metadata"]["manager_notification_kind"] == "form_completed"
    assert queued["metadata"]["manager_notification_form_deal_id"] == 284
    assert queued["metadata"]["calculator_origin"] is True
    assert queued["metadata"]["reply_markup"] == bot.remove_keyboard()
    assert store.transitions[0]["manager_requested"] is True
    assert store.transitions[0]["permanent_human"] is True


def test_repeat_join_with_filled_form_always_shows_choices_without_notification(
    monkeypatch,
):
    store = FakeStore(deal_id=284)
    monkeypatch.setattr(gateway, "_store", lambda: store)
    monkeypatch.setattr(gateway, "_conversation_for_bot_chat", lambda *_a, **_k: 5)
    monkeypatch.setattr(gateway, "_join_body", lambda *_a, **_k: ("filled", True))
    _tg(monkeypatch)

    gateway.run_menu_action(
        bot.CB_JOIN,
        conversation_id=5,
        idempotency_key="join-filled:first",
    )
    gateway.run_menu_action(
        bot.CB_JOIN,
        conversation_id=5,
        idempotency_key="join-filled:again",
    )

    assert len(store.queued) == 2
    for queued in store.queued:
        assert queued["text"] == "filled"
        assert queued["metadata"]["iu_event"] == "join_filled"
        assert "notify_manager_after_delivery" not in queued["metadata"]
        assert queued["metadata"]["manager_notification_form_deal_id"] == 284
        assert queued["metadata"]["join_filled_choice_pending"] is True
        assert queued["metadata"]["repeat_join"] is True
        assert queued["metadata"]["reply_markup"] == bot.join_filled_menu()
    assert store.transitions == []


def _pending_filled_join_message():
    return {
        "id": 50,
        "author_type": "agent",
        "direction": "outbound",
        "delivery_status": "sent",
        "text": "filled",
        "metadata": {
            "iu_event": "join_filled",
            "join_filled_choice_pending": True,
            "manager_notification_form_deal_id": 284,
        },
    }


def test_filled_join_manager_choice_notifies_and_hands_over(monkeypatch):
    store = FakeStore(messages=[_pending_filled_join_message()], deal_id=284)
    monkeypatch.setattr(gateway, "_store", lambda: store)
    monkeypatch.setattr(gateway, "_conversation_for_bot_chat", lambda *_a, **_k: 5)
    _tg(monkeypatch)

    gateway.route_captured_update(_message_update(bot.BUTTON_JOIN_MANAGER))

    queued = store.queued[0]
    assert queued["text"] == bot.JOIN_MANAGER_CALLED
    assert queued["metadata"]["iu_event"] == "join_filled_manager"
    assert queued["metadata"]["notify_manager_after_delivery"] is True
    assert queued["metadata"]["manager_notification_kind"] == "client_called"
    assert queued["metadata"]["manager_notification_form_deal_id"] == 284
    assert queued["metadata"]["reply_markup"] == bot.remove_keyboard()
    assert store.transitions[0]["manager_requested"] is True
    assert store.transitions[0]["permanent_human"] is True


def test_filled_join_menu_choice_opens_menu_without_notification(monkeypatch):
    store = FakeStore(
        messages=[_pending_filled_join_message()],
        deal_id=284,
        control_mode="human",
    )
    monkeypatch.setattr(gateway, "_store", lambda: store)
    monkeypatch.setattr(gateway, "_conversation_for_bot_chat", lambda *_a, **_k: 5)
    _tg(monkeypatch)

    gateway.route_captured_update(_message_update(bot.BUTTON_JOIN_MENU))

    queued = store.queued[0]
    assert queued["text"] == bot.MENU_PROMPT
    assert queued["metadata"]["iu_event"] == "join_filled_menu"
    assert "notify_manager_after_delivery" not in queued["metadata"]
    assert queued["metadata"]["reply_markup"] == bot.main_menu()
    assert store.transitions[0]["kind"] == "control"
    assert store.transitions[0]["mode"] == "ai"
    assert "главное меню" in store.transitions[0]["reason"]


def _legacy_repeat_join_metadata():
    return {
        "manager_requested_at": "2026-07-30T18:39:56+00:00",
        "manager_request_reason": gateway.LEGACY_REPEAT_JOIN_HANDOFF_REASON,
        "manager_request_handled_at": "2026-07-30T18:38:48+00:00",
    }


def _legacy_calculator_metadata():
    return {
        "manager_requested_at": "2026-07-30T19:55:38+00:00",
        "manager_request_reason": gateway.LEGACY_CALCULATOR_HANDOFF_REASON,
        "manager_request_handled_at": "2026-07-30T19:21:27+00:00",
    }


def test_legacy_hold_detector_does_not_override_a_handled_or_assigned_dialog():
    base = {
        "source_key": "telegram_bot",
        "control_mode": "human",
        "assigned_to": None,
        "metadata": _legacy_repeat_join_metadata(),
    }
    assert gateway._legacy_repeat_join_hold(base) is True

    handled = {
        **base,
        "metadata": {
            **_legacy_repeat_join_metadata(),
            "manager_request_handled_at": "2026-07-30T18:40:00+00:00",
        },
    }
    assigned = {**base, "assigned_to": "Александр"}
    assert gateway._legacy_repeat_join_hold(handled) is False
    assert gateway._legacy_repeat_join_hold(assigned) is False


def test_calculator_cta_recovers_its_old_unassigned_hold_after_join_reply(
    monkeypatch,
):
    store = FakeStore(
        control_mode="human",
        metadata=_legacy_calculator_metadata(),
    )
    monkeypatch.setattr(gateway, "_store", lambda: store)
    monkeypatch.setattr(gateway, "_conversation_for_bot_chat", lambda *_a, **_k: 5)
    monkeypatch.setattr(gateway, "_join_body", lambda *_a, **_k: ("filled", True))
    _tg(monkeypatch)

    gateway.route_captured_update(_message_update(bot.CALCULATOR_DISCUSSION_TEXT))

    assert store.queued[0]["text"] == "filled"
    assert store.queued[0]["metadata"]["iu_event"] == "join_filled"
    assert store.queued[0]["metadata"]["reply_markup"] == bot.join_filled_menu()
    assert store.transitions[0]["kind"] == "control"
    assert store.transitions[0]["mode"] == "ai"
    assert "калькулятора" in store.transitions[0]["reason"]


def test_legacy_repeat_join_hold_gets_hint_and_returns_to_ai(monkeypatch):
    store = FakeStore(
        control_mode="human",
        metadata=_legacy_repeat_join_metadata(),
    )
    monkeypatch.setattr(gateway, "_store", lambda: store)
    monkeypatch.setattr(gateway, "_conversation_for_bot_chat", lambda *_a, **_k: 5)
    _tg(monkeypatch)

    gateway.route_captured_update(_message_update("Что происходит"))

    assert store.ingested[0]["schedule_ai"] is False
    assert store.queued[0]["text"] == bot.STRICT_QUESTION_HINT
    assert store.queued[0]["metadata"]["iu_event"] == "strict_question_hint"
    assert store.transitions[0]["kind"] == "control"
    assert store.transitions[0]["mode"] == "ai"


def test_repeated_after_hours_message_falls_back_to_hint_instead_of_silence(
    monkeypatch,
):
    import iu_manager_response_watch as watch

    class DuplicateNightStore(FakeStore):
        def enqueue_outgoing_agent(self, conversation_id, **kwargs):
            if str(kwargs.get("idempotency_key") or "").startswith("night:"):
                return {
                    "duplicate": True,
                    "conversation": self.get_conversation(conversation_id),
                    "message": {"id": 10},
                    "outbox": {"id": 11},
                }
            return super().enqueue_outgoing_agent(conversation_id, **kwargs)

    store = DuplicateNightStore(
        control_mode="human",
        metadata=_legacy_repeat_join_metadata(),
    )
    monkeypatch.setattr(gateway, "_store", lambda: store)
    monkeypatch.setattr(gateway, "_conversation_for_bot_chat", lambda *_a, **_k: 5)
    monkeypatch.setattr(gateway, "_manager_notifications_open", lambda: False)
    monkeypatch.setattr(
        watch,
        "after_hours_period_key",
        lambda conversation_id: f"night:{conversation_id}:2026-07-30",
    )
    monkeypatch.setattr(gateway, "_cancel_bot_reminders", lambda *_a, **_k: None)
    _tg(monkeypatch)

    gateway.route_captured_update(_message_update("Что происходит"))

    assert len(store.queued) == 1
    assert store.queued[0]["text"] == bot.STRICT_QUESTION_HINT
    assert store.queued[0]["metadata"]["after_hours_fallback"] is True
    assert store.transitions[0]["kind"] == "control"
    assert store.transitions[0]["mode"] == "ai"
    assert all("permanent_human" not in item for item in store.transitions)


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
    assert prepared.metadata["manager_notification_recipient"] == "chat2714"
    assert prepared.metadata["manager_notification_bot_id"] == 86
    assert prepared.metadata["manager_notification_kind"] == "manager_needed"


def test_vague_help_request_neither_calls_nor_notifies_manager(monkeypatch):
    monkeypatch.setattr(gateway, "_store", lambda: FakeStore())
    outcome = iu_turn.handle(
        iu_turn.Request(message="Здравствуйте! Помогите мне"),
        iu_turn.Deps(
            ask=lambda _prompt: (_ for _ in ()).throw(
                AssertionError("vague help must not call the model")
            ),
            rules=iu_filters.Ruleset(),
        ),
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

    assert prepared.escalate is False
    assert "notify_manager_after_delivery" not in prepared.metadata
    assert "manager_notification_kind" not in prepared.metadata


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


def test_operator_button_is_allowed_after_two_ai_answers():
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
    assert store.transitions[0]["permanent_human"] is True
    assert "менеджер" in store.queued[0]["text"].lower()
    assert store.queued[0]["metadata"]["notify_manager_after_delivery"] is True
    assert store.queued[0]["metadata"]["manager_notification_recipient"] == "chat2714"
    assert store.queued[0]["metadata"]["manager_notification_bot_id"] == 86
    assert store.queued[0]["metadata"]["manager_notification_kind"] == "client_called"


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


def test_first_ai_reply_has_only_exit_before_the_threshold(monkeypatch):
    import iu_contract

    store = FakeStore(
        agent_replies=0,
        messages=[
            {"id": 1, "author_type": "client", "text": bot.BUTTON_ASK},
            {"id": 2, "author_type": "client", "text": "Первый вопрос"},
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
