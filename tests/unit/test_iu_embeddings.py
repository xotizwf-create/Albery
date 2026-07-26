"""Семантический поиск: карточка находится по смыслу, без ручного списка формулировок.

Владелец 26.07.2026: «для этого же есть эмбеддинги, не надо вручную эту фигню прописывать».

Сеть в тестах не трогаем: вызов эмбеддингов подменяется целиком. Вектора игрушечные, но
проверяют то, что важно — карточка без единого общего слова с вопросом обязана находиться, а
падение API не имеет права стоить клиенту ответа.
"""
from __future__ import annotations

import pytest

import iu_embeddings as e
import iu_knowledge as k

# Карточки БЕЗ строки «Спрашивают так»: владелец пишет только сам факт.
DOC = """
### Комиссия
Ответ: Единая комиссия 44%. В неё входят комиссия WB, логистика, хранение и приёмка.
---
### Сроки подключения
Ответ: Подключение занимает 3 рабочих дня после подписания договора.
"""

CARDS = k.parse_cards(DOC)

# Игрушечное «смысловое пространство»: первая ось — деньги, вторая — время.
VECTORS = {
    "Комиссия": [1.0, 0.0],
    "Сроки подключения": [0.0, 1.0],
    "сколько вы берёте?": [0.97, 0.05],
    "как долго ждать запуска?": [0.05, 0.97],
}


def fake_embed(texts):
    out = []
    for text in texts:
        match = next((v for key, v in VECTORS.items() if key in text), None)
        out.append(match or [0.0, 0.0])
    return out


@pytest.fixture
def index(monkeypatch):
    monkeypatch.setattr(e.Index, "embed", staticmethod(fake_embed))
    return e.Index(store={})


# --- поиск по смыслу -----------------------------------------------------------------------

def test_card_is_found_without_any_shared_word(index):
    """«Сколько вы берёте» и «Комиссия» не имеют общих слов — лексика тут бессильна."""
    lexical = k.search("сколько вы берёте?", CARDS)
    assert lexical == ()

    found = k.search("сколько вы берёте?", CARDS, rerank=index.rerank)

    assert found and found[0].card.title == "Комиссия"
    assert found[0].score > 0.65


def test_paraphrase_finds_the_right_card(index):
    found = k.search("как долго ждать запуска?", CARDS, rerank=index.rerank)

    assert found[0].card.title == "Сроки подключения"


def test_unrelated_question_still_finds_nothing(index):
    """Семантика не должна превращать «любой вопрос» в «есть ответ»."""
    assert k.search("вы возите грузы из Китая?", CARDS, rerank=index.rerank) == ()


def test_lexical_result_is_never_lowered_by_semantics(index):
    """Итог — максимум из двух: семантика поднимает, но не роняет найденное лексикой."""
    lexical = k.search("какая комиссия?", CARDS)
    semantic = k.search("какая комиссия?", CARDS, rerank=index.rerank)

    assert semantic[0].score >= lexical[0].score


# --- устойчивость --------------------------------------------------------------------------

def test_api_failure_falls_back_to_lexical_search(monkeypatch):
    """Протухший ключ или 429 не имеют права стоить клиенту ответа."""
    def broken(texts):
        raise e.Unavailable("HTTP 429")

    monkeypatch.setattr(e.Index, "embed", staticmethod(broken))
    index = e.Index(store={})

    found = k.search("какая комиссия?", CARDS, rerank=index.rerank)

    assert found and found[0].card.title == "Комиссия"


def test_unexpected_crash_also_falls_back(monkeypatch):
    def broken(texts):
        raise ValueError("что-то совсем неожиданное")

    monkeypatch.setattr(e.Index, "embed", staticmethod(broken))

    assert k.search("какая комиссия?", CARDS, rerank=e.Index(store={}).rerank)


def test_missing_key_disables_the_layer(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")

    assert e.scorer() is None


# --- кэш и калибровка ----------------------------------------------------------------------

def test_vectors_are_computed_once_and_reused(index, monkeypatch):
    """Правка одной карточки не должна тянуть пересчёт всего корпуса."""
    calls = []

    def counting(texts):
        calls.append(list(texts))
        return fake_embed(texts)

    monkeypatch.setattr(e.Index, "embed", staticmethod(counting))

    index.warm(CARDS)
    first = len(calls)
    index.warm(CARDS)

    assert len(calls) == first, "второй прогон не должен считать ничего заново"


def test_changed_card_is_recomputed_alone(index, monkeypatch):
    index.warm(CARDS)
    edited = k.parse_cards(DOC.replace("44%", "40%"))
    asked = []

    def counting(texts):
        asked.extend(texts)
        return fake_embed(texts)

    monkeypatch.setattr(e.Index, "embed", staticmethod(counting))
    index.warm(edited)

    assert len(asked) == 1 and "40%" in asked[0]


def test_calibration_maps_cosine_into_the_confidence_scale():
    """Сырой косинус в порог отдавать нельзя — он систематически ниже 0.65."""
    assert e.calibrate(0.0) == 0.0
    assert e.calibrate(1.0) == 1.0
    assert 0.0 < e.calibrate((e.COS_FLOOR + e.COS_CEIL) / 2) < 1.0


def test_cosine_handles_degenerate_vectors():
    assert e.cosine([], [1.0]) == 0.0
    assert e.cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
    assert e.cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)


def test_card_text_needs_no_manual_aliases():
    """Смысл кодируется заголовком и ответом — список формулировок больше не обязателен."""
    text = e.card_text(CARDS[0])

    assert "Комиссия" in text and "44%" in text
