"""HTML must never reach the reader as literal text.

Bitrix chat renders BB-code, so a tag the model slips in is shown verbatim. The model
routinely opens with BB-code and closes with HTML, and users saw «</b>» inside sentences:
«[b]Что улучшить:</b>», «[b]Палей</b>», «лимит [b]600 секунд</b>» (dialogs 14/16/28/30,
reported 20.07.2026).
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def bb(app_module):
    import b24bot

    return b24bot.bb_sanitize


def test_bb_open_closed_with_html_becomes_bold(bb):
    """The exact shape seen in production."""
    assert bb("[b]Что улучшить:</b>") == "[b]Что улучшить:[/b]"
    assert bb("лимит [b]600 секунд</b> и была остановлена") == "лимит [b]600 секунд[/b] и была остановлена"


def test_italic_mismatch_is_closed_too(bb):
    assert bb("[i]Источник: лист «расчет плана».</i>") == "[i]Источник: лист «расчет плана».[/i]"


def test_full_html_pairs_are_converted(bb):
    assert bb("<b>Итог</b> и <i>примечание</i>") == "[b]Итог[/b] и [i]примечание[/i]"
    assert bb("<strong>Важно</strong>") == "[b]Важно[/b]"
    assert bb("<em>тонко</em>") == "[i]тонко[/i]"


def test_paragraphs_and_breaks_become_newlines(bb):
    assert bb("<p>Первый</p><p>Второй</p>") == "Первый\n\nВторой"
    assert bb("Строка<br>Вторая") == "Строка\nВторая"


def test_lists_become_bullets(bb):
    out = bb("<ul><li>Первый</li><li>Второй</li></ul>")
    assert "- Первый" in out and "- Второй" in out
    assert "<li>" not in out and "</ul>" not in out


def test_html_link_becomes_bitrix_url(bb):
    out = bb('Смотри <a href="https://example.com/x">отчёт</a> тут')
    assert out == "Смотри [URL=https://example.com/x]отчёт[/URL] тут"


def test_no_html_survives_in_a_realistic_answer(bb):
    answer = (
        "[b]Уровни доступов</b>\n"
        "- [b]Просмотр[/b] — поиск регламентов.\n"
        "- [b]Операционные действия</b> — создание задач.\n"
        "<p>Любое действие требует подтверждения.</p>"
    )
    out = bb(answer)
    assert "</b>" not in out and "<p>" not in out and "</p>" not in out
    assert out.count("[b]") == out.count("[/b]"), "теги должны быть парными"


def test_math_and_comparisons_are_not_eaten(bb):
    """A real tag needs a letter right after '<' — arithmetic must survive."""
    assert bb("если a < b и x <= 5, то рост") == "если a < b и x <= 5, то рост"
    assert bb("ДРР < 4% при цене > 1000") == "ДРР < 4% при цене > 1000"


def test_plain_bb_answer_is_untouched(bb):
    text = "[b]Готово[/b]\n- пункт один\n- пункт два"
    assert bb(text) == text
