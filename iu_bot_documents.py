"""PDF-вложения клиентского бота из единой базы знаний."""
from __future__ import annotations

import hashlib
import io
import os
import re
import threading
import time
from html import escape
from typing import Any

_TTL_SECONDS = float(os.getenv("IU_BOT_PDF_CACHE_SECONDS", "300") or 300)
_cache: dict[str, tuple[float, bytes]] = {}
_attachment_cache: dict[str, tuple[str, dict[str, Any]]] = {}
_lock = threading.Lock()


def _company_document(name: str) -> str:
    from mcp import context_server as cs

    listed = cs.TOOLS["list_company_files"]["handler"]({"limit": 300}) or {}
    items = listed.get("files") or listed.get("items") or []
    wanted = name.casefold()
    match = next(
        (
            item
            for item in items
            if wanted in str(item.get("name") or "").casefold()
            and item.get("google_file_id")
        ),
        None,
    )
    if not match:
        raise RuntimeError(f"В базе знаний нет документа «{name}».")
    result = cs.TOOLS["get_company_file"]["handler"](
        {"google_file_id": match["google_file_id"]}
    ) or {}
    body = str(result.get("content") or result.get("text") or "")
    body = re.sub(
        r"^(?:Источник|Обновлено в Google Drive|Тип):.*$",
        "",
        body,
        flags=re.MULTILINE,
    ).strip()
    if not body:
        raise RuntimeError(f"Документ «{name}» пуст.")
    return body


def source_text(kind: str) -> str:
    if kind == "terms":
        import tg_agent

        return tg_agent.terms_text()
    if kind == "contract":
        import contract

        # Это именно пример: юридический текст берётся из утверждённого шаблона, а
        # реквизиты конкретного клиента не подставляются.
        try:
            body = contract.load_template()
        except Exception:
            # Шаблон может быть временно исключён из зеркала базы знаний, оставаясь
            # доступным владельцу на Drive. Читаем тот же утверждённый Google Doc,
            # а не подменяем юридический текст встроенной копией.
            from mcp import context_server as cs

            url = (
                "https://docs.google.com/document/d/"
                f"{contract.TEMPLATE_DOC_ID}/edit"
            )
            fetched = cs.TOOLS["fetch_url"]["handler"](
                {"url": url, "max_chars": 200000}
            ) or {}
            body = str(fetched.get("text") or "")
            body = contract._SOURCE_HEADER_RE.sub("", body).strip()
            if not body:
                raise RuntimeError("Утверждённый шаблон договора недоступен.")
        blank = "________________"
        client = {
            field: blank
            for field in set(contract.CLIENT_PLACEHOLDERS.values())
        }
        return contract.fill_template(
            body,
            client,
            "ПРИМЕР",
            "«___» __________ 20__ г.",
        )
    if kind == "faq":
        import iu_runtime

        return _company_document(iu_runtime.QA_DOC_NAME)
    raise ValueError(f"Неизвестный PDF ИУ: {kind}")


def render_pdf(title: str, body: str) -> bytes:
    import contract
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    if not contract._register_fonts():
        raise RuntimeError("Не найден кириллический шрифт для PDF.")
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=1.8 * cm,
        bottomMargin=1.8 * cm,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        title=title,
        author="Albery",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "iu-title",
        parent=styles["Title"],
        fontName=contract.FONT_BOLD,
        fontSize=15,
        leading=19,
        alignment=TA_CENTER,
        spaceAfter=16,
    )
    body_style = ParagraphStyle(
        "iu-body",
        parent=styles["BodyText"],
        fontName=contract.FONT_MAIN,
        fontSize=10.5,
        leading=15,
        spaceAfter=8,
    )
    story: list[Any] = [Paragraph(escape(title), title_style)]
    for block in re.split(r"\n\s*\n", str(body or "").strip()):
        clean = "<br/>".join(escape(line) for line in block.splitlines())
        if clean:
            story.extend((Paragraph(clean, body_style), Spacer(1, 3)))
    doc.build(story)
    return buffer.getvalue()


def pdf_bytes(kind: str) -> bytes:
    now = time.monotonic()
    with _lock:
        cached = _cache.get(kind)
        if cached and now - cached[0] < _TTL_SECONDS:
            return cached[1]
    titles = {
        "terms": "Условия присоединения к ИУ",
        "contract": "Примерный договор ИУ",
        "faq": "Ответы на частые вопросы",
    }
    data = render_pdf(titles[kind], source_text(kind))
    with _lock:
        _cache[kind] = (now, data)
    return data


def attachment(kind: str) -> dict[str, Any]:
    import funnel_workspace_uploads as uploads

    names = {
        "terms": "Условия присоединения к ИУ.pdf",
        "contract": "Примерный договор ИУ.pdf",
        "faq": "Ответы на частые вопросы.pdf",
    }
    data = pdf_bytes(kind)
    digest = hashlib.sha256(data).hexdigest()
    with _lock:
        cached = _attachment_cache.get(kind)
    if cached and cached[0] == digest:
        try:
            uploads.resolve_upload(cached[1]["token"])
            return dict(cached[1])
        except uploads.UploadError:
            pass
    descriptor = uploads.store_upload(
        io.BytesIO(data),
        file_name=names[kind],
        mime_type="application/pdf",
    )
    with _lock:
        _attachment_cache[kind] = (digest, descriptor)
    return descriptor
