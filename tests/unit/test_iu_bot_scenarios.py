from __future__ import annotations

from datetime import datetime

from config import MSK_TZ
import iu_bot_reminders as reminders
import iu_bot_state as state
import iu_client_bot as bot


def client(message_id: int, text: str, metadata=None):
    return {
        "id": message_id,
        "author_type": "client",
        "direction": "inbound",
        "text": text,
        "metadata": metadata or {},
    }


def agent(message_id: int, text: str, *, event="", service=False, metadata=None):
    return {
        "id": message_id,
        "author_type": "agent",
        "direction": "outbound",
        "delivery_status": "sent",
        "text": text,
        "metadata": {
            **({"iu_event": event} if event else {}),
            **({"service_reply": True} if service else {}),
            **(metadata or {}),
        },
    }


def test_support_lifecycle_and_confirmation():
    messages = [client(1, bot.BUTTON_ASK)]
    assert state.support_state(messages).mode == "active"

    messages.append(client(2, bot.BUTTON_EXIT_SUPPORT))
    assert state.support_state(messages).mode == "confirming"

    messages.append(client(3, bot.BUTTON_CONFIRM_NO))
    assert state.support_state(messages).mode == "active"

    messages.extend(
        [client(4, bot.BUTTON_EXIT_SUPPORT), client(5, bot.BUTTON_CONFIRM_YES)]
    )
    assert state.support_state(messages).mode == "inactive"


def test_only_delivered_non_service_ai_answers_open_operator_button():
    messages = [
        client(1, bot.BUTTON_ASK),
        agent(2, bot.ASK_PROMPT, event="support_enter", service=True),
        agent(3, "Первый ответ"),
        agent(4, "Второй ответ"),
        client(5, "Третий вопрос"),
    ]
    assert state.support_agent_replies(messages) == 2
    titles = [
        button["text"]
        for row in bot.support_menu(offer_operator=True)["keyboard"]
        for button in row
    ]
    assert titles == [bot.BUTTON_OPERATOR, bot.BUTTON_EXIT_SUPPORT]


def test_exit_no_keeps_answer_counter_and_pending_question():
    messages = [
        client(1, bot.BUTTON_ASK),
        agent(2, bot.ASK_PROMPT, event="support_enter", service=True),
        client(3, "Первый вопрос"),
        agent(4, "Первый ответ"),
        client(5, "Второй вопрос"),
        agent(6, "Второй ответ"),
        client(7, "Третий вопрос"),
        client(8, bot.BUTTON_EXIT_SUPPORT),
        agent(9, bot.EXIT_CONFIRM, event="support_exit_confirm", service=True),
        client(10, bot.BUTTON_CONFIRM_NO),
        agent(11, bot.CONTINUE_SUPPORT, event="support_continue", service=True),
    ]

    assert state.support_state(messages).mode == "active"
    assert state.support_agent_replies(messages) == 2
    assert state.latest_pending_question(messages) == 7
    assert bot.should_offer_operator(state.support_agent_replies(messages))


def test_strict_hint_is_one_time_and_original_question_is_preserved():
    messages = [
        client(1, "Какая комиссия?"),
        agent(2, bot.STRICT_QUESTION_HINT, event="strict_question_hint", service=True),
        client(3, "Я уже спросил"),
    ]
    assert state.strict_hint_sent(messages) is True
    assert state.latest_pending_question(messages) == 1

    messages.append(client(4, bot.BUTTON_ASK))
    assert state.latest_pending_question(messages) == 1

    messages = [
        client(1, "Старый вопрос"),
        client(2, bot.BUTTON_CALCULATOR),
        client(3, bot.BUTTON_ASK),
    ]
    assert state.latest_pending_question(messages) is None

    messages = [
        client(1, bot.BUTTON_ASK),
        client(2, "Уже отвеченный вопрос"),
        agent(3, "Ответ"),
        client(4, bot.BUTTON_ASK),
    ]
    assert state.latest_pending_question(messages) is None


def test_file_without_caption_is_not_mistaken_for_a_pending_question():
    messages = [
        client(
            1,
            "[Документ: договор.pdf]",
            {"telegram_media_type": "document", "telegram_has_caption": False},
        ),
        client(2, bot.BUTTON_ASK),
    ]
    assert state.latest_pending_question(messages) is None
    messages.insert(
        1,
        agent(
            2,
            bot.FILE_NEEDS_CONTEXT,
            event="file_needs_context",
            service=True,
        ),
    )
    assert state.awaiting_file_context(messages[:-1]) is True
    messages.append(client(4, "Проверьте условия в этом договоре"))
    assert state.awaiting_file_context(messages) is False


def test_quiet_close_reopens_on_the_next_message():
    messages = [
        client(1, bot.BUTTON_ASK),
        agent(2, reminders_text := bot.REMINDER_AFTER_ANSWER,
              event="support_quiet_close", service=True),
    ]
    assert reminders_text
    assert state.support_state(messages).mode == "quiet"
    messages.append(client(3, "Новый вопрос"))
    assert state.support_state(messages).mode == "active"


def test_stop_suppresses_automatic_scenario_until_start_or_menu():
    messages = [client(1, "/stop"), agent(2, bot.STOPPED, event="stop", service=True)]
    assert state.bot_stopped(messages) is True
    messages.extend([client(3, "/menu"), agent(4, bot.MENU_PROMPT, event="menu", service=True)])
    assert state.bot_stopped(messages) is False


def test_join_copy_for_first_repeat_and_filled_states():
    first, filled = bot.join_answer("", "https://example.test/form", repeated=False)
    assert filled is False
    assert first.startswith("Заполните короткую анкету")
    assert "[Заполнить анкету]" in first

    repeated, filled = bot.join_answer("", "https://example.test/form", repeated=True)
    assert filled is False
    assert repeated.startswith("Вижу, анкета ещё не заполнена")

    complete, filled = bot.join_answer(
        "Вижу анкету:\n\n• Категория — Одежда\n\nВсё верно?", ""
    )
    assert filled is True
    assert "• Категория — Одежда" in complete
    assert "Если нужно что-то исправить или обсудить" in complete
    assert "Если всё верно и помощь не требуется" in complete
    assert complete.count("Всё верно?") == 0
    assert "https://" not in complete


def test_reminder_delivery_window_and_stale_cancellation(monkeypatch):
    monkeypatch.setattr(reminders, "STALE_HOURS", 3)
    due = datetime(2026, 7, 29, 10, 0, tzinfo=MSK_TZ)
    assert reminders.delivery_decision(
        datetime(2026, 7, 29, 10, 30, tzinfo=MSK_TZ), due
    ).action == "send"

    before_morning = reminders.delivery_decision(
        datetime(2026, 7, 29, 8, 30, tzinfo=MSK_TZ),
        datetime(2026, 7, 29, 8, 0, tzinfo=MSK_TZ),
    )
    assert before_morning.action == "wait"
    assert before_morning.retry_at.hour == 9

    assert reminders.delivery_decision(
        datetime(2026, 7, 30, 9, 0, tzinfo=MSK_TZ),
        datetime(2026, 7, 29, 21, 0, tzinfo=MSK_TZ),
    ).action == "cancel"


def test_approved_customer_copy_is_kept_exactly():
    assert bot.FORM_RECEIVED == (
        "Анкету получили. Остались ли у Вас вопросы по подключению?"
    )
    assert bot.CALCULATOR_FORM_RECEIVED == bot.FORM_RECEIVED
    assert bot.FORM_QUESTIONS_HINT == (
        'Задать вопрос вы можете нажав на кнопку "Задать вопрос".'
    )
    assert bot.FORM_MANAGER_READY == (
        "Отлично! Сейчас менеджер подключится к диалогу для согласования дальнейших шагов!"
    )
    assert bot.form_questions_menu()["keyboard"] == [
        [
            {"text": "Да"},
            {"text": "Нет"},
        ]
    ]
    assert bot.join_filled_menu()["keyboard"] == [
        [{"text": bot.BUTTON_JOIN_MANAGER}],
        [{"text": bot.BUTTON_JOIN_MENU}],
    ]
    assert bot.EXIT_CONFIRM == "Вы уверены, что хотите выйти из окна поддержки?"
    assert bot.REMINDER_WAITING_QUESTION == (
        "Мы готовы помочь. Напишите ваш вопрос, когда будет удобно."
    )


def test_post_form_no_does_not_reactivate_support_mode():
    messages = [
        agent(
            1,
            bot.FORM_RECEIVED,
            event="form_received",
            metadata={"form_questions_pending": True, "form_deal_id": 264},
        ),
        client(2, bot.BUTTON_FORM_QUESTIONS_NO),
        agent(3, bot.FORM_MANAGER_READY, event="form_questions_no"),
    ]

    assert state.pending_form_questions(messages) == {}
    assert state.support_state(messages).mode == "inactive"


def test_post_form_yes_clears_question_and_keeps_support_inactive():
    messages = [
        agent(
            1,
            bot.FORM_RECEIVED,
            event="form_received",
            metadata={"form_questions_pending": True, "form_deal_id": 264},
        ),
        client(2, bot.BUTTON_FORM_QUESTIONS_YES),
        agent(3, bot.FORM_QUESTIONS_HINT, event="form_questions_yes"),
    ]

    assert state.pending_form_questions(messages) == {}
    assert state.support_state(messages).mode == "inactive"
    assert bot.TERMS_REPLY == (
        "Условия присоединения к ИУ вы можете прочитать в ПДФ файле выше.\n\n"
        "Вы можете посчитать свою экономию в нашем "
        f"[калькуляторе ИУ]({bot.CALCULATOR_URL}), а также ознакомиться с примерным "
        "договором, прикрепленным ниже."
    )


def test_filled_join_choice_is_durable_and_clears_after_selection():
    prompt = agent(
        1,
        "Анкета",
        event="join_filled",
        metadata={
            "join_filled_choice_pending": True,
            "manager_notification_form_deal_id": 284,
        },
    )
    assert state.pending_filled_join_choice([prompt])[
        "manager_notification_form_deal_id"
    ] == 284

    manager_choice = agent(
        2,
        bot.JOIN_MANAGER_CALLED,
        event="join_filled_manager",
    )
    menu_choice = agent(3, bot.MENU_PROMPT, event="join_filled_menu")
    assert state.pending_filled_join_choice([prompt, manager_choice]) == {}
    assert state.pending_filled_join_choice([prompt, menu_choice]) == {}


def test_calculator_discussion_state_survives_until_form_confirmation():
    messages = [
        agent(
            1,
            bot.join_reply("https://www.m4s.ru/iu/personal"),
            event="calculator_discussion_unfilled",
            service=True,
        )
    ]
    assert state.calculator_discussion_pending(messages) is True

    messages.append(
        agent(
            2,
            bot.CALCULATOR_FORM_RECEIVED,
            event="calculator_form_received",
            service=True,
        )
    )
    assert state.calculator_discussion_pending(messages) is False


def test_calculator_discussion_accepts_current_and_cached_page_copy():
    assert bot.is_calculator_discussion(bot.CALCULATOR_DISCUSSION_TEXT) is True
    assert bot.is_calculator_discussion(bot.CALCULATOR_DISCUSSION_LEGACY_TEXT) is True
    assert bot.is_calculator_discussion("Хочу просто задать вопрос") is False
