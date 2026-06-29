"""Pure Google Drive document helpers for Albery.

The helpers in this module intentionally avoid network calls, database access,
Flask state, and secrets.  They only normalize Google Drive payload fragments
that app.py receives from Apps Script / Google Drive sync code.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def google_drive_path_from_parts(value: Any, file_name: str | None = None) -> str:
    if isinstance(value, list):
        parts = [str(part).strip() for part in value if str(part).strip()]
    else:
        raw = str(value or "").strip()
        parts = [part.strip() for part in raw.split("/") if part.strip()] if raw else []
    if file_name:
        parts = [*parts, file_name]
    return " / ".join(parts)


def google_drive_document_content(document: dict[str, Any]) -> str:
    source_url = str(document.get("url") or "").strip()
    updated_at = str(document.get("updated_at") or "").strip()
    mime_type = str(document.get("mime_type") or "").strip()
    content = str(document.get("content") or "")
    header = [
        f"Источник: {source_url}" if source_url else "",
        f"Обновлено в Google Drive: {updated_at}" if updated_at else "",
        f"Тип: {mime_type}" if mime_type else "",
    ]
    header_text = "\n".join(item for item in header if item)
    return f"{header_text}\n\n{content}".strip() if header_text else content


def google_drive_document_structured_content(document: dict[str, Any]) -> str:
    blocks = document.get("blocks")
    if not isinstance(blocks, list):
        return google_drive_document_content(document)

    chunks: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "")
        if block_type == "heading":
            text = str(block.get("text") or "").strip()
            if text:
                chunks.append(f"# {text}")
        elif block_type == "paragraph":
            text = str(block.get("text") or "").strip()
            if text:
                chunks.append(text)
        elif block_type == "list_item":
            text = str(block.get("text") or "").strip()
            if text:
                chunks.append(f"- {text}")
        elif block_type == "table":
            title = str(block.get("title") or "Таблица").strip()
            markdown = str(block.get("markdown") or "").strip()
            headers = block.get("headers") if isinstance(block.get("headers"), list) else []
            rows = block.get("rows") if isinstance(block.get("rows"), list) else []
            records = block.get("records") if isinstance(block.get("records"), list) else []
            table_parts = [title]
            if markdown:
                table_parts.append(markdown)
            if headers and rows:
                header_text = [str(item or "").strip() or f"Колонка {index + 1}" for index, item in enumerate(headers)]
                exact_rows: list[str] = []
                for row_index, row in enumerate(rows, start=1):
                    if not isinstance(row, list):
                        continue
                    cells = []
                    width = max(len(header_text), len(row))
                    for cell_index in range(width):
                        header = header_text[cell_index] if cell_index < len(header_text) else f"Колонка {cell_index + 1}"
                        value = str(row[cell_index] if cell_index < len(row) else "").replace("<br>", "\n").strip()
                        cells.append(f"{header}: {value if value else '∅'}")
                    exact_rows.append(f"Строка {row_index}: " + " | ".join(cells))
                if exact_rows:
                    table_parts.append("Точная структура строк и столбцов:\n" + "\n".join(exact_rows))
            record_parts: list[str] = []
            for index, record in enumerate(records, start=1):
                if not isinstance(record, dict):
                    continue
                lines = [f"- {key}: {value}" for key, value in record.items() if str(value).strip()]
                if lines:
                    record_parts.append(f"Запись {index}:\n" + "\n".join(lines))
            if record_parts:
                table_parts.append("\n\n".join(record_parts))
            chunks.append("\n\n".join(part for part in table_parts if part))

    structured = "\n\n".join(chunks).strip()
    if not structured:
        structured = str(document.get("content") or "")

    source_url = str(document.get("url") or "").strip()
    updated_at = str(document.get("updated_at") or "").strip()
    mime_type = str(document.get("mime_type") or "").strip()
    header = [
        f"Источник: {source_url}" if source_url else "",
        f"Обновлено в Google Drive: {updated_at}" if updated_at else "",
        f"Тип: {mime_type}" if mime_type else "",
    ]
    header_text = "\n".join(item for item in header if item)
    return f"{header_text}\n\n{structured}".strip() if header_text else structured


def parse_google_timestamp(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def google_timestamp_for_apps_script(value: Any) -> str:
    if not isinstance(value, datetime):
        return ""
    current = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    return current.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
