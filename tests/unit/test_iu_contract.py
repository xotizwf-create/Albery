"""Контракт хода: модель предлагает — код разрешает.

Владелец 26.07.2026: «если уверенность более 0.65 — отвечает, если нет — автоматическая
эскалация человеку».

Здесь проверяется то, чего не мог старый обмен магическими строками: любой непонятый ответ
модели уводит ход человеку, а не превращается в сообщение клиенту.
"""
from __future__ import annotations

import json

import pytest

import iu_contract as c

SOURCES = """Единая комиссия 44% включает комиссию WB, логистику, хранение и приёмку.
Выплаты — в течение 3 рабочих дней после поступления средств от WB."""

OFFERED = ("iu-commission-v1", "iu-payouts-v1")


def plan_json(**over) -> str:
    body = {
        "reply": "Комиссия 44%, в неё входят логистика, хранение и приёмка.",
        "next_action": c.REPLY_ONLY,
        "confidence": 0.9,
        "answered": ["комиссия"],
        "source_ids": ["iu-commission-v1"],
    }
    body.update(over)
    return json.dumps(body, ensure_ascii=False)


# --- разбор -------------------------------------------------------------------------------

def test_parses_plain_object():
    plan = c.parse(plan_json(), offered_sources=OFFERED)

    assert plan.next_action == c.REPLY_ONLY
    assert plan.confidence == 0.9
    assert plan.source_ids == ("iu-commission-v1",)
    assert plan.answered == ("комиссия",)


def test_parses_object_wrapped_in_cli_chatter():
    """Hermes зовётся подпроцессом и печатает ответ текстом: вокруг объекта бывает болтовня."""
    raw = f"Хорошо, вот мой ход:\n```json\n{plan_json()}\n```\nГотово."

    assert c.parse(raw, offered_sources=OFFERED).next_action == c.REPLY_ONLY


def test_parses_object_with_braces_inside_strings():
    """Фигурная скобка внутри текста клиенту не должна рвать разбор."""
    raw = plan_json(reply="Комиссия 44% {без скрытых доплат}")

    assert "{без скрытых доплат}" in c.parse(raw, offered_sources=OFFERED).reply


def test_unknown_field_is_rejected():
    """Лишнее поле значит, что модель играет по другому контракту — доверять ходу нельзя."""
    raw = json.dumps({"reply": "ок", "next_action": c.REPLY_ONLY, "confidence": 0.9,
                      "tool_call": "delete_deal"}, ensure_ascii=False)

    with pytest.raises(c.ContractError, match="неизвестные поля"):
        c.parse(raw, offered_sources=OFFERED)


def test_action_outside_contract_is_rejected():
    with pytest.raises(c.ContractError, match="вне контракта"):
        c.parse(plan_json(next_action="delete_crm_deal"), offered_sources=OFFERED)


def test_invented_source_is_rejected():
    """Выдуманный `source_id` — это выдуманный факт, ровно то, на чём агент врал клиентам."""
    with pytest.raises(c.ContractError, match="источник"):
        c.parse(plan_json(source_ids=["iu-secret-discount-v9"]), offered_sources=OFFERED)


def test_confidence_out_of_range_is_rejected():
    with pytest.raises(c.ContractError, match="вне диапазона"):
        c.parse(plan_json(confidence=1.4), offered_sources=OFFERED)


def test_handoff_without_reason_is_rejected():
    with pytest.raises(c.ContractError, match="причины"):
        c.parse(plan_json(next_action=c.HANDOFF, reply=""), offered_sources=OFFERED)


def test_empty_reply_is_rejected():
    """Пустой текст — это молчание клиенту, из-за которого люди ждали часами."""
    with pytest.raises(c.ContractError, match="без текста"):
        c.parse(plan_json(reply="   "), offered_sources=OFFERED)


def test_garbage_is_rejected():
    with pytest.raises(c.ContractError, match="нет объекта хода"):
        c.parse("Извините, я не понял вопрос.", offered_sources=OFFERED)


def test_handoff_keeps_reason_and_needs_no_reply():
    plan = c.parse(plan_json(next_action=c.HANDOFF, reply="",
                             handoff_reason="спор о расчёте"), offered_sources=OFFERED)

    assert plan.wants_human and plan.handoff_reason == "спор о расчёте"


# --- порог уверенности --------------------------------------------------------------------

def test_confident_grounded_answer_passes():
    plan = c.parse(plan_json(reply="Выплаты приходят в течение трёх рабочих дней.",
                             answered=["выплаты"]), offered_sources=OFFERED)

    verdict = c.assess(plan, retrieval=0.9, sources_text=SOURCES,
                       message="когда приходят выплаты?")

    assert verdict.allowed and verdict.score >= 0.65


def test_weak_retrieval_escalates_even_when_model_is_sure():
    """Модели переоценивают себя: «уверен на 1.0» при пустом поиске не должно пускать ответ."""
    plan = c.parse(plan_json(confidence=1.0), offered_sources=OFFERED)

    verdict = c.assess(plan, retrieval=0.05, sources_text=SOURCES)

    assert verdict.escalate
    assert any("ниже порога" in r for r in verdict.reasons)


def test_unbacked_number_is_a_veto_not_a_penalty():
    """Высокий поиск и бодрая само-оценка не имеют права перевесить выдуманную цифру.

    До вето ответ «для вас сделаем 20%» при источнике с 44% набирал 0.81 и уходил клиенту."""
    plan = c.parse(plan_json(reply="Для вас сделаем 20%.", confidence=0.99),
                   offered_sources=OFFERED)

    verdict = c.assess(plan, retrieval=0.95, sources_text=SOURCES)

    assert verdict.escalate
    assert verdict.score > c.THRESHOLD, "скор высокий — эскалация именно из-за вето"
    assert any("вето" in r for r in verdict.reasons)


def test_number_absent_from_sources_lowers_grounding():
    """Цифры условий гуляли от диалога к диалогу — теперь это стоит трети оценки."""
    plan = c.parse(plan_json(reply="Комиссия всего 22%."), offered_sources=OFFERED)

    verdict = c.assess(plan, retrieval=0.9, sources_text=SOURCES)

    assert verdict.grounding < 1.0
    assert any("не подтверждены" in r for r in verdict.reasons)


def test_fact_without_any_source_escalates():
    plan = c.parse(plan_json(source_ids=[], answered=[],
                             reply="Комиссия зависит от вашего оборота."),
                   offered_sources=OFFERED)

    verdict = c.assess(plan, retrieval=0.4, sources_text=SOURCES)

    assert verdict.escalate


def test_greeting_is_not_measured_against_the_threshold():
    """Порог охраняет факты, а не беседу: у «Здравствуйте» нет и не может быть источников."""
    plan = c.parse(plan_json(reply="Здравствуйте! Чем могу помочь?", answered=[],
                             source_ids=[], confidence=0.5), offered_sources=OFFERED)

    verdict = c.assess(plan, retrieval=0.0, sources_text="")

    assert verdict.allowed and verdict.checked is False


def test_clarifying_question_is_not_measured_either():
    plan = c.parse(plan_json(reply="Уточните, речь о продажах на Wildberries?",
                             answered=[], source_ids=[], confidence=0.4),
                   offered_sources=OFFERED)

    assert c.assess(plan, retrieval=0.0, sources_text="").allowed


def test_model_asking_for_human_always_escalates():
    plan = c.parse(plan_json(next_action=c.HANDOFF, reply="",
                             handoff_reason="клиент просит менеджера"),
                   offered_sources=OFFERED)

    assert c.assess(plan, retrieval=1.0, sources_text=SOURCES).escalate


def test_threshold_is_the_owner_number():
    assert c.THRESHOLD == pytest.approx(0.65)
    assert c.CALC_THRESHOLD == pytest.approx(0.95)


# --- строгий порог для денег ------------------------------------------------------------------

def test_calculation_request_uses_the_strict_threshold():
    """Владелец 26.07.2026: «когда дело касается расчётов — уверенность 95%+»."""
    plan = c.parse(plan_json(reply="Расскажу, как это устроено.", answered=[]),
                   offered_sources=OFFERED)

    for message in ("посчитайте мою экономику", "какая будет прибыль?",
                    "от чего считается процент?", "а выручка какая выйдет?"):
        assert c.threshold_for(plan, message) == pytest.approx(0.95), message


def test_models_own_calculation_wording_also_triggers_it():
    """Клиент мог не просить расчёт — модель начала считать сама."""
    plan = c.parse(plan_json(reply="44% вычитается от суммы к перечислению."),
                   offered_sources=OFFERED)

    assert c.threshold_for(plan, "а как это работает?") == pytest.approx(0.95)


def test_quoting_a_rate_is_not_a_calculation():
    """«Комиссия 44%» новых чисел не выводит, и её защищает вето на неподтверждённые числа.

    Иначе строгий порог требовал бы почти идеального поиска на самый частый вопрос воронки, и
    «сколько вы берёте» всегда уходило бы человеку."""
    plan = c.parse(plan_json(reply="Комиссия 44%."), offered_sources=OFFERED)

    assert c.threshold_for(plan, "а сколько вы берёте?") == pytest.approx(0.65)


def test_ordinary_talk_keeps_the_normal_threshold():
    plan = c.parse(plan_json(reply="Подключение занимает три рабочих дня.", answered=["сроки"]),
                   offered_sources=OFFERED)

    assert c.threshold_for(plan, "как быстро подключите?") == pytest.approx(0.65)


def test_calculation_that_would_pass_normally_still_escalates():
    """Ход, проходящий обычный порог, на расчёте уходит человеку."""
    plan = c.parse(plan_json(reply="Выходит около 3 дней ожидания.", confidence=0.9),
                   offered_sources=OFFERED)

    verdict = c.assess(plan, retrieval=0.85, sources_text=SOURCES,
                       message="посчитайте, сколько получится")

    assert 0.65 < verdict.score < 0.95
    assert verdict.escalate


def test_score_is_dominated_by_checkable_parts():
    """Мнение модели весит меньше поиска и опоры на источник — его нельзя проверить."""
    assert c.W_SELF < c.W_GROUNDING < c.W_RETRIEVAL
    assert c.W_RETRIEVAL + c.W_GROUNDING + c.W_SELF == pytest.approx(1.0)
