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


# Кусок шаблона владельца в том виде, в каком его отдаёт база знаний (с шапкой источника).
TEMPLATE = """Источник: https://docs.google.com/document/d/XXX/edit
Обновлено в Google Drive: 2026-07-23T07:31:25.257Z
Тип: application/vnd.google-apps.document

ДОГОВОР ВОЗМЕЗДНОГО ОКАЗАНИЯ УСЛУГ № {НОМЕР ДОГОВОРА}

Таблица 1

| г. {ГОРОД} | {ДАТА ДОГОВОРА} |
| --- | --- |

{ИСПОЛНИТЕЛЬ — НАЗВАНИЕ}, ИНН {ИСПОЛНИТЕЛЬ — ИНН}, именуемое «Исполнитель», в лице {ИСПОЛНИТЕЛЬ — ДОЛЖНОСТЬ} {ИСПОЛНИТЕЛЬ — ФИО}, с одной стороны, и

ООО «{НАЗВАНИЕ}», ИНН {ИНН}, КПП {КПП}, ОГРН {ОГРН}, именуемое «Заказчик», в лице {ДОЛЖНОСТЬ} {ФИО}, с другой стороны.

1. ПРЕДМЕТ ДОГОВОРА

1.1. Исполнитель оказывает услуги по подключению к индивидуальным условиям.

Юридический адрес: {АДРЕС}
Р/с {РАСЧЁТНЫЙ СЧЁТ} в {БАНК}, БИК {БИК}"""


def test_template_source_header_is_not_in_the_contract():
    """База знаний приклеивает к тексту «Источник:» и «Тип:» — клиенту это видеть незачем."""
    from contract import load_template

    text = load_template(fetch_text=lambda _: TEMPLATE)

    assert "Источник:" not in text and "Обновлено в Google Drive" not in text
    assert text.startswith("ДОГОВОР ВОЗМЕЗДНОГО")


def test_client_placeholders_are_filled_from_requisites():
    from contract import fill_template

    out = fill_template(TEMPLATE, parse_requisites(REAL), "23.07.2026-1", "23 июля 2026 г.")

    assert "ООО «Альфа Трейд», ИНН 7704123456, КПП 770401001" in out
    assert "Иванов Иван Иванович" in out
    assert "40702810912345678901" in out and "АО «Тест Банк»" in out
    assert "23.07.2026-1" in out and "23 июля 2026 г." in out


def test_executor_placeholders_are_not_eaten_by_client_ones():
    """«{ИСПОЛНИТЕЛЬ — ИНН}» содержит «{ИНН}» — наивная замена подставила бы туда ИНН клиента."""
    from contract import fill_template

    out = fill_template(TEMPLATE, parse_requisites(REAL), "23.07.2026", "23 июля 2026 г.",
                        executor={"name": "ООО «АЛБЕРИ»", "inn": "9999999999",
                                  "director": "Никитенко А.", "position": "Генеральный директор"})

    assert "ООО «АЛБЕРИ», ИНН 9999999999" in out
    assert "ИНН 7704123456" in out, "ИНН заказчика тоже должен остаться на своём месте"


def test_unfilled_placeholders_are_reported():
    """Владелец должен видеть, что в договоре осталось незаполненным."""
    from contract import fill_template, unfilled_placeholders

    out = fill_template(TEMPLATE, parse_requisites(REAL), "23.07.2026", "23 июля 2026 г.")

    gaps = unfilled_placeholders(out)
    assert any("ИСПОЛНИТЕЛЬ" in g for g in gaps), "реквизиты Албери не заданы — это надо видеть"


def test_pdf_is_built_from_the_template():
    """Договор собирается целиком, без участия человека."""
    pdf = render_contract_pdf("23.07.2026-1", "23 июля 2026 г.", parse_requisites(REAL),
                              template=TEMPLATE)

    assert pdf.startswith(b"%PDF"), "это должен быть настоящий PDF"
    assert len(pdf) > 3000


# Блок реквизитов сторон в шаблоне — двухколоночная таблица, переносы внутри ячейки идут как \r.
PARTIES = ("15. АДРЕСА, РЕКВИЗИТЫ И ПОДПИСИ СТОРОН\n\n"
           "| ИСПОЛНИТЕЛЬ\r {ИСПОЛНИТЕЛЬ — НАЗВАНИЕ}\r ИНН {ИСПОЛНИТЕЛЬ — ИНН}\r\r "
           "_______________ / {ИСПОЛНИТЕЛЬ — ФИО} /\r М.П. "
           "| ЗАКАЗЧИК\r ООО «{НАЗВАНИЕ}»\r ИНН {ИНН}\r\r "
           "_______________ / {ФИО} /\r М.П. |")


def test_party_table_stays_a_two_column_table():
    """Раньше ячейки склеивались в один абзац и блок реквизитов превращался в кашу."""
    from contract import parse_blocks

    blocks = parse_blocks(PARTIES)
    tables = [v for kind, v in blocks if kind == "table"]

    assert len(tables) == 1, "блок подписей должен остаться таблицей"
    left, right = tables[0]
    assert left.startswith("ИСПОЛНИТЕЛЬ") and right.startswith("ЗАКАЗЧИК")
    assert "\n" in left, "переносы внутри ячейки (\\r) должны стать переносами строк"
    assert "ИНН" in right and "М.П." in right


def test_city_and_date_row_is_a_table_too():
    from contract import parse_blocks

    blocks = parse_blocks("| г. Москва | 23 июля 2026 г. |")

    assert blocks == [("table", ["г. Москва", "23 июля 2026 г."])]


def test_appendix_starts_on_a_new_page():
    """Приложение № 1 в договоре печатается с новой страницы."""
    from contract import parse_blocks

    kinds = [k for k, _ in parse_blocks("Приложение № 1 к Договору № 23.07.2026")]

    assert kinds == ["pagebreak", "head"]


def test_section_headings_are_recognised():
    from contract import parse_blocks

    blocks = parse_blocks("1. ТЕРМИНЫ И ОПРЕДЕЛЕНИЯ\n1.1. Маркетплейс — торговая площадка.")

    assert blocks[0][0] == "head" and blocks[1][0] == "body"


def test_pdf_fails_loudly_without_a_cyrillic_font(monkeypatch):
    """Молчаливая подмена шрифта дала бы клиенту договор из кракозябр."""
    import contract

    monkeypatch.setattr(contract, "_register_fonts", lambda: False)
    with pytest.raises(RuntimeError, match="шрифт"):
        render_contract_pdf("23.07.2026", "23 июля 2026 г.", parse_requisites(REAL),
                            template=TEMPLATE)
