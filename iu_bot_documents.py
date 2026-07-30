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
_MAX_PDF_BYTES = int(os.getenv("IU_BOT_PDF_MAX_BYTES", "20000000") or 20000000)
CONTRACT_DOCUMENT_NAME = os.getenv(
    "IU_BOT_CONTRACT_DOCUMENT",
    "Договор оферты",
).strip()
FAQ_DOCUMENT_NAME = os.getenv(
    "IU_BOT_FAQ_DOCUMENT",
    "Ответы на частые вопросы",
).strip()
_cache: dict[str, tuple[float, bytes]] = {}
_attachment_cache: dict[str, tuple[str, dict[str, Any]]] = {}
_lock = threading.Lock()


def _company_file(name: str) -> dict[str, Any] | None:
    from mcp import context_server as cs

    listed = cs.TOOLS["list_company_files"]["handler"](
        {"limit": 300, "include_empty": True}
    ) or {}
    items = listed.get("files") or listed.get("items") or []
    wanted = name.strip().casefold()
    matches = [
        item
        for item in items
        if str(item.get("name") or "").strip().casefold() == wanted
        and item.get("google_file_id")
    ]
    if len(matches) > 1:
        raise RuntimeError(f"В базе знаний несколько документов «{name}».")
    return dict(matches[0]) if matches else None


def _company_document(name: str) -> str:
    from mcp import context_server as cs

    match = _company_file(name)
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


def _drive_client() -> Any:
    from googleapiclient.discovery import build
    from mcp import context_server as cs

    return build(
        "drive",
        "v3",
        credentials=cs._google_creds_for_fetch(),
        cache_discovery=False,
    )


def _drive_pdf_source(name: str) -> tuple[Any, dict[str, Any]]:
    """Найти ровно один утверждённый файл и вернуть Drive-клиент с метаданными.

    Google Docs попадают в зеркало базы знаний вместе с текстом. Загруженный PDF
    договора текста в зеркале не имеет, поэтому для него допустим точный поиск по
    имени в Drive. Подстроки не принимаются: архив или черновик нельзя отправить
    клиенту вместо утверждённого документа.
    """

    drive = _drive_client()
    mirrored = _company_file(name)
    if mirrored:
        metadata = drive.files().get(
            fileId=str(mirrored["google_file_id"]),
            fields="id,name,mimeType,size,modifiedTime",
            supportsAllDrives=True,
        ).execute()
        return drive, dict(metadata or {})

    # Загруженный PDF ещё не имеет текстовой записи в зеркале. Ограничиваем
    # поиск родительской папкой уже синхронизированного клиентского FAQ, чтобы
    # одноимённый файл из личного Drive или архива никогда не ушёл клиенту.
    anchor = _company_file(FAQ_DOCUMENT_NAME)
    if not anchor:
        raise RuntimeError(
            f"Не удалось подтвердить папку базы знаний для файла «{name}»."
        )
    anchor_metadata = drive.files().get(
        fileId=str(anchor["google_file_id"]),
        fields="parents",
        supportsAllDrives=True,
    ).execute()
    parent_ids = [
        str(parent).strip()
        for parent in (anchor_metadata.get("parents") or [])
        if str(parent).strip()
    ]
    if not parent_ids:
        raise RuntimeError(
            f"У клиентского FAQ нет папки базы знаний; файл «{name}» не выбран."
        )
    escaped = name.replace("\\", "\\\\").replace("'", "\\'")
    parent_query = " or ".join(f"'{parent}' in parents" for parent in parent_ids)
    response = drive.files().list(
        q=f"name = '{escaped}' and trashed = false and ({parent_query})",
        fields="files(id,name,mimeType,size,modifiedTime)",
        pageSize=20,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    wanted = name.strip().casefold()
    matches = [
        dict(item)
        for item in (response.get("files") or [])
        if str(item.get("name") or "").strip().casefold() == wanted
    ]
    if not matches:
        raise RuntimeError(f"В базе знаний нет файла «{name}».")
    if len(matches) > 1:
        raise RuntimeError(f"В Google Drive несколько файлов «{name}».")
    return drive, matches[0]


def _original_pdf_bytes(name: str) -> bytes:
    drive, metadata = _drive_pdf_source(name)
    file_id = str(metadata.get("id") or "").strip()
    mime_type = str(metadata.get("mimeType") or "").strip().lower()
    if not file_id:
        raise RuntimeError(f"У файла «{name}» нет Google ID.")
    if mime_type == "application/vnd.google-apps.document":
        data = drive.files().export(
            fileId=file_id,
            mimeType="application/pdf",
        ).execute()
    elif mime_type == "application/pdf":
        data = drive.files().get_media(
            fileId=file_id,
            supportsAllDrives=True,
        ).execute()
    else:
        raise RuntimeError(
            f"Файл «{name}» имеет неподдерживаемый тип {mime_type or 'без типа'}."
        )
    if not isinstance(data, bytes):
        data = bytes(data or b"")
    if len(data) > _MAX_PDF_BYTES:
        raise RuntimeError(f"PDF «{name}» больше допустимых {_MAX_PDF_BYTES} байт.")
    if not data.startswith(b"%PDF-"):
        raise RuntimeError(f"Google Drive не вернул корректный PDF «{name}».")
    return data


def source_text(kind: str) -> str:
    if kind == "terms":
        import tg_agent

        return tg_agent.terms_text()
    if kind == "contract":
        return _company_document(CONTRACT_DOCUMENT_NAME)
    if kind == "faq":
        return _company_document(FAQ_DOCUMENT_NAME)
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
    if kind == "contract":
        data = _original_pdf_bytes(CONTRACT_DOCUMENT_NAME)
    elif kind == "faq":
        data = _original_pdf_bytes(FAQ_DOCUMENT_NAME)
    elif kind == "terms":
        data = render_pdf("Условия присоединения к ИУ", source_text(kind))
    else:
        raise ValueError(f"Неизвестный PDF ИУ: {kind}")
    with _lock:
        _cache[kind] = (now, data)
    return data


def attachment(kind: str) -> dict[str, Any]:
    import funnel_workspace_uploads as uploads

    names = {
        "terms": "Условия присоединения к ИУ.pdf",
        "contract": "Договор оферты.pdf",
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
