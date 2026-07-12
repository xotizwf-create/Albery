"""Bitrix renders no Markdown: tables become a wall of pipes («палки»), ** stays literal.
bb_sanitize is the deterministic net on every message the bot sends (owner, 2026-07-13)."""
from __future__ import annotations

from b24bot import bb_sanitize


def test_markdown_table_becomes_readable_lines():
    src = (
        "Собрал картину:\n\n"
        "| Сотрудник | Должность | Статус |\n"
        "|---|---|---|\n"
        "| Евгений Палей | Генеральный директор | подтверждено |\n"
        "| Анастасия Андрусяк | Бухгалтер | подтверждено |\n\n"
        "Готово."
    )
    out = bb_sanitize(src)
    assert "|" not in out, "ни одной «палки» не должно остаться"
    assert "Евгений Палей — Должность: Генеральный директор; Статус: подтверждено" in out
    assert "Анастасия Андрусяк — Должность: Бухгалтер" in out
    assert out.startswith("Собрал картину:") and out.endswith("Готово.")


def test_two_column_table_is_a_dash_line():
    out = bb_sanitize("| Кто | Роль |\n|---|---|\n| Артур | Руководитель |\n")
    assert out.strip() == "- Артур — Руководитель"


def test_bold_headers_links_code_lists():
    src = ("## Итоги\n"
           "**Важное** и `код` и *курсив* и ~~зачёркнуто~~\n"
           "* пункт один\n"
           "+ пункт два\n"
           "[Битрикс](https://b24.ru/x)\n"
           "---\n")
    out = bb_sanitize(src)
    assert "[b]Итоги[/b]" in out
    assert "[b]Важное[/b]" in out
    assert "код" in out and "`" not in out
    assert "*" not in out  # ни курсива, ни markdown-буллетов
    assert "- пункт один" in out and "- пункт два" in out
    assert "[URL=https://b24.ru/x]Битрикс[/URL]" in out
    assert "зачёркнуто" in out and "~~" not in out


def test_existing_bb_codes_survive():
    src = "[b]Готово[/b]\n[i]Источники: [URL=https://t.me/x]x[/URL][/i]"
    assert bb_sanitize(src) == src


def test_plain_text_unchanged():
    assert bb_sanitize("Просто ответ без разметки.") == "Просто ответ без разметки."
