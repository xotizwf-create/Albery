from __future__ import annotations

# Точечная правка PDF: заменяется только названный фрагмент, остальной документ цел.

import io

import pytest

import pdfedit

fitz = pytest.importorskip("fitz", reason="правка PDF требует pymupdf")


def _pdf(lines: list[tuple[float, float, str, float]]) -> bytes:
    """Небольшой PDF с кириллицей: (x, y, текст, кегль)."""

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    font_file = pdfedit.font_path("TimesNewRomanPSMT", bold=False, italic=False)
    page.insert_font(fontname="probe", fontfile=font_file)
    for x, y, text, size in lines:
        page.insert_text(fitz.Point(x, y), text, fontname="probe", fontsize=size)
    buffer = io.BytesIO()
    doc.save(buffer)
    doc.close()
    return buffer.getvalue()


def _text(data: bytes) -> str:
    return pdfedit.extract_text(data)


def test_named_fragment_is_replaced_and_the_rest_of_the_page_survives():
    source = _pdf([
        (72, 100, "ИНН 231102850042, ОГРНИП 305231118100082", 12),
        (72, 130, "Комиссионер обязуется реализовать Товар Комитента.", 12),
    ])

    result, counts, warnings = pdfedit.apply_edits(source, [("231102850042", "____________")])

    assert counts == [1]
    assert warnings == []
    text = _text(result)
    assert "231102850042" not in text
    assert "____________" in text
    # Соседний текст не пострадал — правится фрагмент, а не страница целиком.
    assert "Комиссионер обязуется реализовать Товар Комитента." in text
    assert "305231118100082" in text


def test_deleted_fragment_leaves_no_trace_in_the_text_layer():
    source = _pdf([(72, 100, "Бобровская Виктория Николаевна, ИП", 12)])

    result, counts, _ = pdfedit.apply_edits(source, [("Бобровская Виктория Николаевна", "")])

    assert counts == [1]
    assert "Бобровская" not in _text(result)
    assert "ИП" in _text(result)


def test_replacement_keeps_the_original_size_and_baseline():
    source = _pdf([(72, 100, "ИНН 231102850042", 14)])

    result, _, _ = pdfedit.apply_edits(source, [("231102850042", "000000000000")])

    with fitz.open(stream=result, filetype="pdf") as doc:
        spans = [
            span
            for block in doc[0].get_text("dict")["blocks"]
            for line in block.get("lines", [])
            for span in line["spans"]
        ]
    inserted = [span for span in spans if "000000000000" in span["text"]]
    assert inserted, "замена должна остаться текстом, а не картинкой"
    assert abs(inserted[0]["size"] - 14) < 0.6
    # Базовая линия совпадает с исходной строкой: текст не съезжает вверх или вниз.
    assert abs(inserted[0]["origin"][1] - 100) < 1.5


def test_fragment_broken_by_a_line_break_is_still_found():
    source = _pdf([
        (72, 100, "Индивидуальный предприниматель Бобровская", 12),
        (72, 118, "Виктория Николаевна действует от своего имени", 12),
    ])

    result, counts, _ = pdfedit.apply_edits(
        source, [("Бобровская Виктория Николаевна", "___")]
    )

    assert counts == [1]
    text = _text(result)
    assert "Бобровская" not in text and "Виктория" not in text
    assert "действует от своего имени" in text


def test_missing_fragment_changes_nothing_and_says_so():
    source = _pdf([(72, 100, "Договор комиссии", 12)])

    result, counts, _ = pdfedit.apply_edits(source, [("Договор поставки", "Договор аренды")])

    assert counts == [0]
    assert "Договор комиссии" in _text(result)
    assert "Договор аренды" not in _text(result)


def test_replacement_too_wide_for_the_line_is_reported_not_hidden():
    source = _pdf([(380, 100, "ИНН 231102850042", 12)])

    _, counts, warnings = pdfedit.apply_edits(
        source,
        [("231102850042", "Общество с ограниченной ответственностью «Ромашка», ИНН 7700000000")],
    )

    assert counts == [1]
    assert warnings, "не влезающая замена обязана быть названа, а не молча обрезана"
    assert "стр. 1" in warnings[0]


def test_password_protected_pdf_is_refused_with_a_clear_reason():
    doc = fitz.open()
    doc.new_page()
    buffer = io.BytesIO()
    doc.save(buffer, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="user")
    doc.close()

    with pytest.raises(pdfedit.PdfEditError) as excinfo:
        pdfedit.apply_edits(buffer.getvalue(), [("а", "б")])

    assert "парол" in str(excinfo.value).lower()


def test_oversized_file_is_refused_before_it_is_parsed(monkeypatch):
    monkeypatch.setattr(pdfedit, "MAX_PDF_BYTES", 32)

    with pytest.raises(pdfedit.PdfEditError):
        pdfedit.apply_edits(b"%PDF-1.4" + b"0" * 128, [("а", "б")])


def test_cyrillic_font_is_picked_for_the_document_family():
    serif = pdfedit.font_path("TimesNewRomanPSMT", bold=False, italic=False)
    sans = pdfedit.font_path("Arial", bold=True, italic=False)

    assert serif and "serif" in serif.lower() or "times" in (serif or "").lower()
    assert sans and ("sans" in sans.lower() or "arial" in sans.lower())
    # Шрифт обязан уметь кириллицу — иначе вместо текста в PDF будет пустота.
    font = fitz.Font(fontfile=serif)
    assert all(font.has_glyph(ord(character)) for character in "Бобровская№12")


def test_scanned_pdf_without_a_text_layer_is_refused_not_silently_unchanged():
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 60, 60))
    pixmap.set_rect(pixmap.irect, (200, 200, 200))
    page.insert_image(fitz.Rect(72, 72, 132, 132), pixmap=pixmap)
    buffer = io.BytesIO()
    doc.save(buffer)
    doc.close()

    with pytest.raises(pdfedit.PdfEditError) as excinfo:
        pdfedit.apply_edits(buffer.getvalue(), [("ИНН", "___")])

    assert "скан" in str(excinfo.value).lower()
