"""Состояние сценария ИУ, восстановленное из durable-журнала сообщений."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import iu_client_bot as bot


@dataclass(frozen=True)
class SupportState:
    mode: str  # inactive | active | confirming | quiet
    entered_at: int = 0


def _metadata(message: Mapping[str, Any]) -> Mapping[str, Any]:
    value = message.get("metadata")
    return value if isinstance(value, Mapping) else {}


def event_of(message: Mapping[str, Any]) -> str:
    return str(_metadata(message).get("iu_event") or "")


def _command(text: str) -> str:
    return text.split(maxsplit=1)[0].split("@", 1)[0].lower() if text else ""


def bot_stopped(messages: Sequence[Mapping[str, Any]]) -> bool:
    for message in reversed(messages):
        event = event_of(message)
        if event == "stop":
            return True
        if event in {"start", "menu", "support_enter"}:
            return False
    return False


def awaiting_file_context(messages: Sequence[Mapping[str, Any]]) -> bool:
    """Бот попросил пояснить последний файл и ещё не получил пояснение."""

    for message in reversed(messages):
        event = event_of(message)
        if event == "file_needs_context":
            return True
        if message.get("author_type") == "client":
            text = str(message.get("text") or "").strip()
            if text in {
                bot.BUTTON_TERMS,
                bot.BUTTON_JOIN,
                bot.BUTTON_CALCULATOR,
                bot.BUTTON_ASK,
            } or _command(text) in {"/start", "/menu", "/stop"}:
                return False
            media = str(_metadata(message).get("telegram_media_type") or "text")
            if media == "text":
                return False
    return False


def support_state(messages: Sequence[Mapping[str, Any]]) -> SupportState:
    state = SupportState("inactive")
    menu_closes = {
        bot.BUTTON_TERMS,
        bot.BUTTON_JOIN,
        bot.BUTTON_CALCULATOR,
    }
    for message in messages:
        text = str(message.get("text") or "").strip()
        message_id = int(message.get("id") or 0)
        if message.get("author_type") == "client":
            command = _command(text)
            if text == bot.BUTTON_ASK:
                state = SupportState("active", message_id)
            elif text == bot.BUTTON_EXIT_SUPPORT:
                state = SupportState("confirming", state.entered_at)
            elif text == bot.BUTTON_CONFIRM_NO:
                state = SupportState("active", state.entered_at)
            elif (
                text == bot.BUTTON_CONFIRM_YES
                or text in menu_closes
                or command in {"/start", "/menu", "/stop"}
            ):
                state = SupportState("inactive")
            elif state.mode == "quiet" and text:
                # Тихое завершение не заставляет клиента заново искать кнопку:
                # любое новое сообщение возобновляет тот же разговор.
                state = SupportState("active", message_id)
        if event_of(message) == "support_quiet_close":
            state = SupportState("quiet", state.entered_at)
    return state


def support_agent_replies(messages: Sequence[Mapping[str, Any]]) -> int:
    state = support_state(messages)
    if state.mode not in {"active", "confirming"}:
        return 0
    return sum(
        1
        for message in messages
        if int(message.get("id") or 0) > state.entered_at
        and message.get("author_type") == "agent"
        and message.get("direction") == "outbound"
        and str(message.get("delivery_status") or "sent") == "sent"
        and not bool(_metadata(message).get("service_reply"))
    )


def action_count(messages: Sequence[Mapping[str, Any]], text: str) -> int:
    return sum(
        1
        for message in messages
        if message.get("author_type") == "client"
        and str(message.get("text") or "").strip() == text
    )


def strict_hint_sent(messages: Sequence[Mapping[str, Any]]) -> bool:
    """Подсказка не повторяется до явного перезапуска/входа в поддержку."""

    seen = False
    for message in messages:
        text = str(message.get("text") or "").strip()
        if message.get("author_type") == "client":
            command = _command(text)
            if text in {
                bot.BUTTON_TERMS,
                bot.BUTTON_JOIN,
                bot.BUTTON_CALCULATOR,
                bot.BUTTON_ASK,
                bot.BUTTON_CONFIRM_YES,
            } or command in {"/start", "/menu"}:
                seen = False
        if event_of(message) == "strict_question_hint":
            seen = True
    return seen


def latest_pending_question(messages: Sequence[Mapping[str, Any]]) -> int | None:
    """Последний вопрос до нажатия «Задать вопрос», который ИИ ещё не обрабатывал."""

    excluded = {
        bot.BUTTON_TERMS,
        bot.BUTTON_JOIN,
        bot.BUTTON_CALCULATOR,
        bot.BUTTON_ASK,
        bot.BUTTON_OPERATOR,
        bot.BUTTON_EXIT_SUPPORT,
        bot.BUTTON_CONFIRM_YES,
        bot.BUTTON_CONFIRM_NO,
    }
    boundaries = {
        bot.BUTTON_TERMS,
        bot.BUTTON_JOIN,
        bot.BUTTON_CALCULATOR,
        bot.BUTTON_CONFIRM_YES,
    }
    entry_at = next(
        (
            index
            for index in range(len(messages) - 1, -1, -1)
            if messages[index].get("author_type") == "client"
            and str(messages[index].get("text") or "").strip() == bot.BUTTON_ASK
        ),
        len(messages),
    )
    previous_entry = next(
        (
            index
            for index in range(entry_at - 1, -1, -1)
            if messages[index].get("author_type") == "client"
            and str(messages[index].get("text") or "").strip() == bot.BUTTON_ASK
        ),
        -1,
    )
    hint_at = next(
        (
            index
            for index in range(entry_at - 1, previous_entry, -1)
            if event_of(messages[index]) == "strict_question_hint"
        ),
        -1,
    )
    if hint_at >= 0:
        for message in reversed(messages[previous_entry + 1 : hint_at]):
            if message.get("author_type") != "client":
                continue
            text = str(message.get("text") or "").strip()
            if not text or text.startswith("/") or text in excluded:
                continue
            media = str(_metadata(message).get("telegram_media_type") or "text")
            if media != "text" and not bool(
                _metadata(message).get("telegram_has_caption")
            ):
                continue
            return int(message.get("id") or 0) or None
    if previous_entry >= 0:
        return None

    skipped_current_entry = False
    for message in reversed(messages):
        if message.get("author_type") != "client":
            continue
        text = str(message.get("text") or "").strip()
        if text == bot.BUTTON_ASK:
            if not skipped_current_entry:
                skipped_current_entry = True
                continue
            break
        command = _command(text)
        if text in boundaries or command in {"/start", "/menu", "/stop"}:
            break
        if text.startswith("/") or text in excluded:
            continue
        media = str(_metadata(message).get("telegram_media_type") or "text")
        if media != "text" and not bool(_metadata(message).get("telegram_has_caption")):
            continue
        return int(message.get("id") or 0) or None
    return None


def last_join_result(messages: Sequence[Mapping[str, Any]]) -> str:
    """Последний результат ветки анкеты: filled | unfilled | пусто."""

    for message in reversed(messages):
        event = event_of(message)
        if event == "join_filled":
            return "filled"
        if event == "join_unfilled":
            return "unfilled"
        if (
            message.get("author_type") == "client"
            and str(message.get("text") or "").strip() != bot.BUTTON_JOIN
        ):
            break
    return ""


def pending_form_questions(
    messages: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    """Metadata of the latest unanswered post-form Да/Нет question, or an empty mapping."""

    pending: Mapping[str, Any] = {}
    for message in messages:
        event = event_of(message)
        if event in {"form_received", "calculator_form_received"} and bool(
            _metadata(message).get("form_questions_pending")
        ):
            pending = _metadata(message)
        elif event in {
            "form_questions_yes",
            "form_questions_no",
            "join_filled",
            "calculator_discussion_filled",
        }:
            pending = {}
    return pending


def calculator_discussion_pending(messages: Sequence[Mapping[str, Any]]) -> bool:
    """The calculator client still needs to finish the form before manager handover."""

    pending = False
    for message in messages:
        event = event_of(message)
        if event == "calculator_discussion_unfilled":
            pending = True
        elif event in {
            "calculator_discussion_filled",
            "calculator_discussion_unavailable",
            "calculator_form_received",
        }:
            pending = False
    return pending
