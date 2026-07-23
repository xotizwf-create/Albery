"""Нумерация договоров: №ДД.ММ.ГГГГ, второй за день — с суффиксом (владелец, 23.07.2026)."""
from __future__ import annotations

from mcp.context_server import next_contract_number


def test_first_contract_of_the_day_is_just_the_date():
    assert next_contract_number([], "23.07.2026") == "23.07.2026"


def test_second_contract_of_the_day_gets_a_suffix():
    assert next_contract_number(["23.07.2026"], "23.07.2026") == "23.07.2026-1"
    assert next_contract_number(["23.07.2026", "23.07.2026-1"], "23.07.2026") == "23.07.2026-2"


def test_yesterdays_numbers_do_not_shift_today():
    """Иначе первый договор нового дня получил бы суффикс без причины."""
    assert next_contract_number(["22.07.2026", "22.07.2026-1"], "23.07.2026") == "23.07.2026"


def test_gap_never_reuses_a_number():
    """Договор 23.07.2026-1 отменили — повторная выдача дала бы два документа с одним номером."""
    assert next_contract_number(["23.07.2026", "23.07.2026-2"], "23.07.2026") == "23.07.2026-3"


def test_number_with_a_leading_hash_is_recognised():
    """В карточке номер могли записать как «№23.07.2026» — это тот же номер."""
    assert next_contract_number(["№23.07.2026"], "23.07.2026") == "23.07.2026-1"


def test_foreign_formats_are_ignored():
    """Чужие номера в поле не должны ломать выдачу."""
    assert next_contract_number(["б/н", "", "ДГ-17/2026"], "23.07.2026") == "23.07.2026"
