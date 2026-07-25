"""Частичные ответы: на что знаем — отвечаем, что не знаем — людям (фаза 2).

Владелец 25.07.2026: «если он знает ответ на несколько вопросов, пусть отвечает на них, а на то
что не знает — пусть эскалирует менеджеру».

Живой случай — диалог 764181402: «Какой дрр нужно держать и как происходит управление? +какая
комиссия ваша по партнерской этой программе?». Про комиссию ответ в документе ЕСТЬ, про ДРР и
управление — нет. Раньше клиент не получал ни одного ответа.
"""
from __future__ import annotations

import json

import answering as a

SOURCES = """Единая комиссия 44% включает комиссию WB, логистику, хранение и приёмку.
Выплаты — в течение 3 рабочих дней после поступления средств от WB.
Стоимость подключения — 30 000 ₽ в месяц."""

REAL_MESSAGE = ("Какой дрр нужно держать и как происходит управление?\n"
                "+какая комиссия ваша по партнерской этой программе?")


def test_real_message_splits_into_separate_questions():
    """Клиенты пишут по три вопроса в одном сообщении — через перевод строки и «+»."""
    qs = a.split_questions(REAL_MESSAGE)

    assert len(qs) == 3, qs
    assert "дрр" in qs[0].lower()
    assert "управление" in qs[1].lower()
    assert "комиссия" in qs[2].lower()


def test_split_handles_several_questions_in_one_line():
    qs = a.split_questions("А сроки какие? И кто платит логистику? Хочу понять цифры")

    assert len(qs) == 3


def test_answers_what_is_in_sources_and_escalates_the_rest():
    """Главное требование владельца: частичный ответ вместо «всё или ничего»."""
    def ask(prompt):
        assert "ИСТОЧНИКИ" in prompt and "44%" in prompt
        return json.dumps([
            {"вопрос": "какой дрр держать", "ответ": a.NO_ANSWER, "источник": ""},
            {"вопрос": "как происходит управление", "ответ": a.NO_ANSWER, "источник": ""},
            {"вопрос": "какая комиссия", "ответ": "Комиссия 44% — в неё уже входят логистика, "
                                                 "хранение и приёмка.", "источник": "условия"},
        ], ensure_ascii=False)

    res = a.answer_questions(a.split_questions(REAL_MESSAGE), SOURCES, ask)

    assert [r.known for r in res] == [False, False, True]
    assert a.unknown(res) == ["Какой дрр нужно держать", "как происходит управление?"]
    text = a.client_text(res, pending_note="По ДРР уточню у команды и вернусь.")
    assert "Комиссия 44%" in text
    assert text.rstrip().endswith("По ДРР уточню у команды и вернусь.")


def test_invented_numbers_are_thrown_away():
    """Ответ с цифрой, которой нет в источниках, — это выдумка, а не ответ.

    Именно на цифрах агент врал больнее всего: «200 000 ₽ вместо 500 000» в одном чате,
    «комиссия 44%» в другом (24.07.2026)."""
    def ask(prompt):
        return json.dumps([{"вопрос": "какая комиссия", "ответ": "Комиссия 38% всё включено",
                            "источник": "условия"}], ensure_ascii=False)

    res = a.answer_questions(["какая комиссия?"], SOURCES, ask)

    assert not res[0].known, "неподтверждённая цифра уходит людям, а не клиенту"
    assert a.unknown(res) == ["какая комиссия?"]


def test_numbers_from_sources_pass_in_any_format():
    """«30 000 ₽» и «30000 ₽» — одно и то же число, ответ не должен из-за этого отбраковываться."""
    assert a.grounded("Стоимость 30000 ₽ в месяц", SOURCES)
    assert a.grounded("Выплаты в течение 3 рабочих дней", SOURCES)
    assert not a.grounded("Выплаты в течение 5 рабочих дней", SOURCES)


def test_answer_without_numbers_is_allowed():
    """Не в каждом ответе есть цифры — текстовые ответы по источникам разрешены."""
    assert a.grounded("Логистика и хранение уже входят в комиссию", SOURCES)


def test_grounded_impersonal_modal_fact_is_not_rejected_as_a_cta():
    sources = "Договор можно подписать по ЭДО. Можно выбрать ЭДО или бумагу."

    def ask(_prompt):
        return json.dumps([
            {"вопрос": "как подписать договор",
             "ответ": "Договор можно подписать по ЭДО.", "источник": "регламент"},
            {"вопрос": "какой формат выбрать",
             "ответ": "Можно выбрать ЭДО или бумагу.", "источник": "регламент"},
        ], ensure_ascii=False)

    rows = a.answer_questions(
        ["как подписать договор?", "какой формат выбрать?"],
        sources,
        ask,
    )

    assert [row.known for row in rows] == [True, True]


def test_answering_model_cannot_smuggle_a_question_or_link_to_the_client():
    def ask(prompt):
        return json.dumps([
            {"вопрос": "что входит", "ответ": "Логистика входит. Заполните анкету.",
             "источник": "условия"},
            {"вопрос": "где читать", "ответ": "Подробнее: https://example.com",
             "источник": "условия"},
        ], ensure_ascii=False)

    res = a.answer_questions(["что входит?", "где читать?"], SOURCES, ask)

    assert not any(row.known for row in res)
    assert a.unknown(res) == ["что входит?", "где читать?"]


def test_broken_model_output_sends_everything_to_humans():
    """Модель ответила мусором — клиент не должен получить мусор."""
    res = a.answer_questions(["какая комиссия?"], SOURCES, lambda p: "я не смог, извините")

    assert a.unknown(res) == ["какая комиссия?"]
    assert a.client_text(res) == ""


def test_model_failure_does_not_break_the_turn():
    def ask(prompt):
        raise RuntimeError("модель недоступна")

    res = a.answer_questions(["какая комиссия?"], SOURCES, ask)

    assert a.unknown(res) == ["какая комиссия?"]


def test_json_wrapped_in_prose_is_still_parsed():
    """Модель любит обрамлять JSON пояснениями — это не повод терять ответ."""
    def ask(prompt):
        return ("Вот результат:\n```json\n"
                + json.dumps([{"вопрос": "комиссия", "ответ": "Комиссия 44%",
                               "источник": "условия"}], ensure_ascii=False)
                + "\n```\nГотово.")

    res = a.answer_questions(["какая комиссия?"], SOURCES, ask)

    assert res[0].known and "44%" in res[0].answer
