"""Документ условий — второй источник фактов, а не только текст на отправку.

Владелец 29.07.2026: «нужно, чтобы этот агент отвечал так же качественно, как Албери».
Разбор живых диалогов показал, в чём была разница: Албери отвечает по ВСЕЙ папке «База знаний
— Партнёрская программа WB», а клиентский агент видел только документ «Вопрос - ответ».
Поэтому на вопрос «когда выплаты?» Албери называл срок и еженедельный отчёт комиссионера, а
клиентский агент отвечал «уточню у коллег»: этих фактов в его знаниях просто не было — они
написаны в документе условий, который агент умел лишь пересылать дословно.
"""
from __future__ import annotations

import iu_knowledge

# Клиентская часть живого документа (29.07.2026), уже вырезанная по строке-маркеру.
CLIENT_TEXT = """В связи с пожарами на складах скорректирована партнерская программа

Партнёрская программа Wildberries — продажи через ИУ-кабинет FBS / FBO

Кабинет работает на рынке более 7 лет, оборот за прошлый год — 3 млрд ₽.

Что входит в условия:

- Единая комиссия 44% «всё включено» для любых категорий.

- Выплаты в течение 3 рабочих дней после поступления средств от Wildberries.

- Еженедельный отчёт комиссионера: продажи, удержания и сумма к выплате по вашему товару.

Условия входа:

- Полный пакет документов на товар.

- Единоразовый взнос 500 000р за подключение. В связи с текущей ситуацией мы можем уменьшить
взнос до 200 000р.

Количество мест ограничено, подключаем волнами."""


def test_sections_become_separate_cards():
    cards = iu_knowledge.parse_terms(CLIENT_TEXT)
    titles = [card.title for card in cards]

    assert iu_knowledge.TERMS_INTRO_TITLE in titles
    assert "Что входит в условия" in titles
    assert "Условия входа" in titles


def test_payout_facts_are_now_answerable():
    """Ровно те факты, из-за отсутствия которых агент отправлял клиента к людям."""
    cards = {card.title: card.answer for card in iu_knowledge.parse_terms(CLIENT_TEXT)}
    terms = cards["Что входит в условия"]

    assert "3 рабочих дней" in terms
    assert "Еженедельный отчёт комиссионера" in terms


def test_entry_fee_lives_in_its_own_card():
    """Взнос — отдельный разговор, и цитироваться должен раздел про вход, а не весь документ."""
    cards = {card.title: card.answer for card in iu_knowledge.parse_terms(CLIENT_TEXT)}

    assert "500 000р" in cards["Условия входа"]
    assert "500 000р" not in cards["Что входит в условия"]


def test_list_item_with_a_colon_is_not_a_section():
    """«- Персональный менеджер: подключение, онбординг» — это пункт, а не заголовок раздела."""
    cards = iu_knowledge.parse_terms(
        "Что входит в условия:\n\n- Персональный менеджер: подключение и сопровождение.")
    titles = [card.title for card in cards]

    assert titles == ["Что входит в условия"]
    assert "Персональный менеджер" in cards[0].answer


def test_draft_marks_keep_the_card_away_from_the_client():
    """Незаполненные условия у клиента хуже паузы — правило то же, что у остальных карточек."""
    cards = iu_knowledge.parse_terms("Условия входа:\n\n- Взнос [ЗАПОЛНИТЬ] ₽.")

    assert cards
    assert not iu_knowledge.approved(cards)


def test_empty_document_is_not_a_crash():
    assert iu_knowledge.parse_terms("") == ()
    assert iu_knowledge.parse_terms(None) == ()


def test_cards_are_citable_as_sources():
    """Идентификатор карточки попадает в source_ids, поэтому он обязан быть непустым."""
    for card in iu_knowledge.parse_terms(CLIENT_TEXT):
        assert card.id
        assert card.id == card.id.strip("-")
