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


def test_client_placeholder_left_empty_never_reaches_the_document():
    """Владелец 23.07.2026: клиент не прислал e-mail, и в договоре осталось «{EMAIL}»."""
    from contract import fill_template

    tpl = ("Юридический адрес: {АДРЕС}\n"
           "E-mail: {EMAIL}\n"
           "Тел.: {ТЕЛЕФОН}\n"
           "ООО «{НАЗВАНИЕ}», ИНН {ИНН}, КПП {КПП}")
    out = fill_template(tpl, parse_requisites(REAL), "23.07.2026", "23 июля 2026 г.")

    assert "{EMAIL}" not in out and "{ТЕЛЕФОН}" not in out
    assert "E-mail:" not in out, "строка с пустым значением убирается целиком"
    assert "Тверская" in out and "ИНН 7704123456" in out, "заполненное на месте"


def test_empty_placeholder_inside_a_sentence_leaves_it_readable():
    from contract import fill_template

    out = fill_template("Заказчик, ИНН {ИНН}, КПП {КПП}, ОГРН {ОГРН}, адрес {АДРЕС}.",
                        {"inn": "7704123456", "address": "г. Москва"},
                        "23.07.2026", "23 июля 2026 г.")

    assert "{" not in out
    assert "ИНН 7704123456" in out and "адрес г. Москва" in out
    assert ", ," not in out, "лишние запятые от пустых значений убраны"


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


def test_section_headings_are_centered():
    """Владелец 23.07.2026: заголовки разделов в PDF должны стоять по центру."""
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY

    from contract import build_styles

    styles = build_styles()

    assert styles["head"].alignment == TA_CENTER, "заголовки разделов — по центру"
    assert styles["title"].alignment == TA_CENTER
    assert styles["body"].alignment == TA_JUSTIFY, "текст пунктов остаётся по ширине"


def test_section_headings_are_recognised():
    from contract import parse_blocks

    blocks = parse_blocks("1. ТЕРМИНЫ И ОПРЕДЕЛЕНИЯ\n1.1. Маркетплейс — торговая площадка.")

    assert blocks[0][0] == "head" and blocks[1][0] == "body"


# --- другой шаблон: заголовки не капсом, маркированные списки -------------------------------

def test_headings_without_caps_are_recognised():
    """Владелец может подгрузить шаблон, где разделы названы обычным регистром."""
    from contract import parse_blocks

    blocks = parse_blocks("1. Предмет договора\n"
                          "1.1. Исполнитель оказывает услуги по подключению.\n"
                          "2. Стоимость и порядок расчётов")

    kinds = [k for k, _ in blocks]
    assert kinds == ["head", "body", "head"], "пункт «1.1.» — текст, а «1.»/«2.» — заголовки"


def test_clause_is_never_mistaken_for_a_heading():
    """Иначе каждый пункт договора печатался бы жирным по центру."""
    from contract import parse_blocks

    blocks = parse_blocks("5.2. Стороны признают юридическую силу документов")

    assert blocks[0][0] == "body"


def test_title_is_recognised_in_any_case():
    from contract import parse_blocks

    for line in ("Договор оказания услуг № 24.07.2026",
                 "ДОГОВОР ВОЗМЕЗДНОГО ОКАЗАНИЯ УСЛУГ № 1"):
        assert parse_blocks(line)[0][0] == "title", line


def test_bullet_markers_do_not_leak_into_the_pdf():
    """Маркеры ● из Google Docs раньше уходили клиенту как символы в тексте."""
    from contract import parse_blocks

    blocks = parse_blocks("●\tпервый пункт\n• второй пункт")

    assert blocks == [("bullet", "первый пункт"), ("bullet", "второй пункт")]


def test_a_foreign_template_renders_without_losing_structure():
    """Сквозная проверка: чужой шаблон должен собраться со всеми видами блоков."""
    from contract import parse_blocks, render_contract_pdf

    other = ("Договор оказания услуг № {НОМЕР ДОГОВОРА}\n\n"
             "| г. {ГОРОД} | {ДАТА ДОГОВОРА} |\n\n"
             "1. Предмет договора\n\n"
             "1.1. Исполнитель оказывает услуги.\n\n"
             "●\tпервый пункт\n\n"
             "Приложение № 1 к договору\n\n"
             "| Исполнитель\rИНН 1 | Заказчик\rИНН {ИНН} |")

    kinds = [k for k, _ in parse_blocks(other)]
    assert kinds == ["title", "table", "head", "body", "bullet", "pagebreak", "head", "table"]

    pdf = render_contract_pdf("24.07.2026", "24 июля 2026 г.", parse_requisites(REAL),
                              template=other)
    assert pdf.startswith(b"%PDF")


def test_template_is_found_by_name_not_only_by_id():
    """Подгружённый заново шаблон получает НОВЫЙ id — иначе агент собирал бы старый договор."""
    from contract import find_template_id

    files = [{"name": "Что отвечать лидам", "google_file_id": "old-1"},
             {"name": "Шаблон договора ИУ", "google_file_id": "new-42",
              "updated_at": "2026-07-24T10:00:00Z"}]

    assert find_template_id(list_files=lambda: files) == "new-42"


def test_newest_template_wins_when_several_match():
    from contract import find_template_id

    files = [{"name": "Шаблон договора ИУ", "google_file_id": "a", "updated_at": "2026-07-01"},
             {"name": "Шаблон договора ИУ (новый)", "google_file_id": "b",
              "updated_at": "2026-07-24"}]

    assert find_template_id(list_files=lambda: files) == "b"


def test_missing_template_falls_back_to_the_pinned_id():
    """Сбой поиска не должен ронять сборку договора."""
    from contract import TEMPLATE_DOC_ID, find_template_id

    assert find_template_id(list_files=lambda: []) == TEMPLATE_DOC_ID
    assert find_template_id(
        list_files=lambda: (_ for _ in ()).throw(RuntimeError("нет связи"))) == TEMPLATE_DOC_ID


def test_pdf_fails_loudly_without_a_cyrillic_font(monkeypatch):
    """Молчаливая подмена шрифта дала бы клиенту договор из кракозябр."""
    import contract

    monkeypatch.setattr(contract, "_register_fonts", lambda: False)
    with pytest.raises(RuntimeError, match="шрифт"):
        render_contract_pdf("23.07.2026", "23 июля 2026 г.", parse_requisites(REAL),
                            template=TEMPLATE)


# --- адекватность реквизитов (владелец, 24.07.2026) -------------------------------------------
# Живой случай: клиент прислал «188 4884 4838», и агент чуть не принял это за ИНН.

def test_random_digits_do_not_pass_as_inn():
    from contract import _inn_valid

    assert not _inn_valid("18848844838"), "фигня из диалога 24.07.2026 — не ИНН"
    assert not _inn_valid("7704123456"), "правдоподобный, но с неверной контрольной суммой"
    assert not _inn_valid("123")


def test_real_inn_checksums_pass():
    from contract import _inn_valid

    assert _inn_valid("7707083893"), "ИНН Сбербанка (10 цифр, организация)"
    assert _inn_valid("500100732259"), "валидный 12-значный ИНН физлица/ИП"


def test_ogrn_checksum():
    from contract import _ogrn_valid

    assert _ogrn_valid("1027700132195"), "ОГРН Сбербанка"
    assert not _ogrn_valid("1207700123456"), "тестовая заглушка не проходит контрольную цифру"
    assert not _ogrn_valid("188")


def test_validate_requisites_names_each_problem_in_russian():
    from contract import validate_requisites

    problems = validate_requisites({"inn": "18848844838", "kpp": "77", "bik": "123456789",
                                    "name": "просто слова", "account": "40702810"})

    text = " ".join(problems)
    assert "ИНН" in text and "контрольной" in text
    assert "КПП" in text and "9 цифр" in text
    assert "БИК" in text and "04" in text
    assert "Название" in text
    assert "Расчётный счёт" in text and "20 цифр" in text


def test_valid_requisites_pass_clean():
    from contract import validate_requisites

    assert validate_requisites({
        "name": "ООО «Настоящая фирма»", "inn": "7707083893", "kpp": "773601001",
        "ogrn": "1027700132195", "bik": "044525225",
        "account": "40702810912345678901", "corr_account": "30101810400000000225",
    }) == []


def test_empty_fields_are_not_flagged_as_invalid():
    """Пустое поле — забота missing_fields, а не validate_requisites."""
    from contract import validate_requisites

    assert validate_requisites({}) == []
