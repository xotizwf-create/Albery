"""Договор ИУ: разбор реквизитов клиента и сборка PDF.

Владелец 23.07.2026: агент ставил задачу «подготовить договор» и ждал человека вместо того,
чтобы заполнить шаблон и прислать готовый файл. Реквизиты клиент присылает свободным текстом —
разбор обязан переживать разные подписи полей.
"""
from __future__ import annotations

import pytest

from contract import missing_fields, parse_requisites, render_contract_pdf

# Ровно то сообщение, которое владелец прислал в Telegram 23.07.2026.
REAL = """Наименование: ООО «Альфа Трейд»
ИНН: 7704123456
КПП: 770401001
ОГРН: 1207700123456
ОКПО: 12345678
ОКВЭД: 46.90
Юридический адрес: 125009, г. Москва, ул. Тверская, д. 10, офис 15
Почтовый адрес: 125009, г. Москва, ул. Тверская, д. 10, офис 15
Расчетный счет (р/с): 40702810912345678901
Корреспондентский счет (к/с): 30101810400000000225
БИК: 044525225
Банк: АО «Тест Банк»
Генеральный директор: Иванов Иван Иванович
Телефон: +7 (495) 123-45-67"""


def test_real_client_message_is_parsed():
    r = parse_requisites(REAL)

    assert r["name"] == "ООО «Альфа Трейд»"
    assert r["inn"] == "7704123456"
    assert r["kpp"] == "770401001"
    assert r["ogrn"] == "1207700123456"
    assert r["account"] == "40702810912345678901"
    assert r["corr_account"] == "30101810400000000225"
    assert r["bik"] == "044525225"
    assert r["bank"] == "АО «Тест Банк»"
    assert r["director"] == "Иванов Иван Иванович"
    assert "Тверская" in r["address"]


def test_postal_address_does_not_override_the_legal_one():
    """Юридический адрес идёт в договор; почтовый — нет."""
    assert parse_requisites(REAL)["address"].startswith("125009")
    assert parse_requisites(REAL)["address"] == "125009, г. Москва, ул. Тверская, д. 10, офис 15"


def test_short_labels_are_understood():
    """Люди пишут «р/с» и «тел» — это те же поля."""
    r = parse_requisites("ООО «Бета»\nИНН: 5001234567\nр/с: 40702810900000000111\n"
                         "БИК: 044525999\nБанк: Сбербанк\nАдрес: г. Тула, ул. Мира, 1\n"
                         "Директор: Петров П.П.")

    assert r["account"] == "40702810900000000111"
    assert r["director"] == "Петров П.П."
    assert r["name"] == "ООО «Бета»"


def test_numbers_are_recognised_without_labels():
    """Подписи могут быть непривычными — форму счёта и ИНН узнаём по самим цифрам."""
    r = parse_requisites("Компания: ООО «Гамма»\nномер счёта — 40702810900000000222\n"
                         "идентификационный номер 7712345678")

    assert r["account"] == "40702810900000000222"
    assert r["inn"] == "7712345678"


def test_missing_fields_are_named_in_russian():
    """Агент должен спросить именно недостающее, а не просить реквизиты заново."""
    gaps = missing_fields(parse_requisites("ООО «Дельта»\nИНН: 7701234567"))

    assert "расчётный счёт" in gaps and "БИК" in gaps
    assert "ИНН" not in gaps


def test_complete_requisites_have_no_gaps():
    assert missing_fields(parse_requisites(REAL)) == []


def test_pdf_is_built_with_the_client_data():
    """Договор должен собираться целиком, без участия человека."""
    pdf = render_contract_pdf("23.07.2026-1", "23 июля 2026 г.", parse_requisites(REAL))

    assert pdf.startswith(b"%PDF"), "это должен быть настоящий PDF"
    assert len(pdf) > 8000, "договор из одной страницы — значит текст не попал"


def test_pdf_fails_loudly_without_a_cyrillic_font(monkeypatch):
    """Молчаливая подмена шрифта дала бы клиенту договор из кракозябр."""
    import contract

    monkeypatch.setattr(contract, "_register_fonts", lambda: False)
    with pytest.raises(RuntimeError, match="шрифт"):
        render_contract_pdf("23.07.2026", "23 июля 2026 г.", parse_requisites(REAL))
