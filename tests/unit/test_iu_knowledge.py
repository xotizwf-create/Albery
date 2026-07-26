"""Карточки фактов и поиск по ним.

Владелец 26.07.2026: «мы просто наполним нашу систему информацией, агент будет отвечать исходя
из неё».

Главное, что здесь проверяется: клиент спрашивает СВОИМИ словами («сколько вы берёте»), а не
заголовком темы («комиссия»), и карточка обязана находиться всё равно. Именно на этом ломался
старый лексический поиск.
"""
from __future__ import annotations

import iu_knowledge as k

DOC = """
### Комиссия
Спрашивают так: сколько вы берёте, ваша комиссия, процент с продаж, сколько это стоит
Ответ: Единая комиссия 44%. В неё входят комиссия WB, логистика, хранение и приёмка.
Проще: Вы отдаёте 44 рубля с каждых 100 рублей продаж, больше ничего доплачивать не нужно.
Этап: любой
Человек: если клиент спорит с расчётом
---
### Сроки подключения
Спрашивают так: как быстро подключите, сколько ждать, когда начнём
Ответ: Подключение занимает 3 рабочих дня после подписания договора.
Этап: любой
---
### ДРР
Спрашивают так: какой дрр держать, расходы на рекламу
Ответ: [ЗАПОЛНИТЬ]
---
### Налоги
Спрашивают так: какой у вас ндс, налоги
Ответ:
"""

CARDS = k.parse_cards(DOC)


# --- разбор документа владельца ------------------------------------------------------------

def test_parses_all_blocks():
    assert len(CARDS) == 4
    assert [c.title for c in CARDS] == ["Комиссия", "Сроки подключения", "ДРР", "Налоги"]


def test_card_keeps_answer_aliases_and_simple_wording():
    card = CARDS[0]

    assert card.id == "комиссия"
    assert "44%" in card.answer
    assert "сколько вы берёте" in card.aliases
    assert "44 рубля" in card.simple
    assert card.human_when == "если клиент спорит с расчётом"


def test_multiline_answer_is_kept_whole():
    doc = """### Что входит
Ответ: В услугу входит ведение кабинета,
подготовка карточек
и работа с отзывами.
Этап: любой"""

    card = k.parse_cards(doc)[0]

    assert "отзывами" in card.answer and "Этап" not in card.answer


def test_unfilled_cards_are_drafts_and_never_reach_the_client():
    """Пустая заготовка, выданная за факт, хуже честной передачи человеку."""
    titles = [c.title for c in k.drafts(CARDS)]

    assert titles == ["ДРР", "Налоги"]
    assert [c.title for c in k.approved(CARDS)] == ["Комиссия", "Сроки подключения"]


def test_duplicate_titles_get_distinct_ids():
    cards = k.parse_cards("### Комиссия\nОтвет: раз\n---\n### Комиссия\nОтвет: два")

    assert [c.id for c in cards] == ["комиссия", "комиссия-2"]


def test_broken_block_does_not_break_the_document():
    cards = k.parse_cards("мусор без заголовка\n---\n### Комиссия\nОтвет: 44%")

    assert [c.title for c in cards] == ["Комиссия"]


# --- поиск ---------------------------------------------------------------------------------

def test_client_wording_finds_the_card():
    """Клиент говорит «сколько вы берёте», а не «комиссия»."""
    found = k.search("а сколько вы берёте?", CARDS)

    assert found[0].card.id == "комиссия"
    assert found[0].score == 1.0


def test_morphology_does_not_hide_the_card():
    """«комиссии» и «комиссия» — одно слово; на этом ломался старый поиск."""
    found = k.search("расскажите про размер комиссии", CARDS)

    assert found and found[0].card.id == "комиссия"
    assert found[0].score > 0.15


def test_unrelated_question_finds_nothing():
    assert k.search("а вы возите грузы из Китая?", CARDS) == ()


def test_draft_card_is_never_returned():
    """Про ДРР карточка есть, но она пустая — поиск обязан молчать, а не отдавать заготовку."""
    assert k.search("какой дрр нужно держать?", CARDS) == ()


def test_retrieval_score_is_the_best_hit():
    found = k.search("сколько ждать подключение?", CARDS)

    assert k.retrieval_score(found) == max(hit.score for hit in found)
    assert k.retrieval_score(()) == 0.0


def test_sources_carry_ids_the_model_must_cite():
    found = k.search("а сколько вы берёте?", CARDS)

    text = k.sources_text(found)

    assert "[комиссия]" in text and "44%" in text
    assert k.offered_ids(found) == ("комиссия",)


def test_simple_wording_is_used_for_a_repeated_question():
    """Владелец: «если вопрос такой же, человек не понял — объяснить простым языком»."""
    found = k.search("а сколько вы берёте?", CARDS)

    assert "44 рубля" in k.sources_text(found, simple=True)


def test_card_without_simple_wording_falls_back_to_the_answer():
    found = k.search("сколько ждать подключение?", CARDS)

    assert "3 рабочих дня" in k.sources_text(found, simple=True)


def test_human_required_condition_is_surfaced():
    found = k.search("а сколько вы берёте?", CARDS)

    assert k.human_required(found) == "если клиент спорит с расчётом"


def test_rerank_hook_can_reorder_without_rewriting_search():
    """Место для эмбеддингов: Ступень B добавляется передачей второго скорера."""
    def rerank(query, hits):
        return [k.Found(hit.card, 1.0 if hit.card.id == "сроки-подключения" else 0.2)
                for hit in hits]

    found = k.search("сколько это займёт по времени и деньгам", CARDS, rerank=rerank)

    assert found[0].card.id == "сроки-подключения"


def test_failing_rerank_does_not_lose_lexical_results():
    def rerank(query, hits):
        raise RuntimeError("эмбеддинги недоступны")

    assert k.search("а сколько вы берёте?", CARDS, rerank=rerank)[0].card.id == "комиссия"
