"""Поля-списки сделок принимают только id варианта (23.07.2026).

Агент записывал «ЭДО» в поле «Способ подписания» — Битрикс молча не сохранял значение, поле
оставалось пустым, шаг воронки считался невыполненным, и на каждое следующее сообщение клиента
ставилась бы новая задача на отправку договора.
"""
from __future__ import annotations

import pytest

from mcp.context_server import McpError, _crm_custom_fields_arg, _CRM_ENUM_CACHE

SIGNING = "UF_CRM_F84751395"


@pytest.fixture(autouse=True)
def enum_dictionary(monkeypatch):
    """Словарь вариантов как из Битрикса, без обращения к порталу."""
    _CRM_ENUM_CACHE.update({"at": 0.0, "map": {}})
    monkeypatch.setattr(
        "mcp.context_server._crm_enum_items",
        lambda: {SIGNING: {"эдо": "101", "бумага": "102", "не выбрано": "103"}})
    yield
    _CRM_ENUM_CACHE.update({"at": 0.0, "map": {}})


def test_label_is_replaced_by_the_item_id():
    out = _crm_custom_fields_arg({"custom_fields": {SIGNING: "ЭДО"}})

    assert out[SIGNING] == "101", "в Битрикс должен уйти id варианта, а не его название"


def test_case_and_spaces_do_not_matter():
    assert _crm_custom_fields_arg({"custom_fields": {SIGNING: " эдо "}})[SIGNING] == "101"
    assert _crm_custom_fields_arg({"custom_fields": {SIGNING: "Бумага"}})[SIGNING] == "102"


def test_id_passed_directly_is_left_alone():
    assert _crm_custom_fields_arg({"custom_fields": {SIGNING: "102"}})[SIGNING] == "102"


def test_wrong_value_fails_loudly_with_the_options():
    """Молчаливое несохранение — худший исход: агент считает шаг сделанным, а поле пустое."""
    with pytest.raises(McpError) as e:
        _crm_custom_fields_arg({"custom_fields": {SIGNING: "телепатия"}})

    assert "список" in str(e.value) and "эдо" in str(e.value)


def test_ordinary_text_fields_are_untouched():
    out = _crm_custom_fields_arg({"custom_fields": {"UF_CRM_F84751394": "ИНН 7704123456"}})

    assert out["UF_CRM_F84751394"] == "ИНН 7704123456"
