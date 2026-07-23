"""Договор ИУ: разбор реквизитов клиента и сборка готового PDF ПО ШАБЛОНУ ВЛАДЕЛЬЦА.

Владелец 23.07.2026: агент должен САМ заполнить шаблон реквизитами и прислать клиенту PDF на
согласование, а не ставить задачу «подготовить договор» и ждать человека.

Источник текста — документ «Шаблон договора ИУ» в папке базы знаний на Google Drive. Владелец
правит договор там, без деплоя; агент подставляет только то, что относится к Заказчику, номер и
дату. Зашивать текст в код нельзя: тогда правка шаблона ничего бы не меняла.

PDF собирается reportlab'ом прямо здесь. LibreOffice на прод не ставится намеренно: там 2 ГБ
памяти, а конвертация документа тянула бы за собой полный офисный пакет (правило «не перегружать
прод»). Шрифт — Liberation Serif, метрический аналог Times New Roman, уже есть в системе.
"""
from __future__ import annotations

import io
import os
import re
from html import escape
from pathlib import Path
from typing import Any

_FONT_DIRS = ("/usr/share/fonts/truetype/liberation", "/usr/share/fonts/truetype/dejavu",
              "C:/Windows/Fonts")
_FONT_CANDIDATES = (("LiberationSerif-Regular.ttf", "LiberationSerif-Bold.ttf"),
                    ("DejaVuSerif.ttf", "DejaVuSerif-Bold.ttf"),
                    ("times.ttf", "timesbd.ttf"))

FONT_MAIN = "ContractSerif"
FONT_BOLD = "ContractSerif-Bold"


def _register_fonts() -> bool:
    """Кириллический шрифт для PDF. Без него reportlab молча рисует кракозябры."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    if FONT_MAIN in pdfmetrics.getRegisteredFontNames():
        return True
    for regular, bold in _FONT_CANDIDATES:
        for d in _FONT_DIRS:
            reg, bld = Path(d) / regular, Path(d) / bold
            if reg.is_file() and bld.is_file():
                pdfmetrics.registerFont(TTFont(FONT_MAIN, str(reg)))
                pdfmetrics.registerFont(TTFont(FONT_BOLD, str(bld)))
                return True
    return False


# --- разбор реквизитов ---------------------------------------------------------------------
# Клиент присылает реквизиты свободным текстом, подписи у всех разные («Расчетный счет (р/с)»,
# «р/с», «Расчётный счёт»). Ключ ищем по нескольким вариантам, значение берём после двоеточия.
_FIELD_PATTERNS: dict[str, tuple[str, ...]] = {
    "name": (r"наименование", r"организац", r"компани", r"^ООО", r"^ИП\b"),
    "inn": (r"\bинн\b",),
    "kpp": (r"\bкпп\b",),
    "ogrn": (r"\bогрнип\b", r"\bогрн\b"),
    "address": (r"юридическ\w*\s+адрес", r"^адрес",),
    "account": (r"расч\w*\s*сч\w*", r"\bр/с\b", r"\bр\\с\b"),
    "corr_account": (r"корр\w*\s*сч\w*", r"\bк/с\b"),
    "bik": (r"\bбик\b",),
    "bank": (r"^банк", r"наименование\s+банка"),
    "director": (r"генеральн\w*\s+директор", r"^директор", r"руководител", r"\bв лице\b"),
    "phone": (r"телефон", r"\bтел\b"),
    "email": (r"e-?mail", r"почта"),
}
_INN_RE = re.compile(r"\b(\d{10}|\d{12})\b")
_ACCOUNT_RE = re.compile(r"\b(\d{20})\b")
_BIK_RE = re.compile(r"\b(\d{9})\b")

# Без этих полей договор подписывать нельзя: сторона не определена или платить некуда.
REQUIRED_FIELDS = ("name", "inn", "address", "account", "bank", "bik", "director")
FIELD_LABELS = {
    "name": "наименование организации", "inn": "ИНН", "kpp": "КПП", "ogrn": "ОГРН",
    "address": "юридический адрес", "account": "расчётный счёт", "corr_account": "корр. счёт",
    "bik": "БИК", "bank": "банк", "director": "ФИО руководителя", "phone": "телефон",
    "email": "e-mail",
}


def parse_requisites(text: str) -> dict[str, str]:
    """Реквизиты из свободного текста клиента в поля договора."""
    out: dict[str, str] = {}
    for raw_line in (text or "").splitlines():
        line = raw_line.strip().strip("•-—").strip()
        if not line:
            continue
        key, _, value = line.partition(":")
        value = value.strip()
        head = key.strip().lower()
        if not value:
            # Строка без двоеточия: единственная зацепка — само содержимое.
            if not out.get("name") and re.match(r'^(ООО|ЗАО|ПАО|АО|ИП)\b', line):
                out["name"] = line
            continue
        for field, patterns in _FIELD_PATTERNS.items():
            if field in out:
                continue
            if any(re.search(p, head, flags=re.I) for p in patterns):
                out[field] = value
                break
    # Подстраховка: если подписи были нестандартные, узнаём значения по их форме.
    flat = " ".join((text or "").split())
    if "inn" not in out and (m := _INN_RE.search(flat)):
        out["inn"] = m.group(1)
    if "account" not in out and (m := _ACCOUNT_RE.search(flat)):
        out["account"] = m.group(1)
    if "bik" not in out and (m := _BIK_RE.search(flat)):
        out["bik"] = m.group(1)
    return out


def missing_fields(requisites: dict[str, str]) -> list[str]:
    """Каких обязательных реквизитов не хватает — по-русски, чтобы спросить именно их."""
    return [FIELD_LABELS[f] for f in REQUIRED_FIELDS if not str(requisites.get(f) or "").strip()]


# --- шаблон владельца ----------------------------------------------------------------------
TEMPLATE_DOC_ID = os.getenv("CONTRACT_TEMPLATE_DOC_ID",
                            "1fZbcGZtEyXDJzZjxDidWh_Q5jo2G4i_pdovA8pJA5eA").strip()
# Заголовок, который база знаний приклеивает к тексту файла: в договор он попасть не должен.
_SOURCE_HEADER_RE = re.compile(
    r"^(?:Источник|Обновлено в Google Drive|Тип):.*$|^Таблица \d+\s*$|^\|\s*-{2,}.*$",
    re.MULTILINE)

# Плейсхолдер шаблона → поле разобранных реквизитов Заказчика.
CLIENT_PLACEHOLDERS = {
    "{НАЗВАНИЕ}": "name", "{ИНН}": "inn", "{КПП}": "kpp", "{ОГРН}": "ogrn",
    "{АДРЕС}": "address", "{РАСЧЁТНЫЙ СЧЁТ}": "account", "{РАСЧЕТНЫЙ СЧЁТ}": "account",
    "{КОРР. СЧЁТ}": "corr_account", "{БАНК}": "bank", "{БИК}": "bik",
    "{ФИО}": "director", "{ДОЛЖНОСТЬ}": "position", "{ОСНОВАНИЕ}": "basis",
    "{EMAIL}": "email", "{ТЕЛЕФОН}": "phone",
}
CLIENT_DEFAULTS = {"position": "Генеральный директор", "basis": "Устава"}


def load_template(fetch_text=None) -> str:
    """Текст шаблона договора из базы знаний. fetch_text — для тестов без Drive."""
    if fetch_text is None:
        from mcp import context_server as cs

        def fetch_text(doc_id: str) -> str:
            res = cs.TOOLS["get_company_file"]["handler"]({"google_file_id": doc_id})
            return str(res.get("content") or res.get("text") or "")
    text = fetch_text(TEMPLATE_DOC_ID)
    if not text.strip():
        raise RuntimeError(f"Шаблон договора ({TEMPLATE_DOC_ID}) пуст или недоступен.")
    return _SOURCE_HEADER_RE.sub("", text).strip()


def fill_template(template: str, client: dict[str, str], number: str, date: str,
                  executor: dict[str, Any] | None = None) -> str:
    """Подставить в шаблон номер, дату, реквизиты Заказчика и Исполнителя.

    Плейсхолдеры Заказчика ({ИНН}) и Исполнителя ({ИСПОЛНИТЕЛЬ — ИНН}) различаются префиксом,
    поэтому сначала подставляем длинные — иначе «{ИСПОЛНИТЕЛЬ — ИНН}» частично затёрся бы
    подстановкой «{ИНН}»."""
    ex = {**EXECUTOR_DEFAULTS, **(executor or {})}
    out = template
    # В шаблоне написано «ООО «{НАЗВАНИЕ}»», а клиент присылает наименование уже с формой
    # собственности — иначе вышло бы «ООО «ООО «Альфа Трейд»»». Снимаем обёртку у той стороны,
    # чья форма уже есть в реквизитах.
    for ph, party in (("{НАЗВАНИЕ}", client), ("{ИСПОЛНИТЕЛЬ — НАЗВАНИЕ}", ex)):
        if re.match(r"^(ООО|ЗАО|ПАО|АО|ИП)\b", str(party.get("name") or "").strip()):
            out = re.sub(r"(?:ООО|ЗАО|ПАО|АО)\s*«?" + re.escape(ph) + r"»?", ph, out)
    out = out.replace("{НОМЕР ДОГОВОРА}", number).replace("{ДАТА ДОГОВОРА}", date)
    out = out.replace("{ГОРОД}", str(ex.get("city") or ""))
    for ph, field in sorted(CLIENT_PLACEHOLDERS.items(), key=lambda kv: -len(kv[0])):
        ex_ph = ph.replace("{", "{ИСПОЛНИТЕЛЬ — ")
        out = out.replace(ex_ph, str(ex.get(field) or "").strip())
    for ph, field in sorted(CLIENT_PLACEHOLDERS.items(), key=lambda kv: -len(kv[0])):
        value = str(client.get(field) or CLIENT_DEFAULTS.get(field) or "").strip()
        out = out.replace(ph, value)
    return out


def unfilled_placeholders(text: str) -> list[str]:
    """Что в договоре осталось незаполненным — владелец должен знать до отправки клиенту."""
    return sorted(set(re.findall(r"\{[^}\n]{2,60}\}", text)))


EXECUTOR_DEFAULTS = {
    "name": os.getenv("CONTRACT_EXECUTOR_NAME", "{ИСПОЛНИТЕЛЬ — НАЗВАНИЕ}"),
    "inn": os.getenv("CONTRACT_EXECUTOR_INN", "{ИНН}"),
    "kpp": os.getenv("CONTRACT_EXECUTOR_KPP", "{КПП}"),
    "ogrn": os.getenv("CONTRACT_EXECUTOR_OGRN", "{ОГРН}"),
    "address": os.getenv("CONTRACT_EXECUTOR_ADDRESS", "{АДРЕС}"),
    "account": os.getenv("CONTRACT_EXECUTOR_ACCOUNT", "{РАСЧЁТНЫЙ СЧЁТ}"),
    "corr_account": os.getenv("CONTRACT_EXECUTOR_CORR", "{КОРР. СЧЁТ}"),
    "bik": os.getenv("CONTRACT_EXECUTOR_BIK", "{БИК}"),
    "bank": os.getenv("CONTRACT_EXECUTOR_BANK", "{БАНК}"),
    "director": os.getenv("CONTRACT_EXECUTOR_DIRECTOR", "{ФИО}"),
    "position": os.getenv("CONTRACT_EXECUTOR_POSITION", "Генеральный директор"),
    "basis": os.getenv("CONTRACT_EXECUTOR_BASIS", "Устава"),
    "city": os.getenv("CONTRACT_CITY", "Москва"),
}

CR, NL = chr(13), chr(10)


def parse_blocks(text: str) -> list[tuple[str, Any]]:
    """Текст шаблона → блоки для вёрстки: заголовок, абзац, таблица, разрыв страницы.

    Google Docs отдаёт таблицы строками «| ячейка | ячейка |», а переносы внутри ячейки — как
    возврат каретки. Раньше ячейки просто склеивались в один абзац, и блок реквизитов сторон превращался в
    нечитаемую кашу (владелец, 23.07.2026). Теперь это настоящие таблицы."""
    blocks: list[tuple[str, Any]] = []
    # Делим ТОЛЬКО по переводу строки: splitlines() режет ещё и по \r, а \r внутри ячейки —
    # это перенос внутри неё, а не конец строки таблицы. Из-за этого блок реквизитов
    # рассыпался на куски и переставал быть таблицей.
    for raw in text.replace("\r\n", NL).split(NL):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if any(cells):
                blocks.append(("table", [c.replace(CR, NL).strip() for c in cells]))
            continue
        line = line.lstrip("#").strip()
        if line.startswith("Приложение №"):
            blocks.append(("pagebreak", ""))
            blocks.append(("head", line))
            continue
        letters = [c for c in line if c.isalpha()]
        if letters and all(c.isupper() for c in letters) and len(line) < 120:
            blocks.append(("title" if "ДОГОВОР ВОЗМЕЗДНОГО" in line else "head", line))
        else:
            blocks.append(("body", line))
    return blocks


def _cell(text: str, style) -> Any:
    """Ячейка таблицы: переносы строк внутри неё должны остаться переносами."""
    from reportlab.platypus import Paragraph
    return Paragraph("<br/>".join(escape(part) for part in text.split(NL)), style)


def render_contract_pdf(number: str, date: str, client: dict[str, str],
                        executor: dict[str, Any] | None = None,
                        template: str | None = None) -> bytes:
    """Готовый договор в PDF: шаблон владельца + реквизиты сторон."""
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate,
                                    Spacer, Table, TableStyle)

    if not _register_fonts():
        raise RuntimeError("В системе нет кириллического шрифта для PDF "
                           "(искали Liberation Serif, DejaVu Serif, Times New Roman).")
    ex = {**EXECUTOR_DEFAULTS, **(executor or {})}
    body_text = fill_template(template if template is not None else load_template(),
                              client, number, date, ex)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=2 * cm, bottomMargin=2 * cm,
                            leftMargin=2.5 * cm, rightMargin=1.5 * cm,
                            title=f"Договор № {number}", author=str(ex.get("name") or ""))
    width = doc.width
    base = ParagraphStyle("base", fontName=FONT_MAIN, fontSize=11, leading=15.5,
                          alignment=TA_JUSTIFY, firstLineIndent=1.25 * cm, spaceAfter=5)
    head = ParagraphStyle("head", parent=base, fontName=FONT_BOLD, fontSize=11.5,
                          firstLineIndent=0, spaceBefore=12, spaceAfter=7, alignment=0)
    title = ParagraphStyle("title", parent=base, fontName=FONT_BOLD, fontSize=13.5,
                           alignment=TA_CENTER, firstLineIndent=0, spaceAfter=12)
    cell = ParagraphStyle("cell", parent=base, firstLineIndent=0, alignment=0, fontSize=10,
                          leading=13, spaceAfter=0)
    styles = {"title": title, "head": head, "body": base}

    story: list[Any] = []
    for kind, value in parse_blocks(body_text):
        if kind == "pagebreak":
            story.append(PageBreak())
        elif kind == "table":
            cols = len(value) or 1
            # Строка «город — дата»: одна короткая строка на две колонки, дату прижимаем вправо.
            short = cols == 2 and all(len(c) < 60 and NL not in c for c in value)
            right = ParagraphStyle("right", parent=cell, alignment=2)
            cells = [_cell(value[0], cell),
                     _cell(value[1], right if short else cell)] if cols == 2 else [
                _cell(v, cell) for v in value]
            table = Table([cells], colWidths=[width / cols] * cols)
            table.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (0, -1), 0),
                ("RIGHTPADDING", (-1, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]))
            story.append(table if short else KeepTogether(table))
            story.append(Spacer(1, 8))
        else:
            story.append(Paragraph(escape(value), styles[kind]))
    doc.build(story)
    return buf.getvalue()
