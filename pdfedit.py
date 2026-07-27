"""pdfedit.py — точечная правка текста прямо в PDF, без пересборки документа.

Продолжение линии `docedit`: правим ИСХОДНЫЕ байты, а не пересобираем файл из пересказа.
Для PDF это принципиально. Круговая конвертация PDF → Word → PDF всегда перевёрстывает
документ заново: съезжают отступы, разваливаются таблицы, меняются переносы — «точно
такой же файл, только с правкой» так получить нельзя. Здесь вместо этого стирается ровно
тот фрагмент, который нужно заменить, и на его место — на ту же базовую линию, тем же
кеглем и цветом — вписывается новый текст. Всё остальное в файле остаётся нетронутым.

Шрифт для вставки берётся не из документа: внутри PDF шрифты лежат подмножествами
(subset) с переиндексированными глифами — вписать ими новую строку нельзя, вместо
кириллицы получится пустота. Поэтому подбирается системный шрифт того же семейства;
Liberation Serif/Sans метрически совместимы с Times New Roman/Arial, поэтому вставка
совпадает с оригиналом по ширине символов.

apply_edits() возвращает (новые байты, число замен по каждой правке, предупреждения) и
никогда не меняет вход. Не подошедшая по ширине замена — предупреждение, а не молчание:
текст, вылезший за поля, читатель заметит уже в подписанном договоре.
"""
from __future__ import annotations

import io
import logging
import os
import re

log = logging.getLogger(__name__)

MAX_PDF_BYTES = int(os.getenv("PDFEDIT_MAX_BYTES", str(40 * 1024 * 1024)) or str(40 * 1024 * 1024))
MAX_PAGES = int(os.getenv("PDFEDIT_MAX_PAGES", "400") or "400")

#: Системные шрифты, которыми вписывается новый текст. Liberation метрически совместим
#: с Times New Roman / Arial, DejaVu — запасной вариант с широким кириллическим покрытием.
_FONT_DIRS = (
    "/usr/share/fonts/truetype/liberation",
    "/usr/share/fonts/truetype/dejavu",
    "C:/Windows/Fonts",
)
_FONT_FILES = {
    ("serif", False, False): ("LiberationSerif-Regular.ttf", "DejaVuSerif.ttf", "times.ttf"),
    ("serif", True, False): ("LiberationSerif-Bold.ttf", "DejaVuSerif-Bold.ttf", "timesbd.ttf"),
    ("serif", False, True): ("LiberationSerif-Italic.ttf", "DejaVuSerif-Italic.ttf", "timesi.ttf"),
    ("serif", True, True): ("LiberationSerif-BoldItalic.ttf", "DejaVuSerif-BoldItalic.ttf", "timesbi.ttf"),
    ("sans", False, False): ("LiberationSans-Regular.ttf", "DejaVuSans.ttf", "arial.ttf"),
    ("sans", True, False): ("LiberationSans-Bold.ttf", "DejaVuSans-Bold.ttf", "arialbd.ttf"),
    ("sans", False, True): ("LiberationSans-Italic.ttf", "DejaVuSans-Oblique.ttf", "ariali.ttf"),
    ("sans", True, True): ("LiberationSans-BoldItalic.ttf", "DejaVuSans-BoldOblique.ttf", "arialbi.ttf"),
    ("mono", False, False): ("LiberationMono-Regular.ttf", "DejaVuSansMono.ttf", "cour.ttf"),
    ("mono", True, False): ("LiberationMono-Bold.ttf", "DejaVuSansMono-Bold.ttf", "courbd.ttf"),
    ("mono", False, True): ("LiberationMono-Italic.ttf", "DejaVuSansMono-Oblique.ttf", "couri.ttf"),
    ("mono", True, True): ("LiberationMono-BoldItalic.ttf", "DejaVuSansMono-BoldOblique.ttf", "courbi.ttf"),
}
_MONO_MARKERS = ("mono", "courier", "consol")
_SANS_MARKERS = ("arial", "helvetica", "sans", "verdana", "tahoma", "calibri", "segoe")


class PdfEditError(Exception):
    """Правку нельзя применить, и продолжать нельзя — причина в сообщении."""


def _family(font_name: str) -> str:
    name = (font_name or "").lower()
    if any(marker in name for marker in _MONO_MARKERS):
        return "mono"
    if any(marker in name for marker in _SANS_MARKERS):
        return "sans"
    return "serif"


def font_path(font_name: str, *, bold: bool, italic: bool) -> str | None:
    """Файл системного шрифта, ближайшего к шрифту исходного фрагмента."""

    candidates = _FONT_FILES.get((_family(font_name), bold, italic), ())
    for directory in _FONT_DIRS:
        for candidate in candidates:
            path = os.path.join(directory, candidate)
            if os.path.isfile(path):
                return path
    # Семейство/начертание не нашлись — берём хоть что-то с кириллицей.
    for directory in _FONT_DIRS:
        for candidate in ("LiberationSerif-Regular.ttf", "DejaVuSerif.ttf", "DejaVuSans.ttf", "times.ttf"):
            path = os.path.join(directory, candidate)
            if os.path.isfile(path):
                return path
    return None


def _normalized(text: str) -> str:
    """Текст без переносов строк и повторных пробелов — так его видит человек."""

    return re.sub(r"\s+", " ", (text or "").replace("\u00ad", "")).strip()


def _span_style(page, rect) -> dict:
    """Кегль, цвет, шрифт и базовая линия фрагмента, попавшего в прямоугольник."""

    import fitz

    best = None
    best_overlap = 0.0
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                overlap = (fitz.Rect(span["bbox"]) & rect).get_area()
                if overlap > best_overlap:
                    best_overlap, best = overlap, span
    if best is None:
        return {"size": 11.0, "color": 0, "font": "", "origin_y": rect.y1 - rect.height * 0.22}
    flags = int(best.get("flags") or 0)
    return {
        "size": float(best.get("size") or 11.0),
        "color": int(best.get("color") or 0),
        "font": str(best.get("font") or ""),
        # Базовая линия исходного текста: вставка на неё ложится без вертикального сдвига.
        "origin_y": float(best.get("origin", (0, rect.y1))[1]),
        "bold": bool(flags & 2 ** 4) or "bold" in str(best.get("font", "")).lower(),
        "italic": bool(flags & 2 ** 1) or "italic" in str(best.get("font", "")).lower(),
    }


def _color_tuple(color: int) -> tuple[float, float, float]:
    return (
        ((color >> 16) & 255) / 255.0,
        ((color >> 8) & 255) / 255.0,
        (color & 255) / 255.0,
    )


def _line_groups(rects: list) -> list[list]:
    """Прямоугольники одного вхождения группируются по строкам.

    Поиск отдаёт фразу кусками: перенос строки, изменение начертания и даже широкий
    пробел рвут её на части. Для замены важна первая строка вхождения — туда встаёт
    новый текст, остальные куски просто стираются.
    """

    groups: list[list] = []
    for rect in sorted(rects, key=lambda r: (round(r.y0, 1), r.x0)):
        if groups and abs(groups[-1][-1].y0 - rect.y0) < 3 and rect.x0 >= groups[-1][-1].x0 - 1:
            groups[-1].append(rect)
        else:
            groups.append([rect])
    return groups


def apply_edits(
    data: bytes,
    edits: list[tuple[str, str]],
    *,
    file_name: str = "document.pdf",
) -> tuple[bytes, list[int], list[str]]:
    """Заменить точные фрагменты текста в PDF, сохранив всё остальное оформление."""

    try:
        import fitz  # PyMuPDF
    except ImportError as exc:  # pragma: no cover - на сервере пакет установлен
        raise PdfEditError(
            "Для правки PDF нужен пакет pymupdf — он не установлен в окружении."
        ) from exc

    if not data:
        raise PdfEditError("Файл пустой.")
    if len(data) > MAX_PDF_BYTES:
        raise PdfEditError(
            f"PDF больше допустимых {MAX_PDF_BYTES // (1024 * 1024)} МБ — править такой файл не берусь."
        )

    counts = [0] * len(edits)
    warnings: list[str] = []

    try:
        document = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001 - битый файл не должен выглядеть как сбой сервера
        raise PdfEditError(
            "Файл не открывается как PDF — он повреждён или это другой формат."
        ) from exc

    with document as doc:
        if doc.is_encrypted and not doc.authenticate(""):
            raise PdfEditError("PDF защищён паролем — снимите защиту и пришлите файл заново.")
        if doc.page_count > MAX_PAGES:
            raise PdfEditError(f"В PDF {doc.page_count} страниц — это больше предела в {MAX_PAGES}.")
        if not any(page.get_text().strip() for page in doc):
            # Скан — это картинка страницы: искать в нём текст нечем, и «ничего не нашлось»
            # выглядело бы как неудачная правка, а не как неподходящий файл.
            raise PdfEditError(
                "В этом PDF нет текстового слоя — похоже, это скан. "
                "Править можно только PDF с текстом: попросите исходный документ."
            )

        for page in doc:
            insertions: list[tuple] = []
            page_text = _normalized(page.get_text())

            for index, (find, replace) in enumerate(edits):
                needle = _normalized(find)
                if not needle:
                    continue
                counts[index] += page_text.count(needle)
                rects = page.search_for(find) or page.search_for(needle)
                if not rects:
                    continue
                for group in _line_groups(rects):
                    for rect in group:
                        page.add_redact_annot(rect)
                    if replace:
                        insertions.append((group[0], replace, index))

            if not insertions and not page.annots(types=[fitz.PDF_ANNOT_REDACT]):
                continue

            styles = [(_span_style(page, rect), rect, text, index) for rect, text, index in insertions]
            # Текст стирается один раз на всю страницу: линии таблиц и картинки не трогаем,
            # иначе вместе с реквизитами исчезнут рамки и печати.
            page.apply_redactions(
                images=fitz.PDF_REDACT_IMAGE_NONE,
                graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                text=fitz.PDF_REDACT_TEXT_REMOVE,
            )

            for style, rect, text, index in styles:
                path = font_path(style["font"], bold=style.get("bold", False), italic=style.get("italic", False))
                if path is None:
                    warnings.append(
                        "В системе нет ни одного шрифта с кириллицей — новый текст вписать нечем."
                    )
                    continue
                alias = "pdfedit-" + os.path.splitext(os.path.basename(path))[0].lower()
                size = style["size"]
                font = fitz.Font(fontfile=path)
                available = page.rect.x1 - rect.x0 - 28  # правое поле страницы
                width = font.text_length(text, fontsize=size)
                if width > available:
                    shrunk = max(size * 0.8, size * available / width) if width else size
                    warnings.append(
                        f"«{text[:40]}» шире освободившегося места на стр. {page.number + 1}: "
                        f"кегль уменьшен с {size:.1f} до {shrunk:.1f}. Проверьте вёрстку этой строки."
                    )
                    size = shrunk
                page.insert_font(fontname=alias, fontfile=path)
                page.insert_text(
                    fitz.Point(rect.x0, style["origin_y"]),
                    text,
                    fontname=alias,
                    fontsize=size,
                    color=_color_tuple(style["color"]),
                )

        buffer = io.BytesIO()
        doc.save(buffer, garbage=3, deflate=True)

    if not any(counts):
        log.info("pdfedit: ни одна правка не совпала в %s", file_name)
    return buffer.getvalue(), counts, warnings


def extract_text(data: bytes) -> str:
    """Текст PDF для проверки результата и для показа агенту.

    Неразрывные пробелы MuPDF приводятся к обычным: агент возвращает этот текст обратно
    в правку буквальной строкой, и невидимая глазом разница ломала бы поиск.
    """

    import fitz

    with fitz.open(stream=data, filetype="pdf") as doc:
        return "\n".join(page.get_text() for page in doc).replace(" ", " ")
