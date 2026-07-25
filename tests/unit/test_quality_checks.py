"""Механические проверки качества: ловят провалы этой недели без модели (фаза 4).

Каждый тест — реальный случай с прода. Если проверка перестанет их ловить, ночной обзор станет
бесполезным, поэтому проверки закреплены здесь.
"""
from __future__ import annotations

import quality_checks as qc


def kinds(issues):
    return {i.kind for i in issues}


def test_first_message_without_greeting_is_caught():
    """Диалог 256942600: первым сообщением ушло голое «Вижу анкету:»."""
    issues = qc.check_message("Вижу анкету:\n\n• Категории товара — Трусы\n\nВсё верно?",
                              first_in_dialog=True)

    assert "нет приветствия" in kinds(issues)


def test_greeting_in_the_first_message_passes():
    issues = qc.check_message("Здравствуйте, Георгий!\n\nСпасибо, анкету получил! Всё верно?",
                              first_in_dialog=True)

    assert issues == []


def test_greeting_is_not_required_mid_conversation():
    assert qc.check_message("Условия отправил выше, вопросы есть?") == []


def test_promise_of_unit_economics_is_caught():
    """Диалог 764181402: «посмотрим экономику по нему» — инструмента нет."""
    issues = qc.check_message("Да, пришлите артикул или ссылку на товар — посмотрим экономику по нему")

    assert "невыполнимое обещание" in kinds(issues)
    assert len([i for i in issues if i.kind == "невыполнимое обещание"]) == 2, "оба обещания"


def test_service_marker_leak_is_caught():
    assert "утечка служебного" in kinds(qc.check_message("ПОКАЖИ_УСЛОВИЯ"))
    assert "утечка служебного" in kinds(qc.check_message("Извините, техническая заминка по условиям"))
    assert "утечка служебного" in kinds(
        qc.check_message("Источник: https://docs.google.com/document/d/XXX/edit"))


def test_two_questions_in_one_message_are_flagged():
    """Правило одного вопроса: чем меньше усилий клиента, тем выше лояльность."""
    issues = qc.check_message("Какая у вас категория товара? И какой оборот?")

    assert "много вопросов" in kinds(issues)


def test_verbatim_document_is_not_judged_by_length_or_questions():
    """Документ условий задаёт владелец: 2000 символов и вопросы внутри — не вина агента."""
    long_doc = "Условия. " * 200 + "Есть вопросы? Какие категории? "

    assert qc.check_message(long_doc, is_verbatim_block=True) == []
    assert "простыня" in kinds(qc.check_message(long_doc))


def test_unanswered_client_question_is_caught():
    """Случай Георгия: клиент спросил и остался без ответа."""
    issues = qc.check_dialog([
        {"direction": "out", "text": "Здравствуйте! Чем помочь?"},
        {"direction": "in", "text": "А какая комиссия?"},
    ])

    assert "вопрос без ответа" in kinds(issues)


def test_answered_dialog_is_clean():
    issues = qc.check_dialog([
        {"direction": "out", "text": "Здравствуйте, Пётр! Чем помочь?"},
        {"direction": "in", "text": "А какая комиссия?"},
        {"direction": "out", "text": "Комиссия 44% — в неё уже входят логистика и хранение."},
    ])

    assert issues == []


def test_summary_counts_by_kind():
    issues = qc.check_message("ПОКАЖИ_УСЛОВИЯ Какая категория? А оборот?", first_in_dialog=True)

    text = qc.summary(issues)
    assert "нет приветствия" in text and "утечка служебного" in text
    assert qc.summary([]) == "нарушений нет"
