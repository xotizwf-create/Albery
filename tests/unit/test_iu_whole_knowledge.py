"""Агент видит базу знаний ЦЕЛИКОМ и сам решает, есть ли в ней ответ.

Владелец 28.07.2026: «есть Агент Албери, он очень хорошо отвечает по нашей базе; нужен такой
же агент в боте, чтобы я дополнял базу и агент её видел». Албери силён не поиском, а тем, что
читает документы целиком. База ИУ — ~2 900 токенов, она помещается в промпт полностью, поэтому
выбирать за модель одну карточку лексическим скором больше незачем: именно этот выбор и давал
«уточню у команды» на вопросы, ответ на которые в базе есть (замер на живых вопросах из
переписок: 1 из 8 против 4 из 8).

Что при этом обязано сохраниться и проверяется здесь: цифры только из базы, спор о деньгах —
человеку, безусловное «здесь нужен человек» — по процитированной карточке, а утверждение о
фактах без ссылки на базу к клиенту не уходит.
"""
from __future__ import annotations

import json

import iu_contract
import iu_filters
import iu_funnel
import iu_knowledge
import iu_turn

DOC = """
### Комиссия
Ответ: Единая комиссия 44% от суммы реализации.
Человек: если клиент спорит с расчётом
---
### Налоговая база
Ответ: Налоги селлер платит сам по своей системе налогообложения. Для расчёта ориентируйтесь
на сумму реализации за вычетом СПП.
---
### Гарантии результата
Ответ: Гарантий по объёму продаж мы не даём.
Человек: всегда
---
### Черновик про тарифы
Ответ: [ЗАПОЛНИТЬ]
"""

CARDS = iu_knowledge.parse_cards(DOC)
RULES = iu_filters.Ruleset(phrases=iu_filters.parse_phrases("Ozon"))
NEW = iu_funnel.DealFacts(stage=iu_funnel.STAGE_NEW)


def model(**over):
    body = {"reply": "", "next_action": iu_contract.REPLY_ONLY, "confidence": 0.9}
    body.update(over)
    return lambda prompt: json.dumps(body, ensure_ascii=False)


def capture():
    """Модель, которая ничего не решает, но запоминает промпт целиком."""
    seen = {}

    def ask(prompt):
        seen["prompt"] = prompt
        return json.dumps({"reply": "Секунду.", "next_action": iu_contract.REPLY_ONLY,
                           "confidence": 0.9}, ensure_ascii=False)

    return ask, seen


def run(message, *, ask, facts=NEW, history="", cards=CARDS):
    return iu_turn.handle(
        iu_turn.Request(message=message, name="Александр", history=history, facts=facts),
        iu_turn.Deps(ask=ask, cards=cards, rules=RULES, rerank=None),
    )


def test_everything_returns_the_whole_approved_base():
    found = iu_knowledge.everything("что угодно", CARDS)

    assert len(found) == len(iu_knowledge.approved(CARDS))
    assert "черновик-про-тарифы" not in {hit.card.id for hit in found}


def test_everything_puts_the_closest_card_first():
    """Порядок не отсекает ничего, но ближайшее к вопросу модель должна прочитать первым."""
    found = iu_knowledge.everything("а сколько вы берёте комиссии?", CARDS)

    assert found[0].card.id == "комиссия"


def test_prompt_holds_every_card_no_matter_what_was_asked():
    """Раньше в промпт попадало только то, что выбрал лексический скор — до вызова модели.

    Проверяем на вопросе, у которого с половиной базы нет ни одного общего слова: карточки
    всё равно обязаны быть в промпте, иначе решение снова принимает поиск, а не агент."""
    ask, seen = capture()
    run("Как я буду платить налоги?", ask=ask)

    for card in iu_knowledge.approved(CARDS):
        assert f"[{card.id}]" in seen["prompt"], card.title
    assert "Единая комиссия 44%" in seen["prompt"]
    assert "[черновик-про-тарифы]" not in seen["prompt"]


def test_answer_from_a_card_word_search_missed_reaches_the_client():
    out = run("Как я буду платить налоги?", ask=model(
        reply="Налоги вы платите сами по своей системе налогообложения.",
        source_ids=["налоговая-база"], answered=["налоги"]))

    assert not out.escalate
    assert "по своей системе" in out.reply
    assert out.sources == ("налоговая-база",)


def test_fact_without_a_source_goes_to_a_human():
    """Замена прежнего порога поиска: утверждаешь факт — назови карточку владельца."""
    out = run("Сколько стоит подключение?", ask=model(
        reply="Подключение стоит 300 000 ₽.", source_ids=[]))

    assert out.escalate


def test_unconditional_human_rule_fires_on_the_cited_card():
    out = run("Какие гарантии вы даёте?", ask=model(
        reply="Гарантий по объёму продаж мы не даём.", source_ids=["гарантии-результата"]))

    assert out.escalate
    assert "всегда" in out.reason


def test_unconditional_human_rule_does_not_touch_other_topics():
    """Раньше одно «всегда» на карточке гарантий увело бы к человеку каждый разговор."""
    out = run("А сколько вы берёте?", ask=model(
        reply="Единая комиссия 44% от суммы реализации.", source_ids=["комиссия"]))

    assert not out.escalate


def test_conditions_reach_the_prompt_with_their_topic():
    ask, seen = capture()
    run("Расскажите про условия", ask=ask)

    assert "«Комиссия» — если клиент спорит с расчётом" in seen["prompt"]
    # Безусловное правило исполняет код, в промпте ему делать нечего.
    assert "Условие владельца по этой теме: всегда" not in seen["prompt"]


def test_invented_number_is_still_vetoed_with_the_whole_base_shown():
    out = run("Сколько стоит подключение?", ask=model(
        reply="Для вас сделаем 20% вместо 44%.", source_ids=["комиссия"]))

    assert out.escalate


def test_money_dispute_still_goes_to_a_human():
    out = run("Вы неправильно считаете, 44% вычитается от продаж", ask=model(
        reply="44% считается от суммы реализации.", source_ids=["комиссия"]))

    assert out.escalate
