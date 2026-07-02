"""Google Drive integration: OAuth-user credentials, company-documents and
call-transcripts sync, folder organization/classification, Apps Script
timestamps, integration sync status and the inbound Drive webhook.

Moved verbatim out of app.py (2026-07-02 refactor, step Sh2.5 - move-only).
Registers its routes on the shared Flask `app` at import time; shares the
zoom-transcript helpers with zoom.py.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re

from datetime import datetime
from datetime import timezone
from flask import jsonify
from flask import request
from psycopg.types.json import Jsonb
from typing import Any
import requests

from config import (
    MSK_TZ,
)

from utils import (
    RU_MONTH_NAMES,
    format_datetime_msk_label,
    iso_or_none,
    parse_datetime,
)

from zoom import (
    ZOOM_SPEAKER_NOISE_NAMES,
    ZOOM_TECHNICAL_PARTICIPANT_NAMES,
    ensure_zoom_schema,
    zoom_call_row_payload,
)

from app import (  # shared Flask app + db glue still living in app.py
    app,
    pg_connect,
    pg_json,
    pg_table_exists,
    postgres_enabled,
)


def ensure_company_profile_schema() -> None:
    with pg_connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                required_tables = ("company_profile", "company_folders", "company_drive_sources", "company_drive_folders")
                for table_name in required_tables:
                    cur.execute("SELECT to_regclass(%s) IS NOT NULL AS exists", (f"public.{table_name}",))
                    row = cur.fetchone()
                    if not row or not row["exists"]:
                        raise RuntimeError(
                            f"PostgreSQL table public.{table_name} is missing. "
                            "Apply database migrations before using company folders."
                        )
                cur.execute(
                    """
                    INSERT INTO company_profile (profile_key, title, content)
                    VALUES ('main', 'О компании', '')
                    ON CONFLICT (profile_key) DO NOTHING
                    """
                )
def google_drive_company_sync_config() -> tuple[str, str]:
    sync_url = os.getenv("GOOGLE_APPS_SCRIPT_SYNC_URL", "").strip()
    token = os.getenv("GOOGLE_APPS_SCRIPT_SYNC_TOKEN", "").strip()
    if not sync_url:
        raise ValueError("Укажите GOOGLE_APPS_SCRIPT_SYNC_URL в .env")
    if not token:
        raise ValueError("Укажите GOOGLE_APPS_SCRIPT_SYNC_TOKEN в .env")
    return sync_url, token
def _google_user_credentials() -> Any:
    """Load the albery agent's Google OAuth user credentials from the secure token paths and
    refresh if needed. Requires the google-auth libraries in the venv."""
    from google.oauth2.credentials import Credentials
    import google.auth.transport.requests as _gtr

    last_err: Exception | None = None
    for path in ("/root/.hermes/secure/google_oauth_token.json", "/root/.hermes/google_token.json"):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except OSError as exc:
            last_err = exc
            continue
        creds = Credentials.from_authorized_user_info(data, data.get("scopes"))
        if not creds.valid:
            creds.refresh(_gtr.Request())
        return creds
    raise RuntimeError(f"Google OAuth token не найден в secure store ({last_err})")
def _share_drive_anyone(drive, file_id, role: str = "writer") -> str:
    """Share a Drive item for anyone-with-the-link. Tries `role` then 'reader'. Returns granted role or ''."""
    if not file_id:
        return ""
    for r in (role if role in ("writer", "reader") else "writer", "reader"):
        try:
            drive.permissions().create(fileId=str(file_id), body={"type": "anyone", "role": r}).execute()
            return r
        except Exception:
            continue
    return ""
def share_drive_item_for_everyone(item: str, role: str = "writer") -> dict[str, Any]:
    """Open ANY Drive item (sheet/doc/folder/file/Apps Script) for anyone-with-the-link. Accepts id or URL."""
    from googleapiclient.discovery import build
    fid = _extract_drive_folder_id(item)
    if not fid:
        raise ValueError("item (Drive id or URL) is required")
    drive = build("drive", "v3", credentials=_google_user_credentials(), cache_discovery=False)
    granted = _share_drive_anyone(drive, fid, role)
    try:
        meta = drive.files().get(fileId=fid, fields="id,name,mimeType,webViewLink").execute()
    except Exception:
        meta = {"id": fid}
    return {
        "id": fid,
        "name": meta.get("name"),
        "web_view_link": meta.get("webViewLink"),
        "shared_role": granted or "none",
        "access": ("anyone_with_link_" + ("editor" if granted == "writer" else "viewer")) if granted else "share_failed",
    }
def _extract_drive_folder_id(value: Any) -> str:
    """Accept a bare Drive folder id or a folder/file URL and return the id."""
    import re as _re
    s = str(value or "").strip()
    if not s:
        return ""
    for pat in (r"/folders/([A-Za-z0-9_-]+)", r"[?&]id=([A-Za-z0-9_-]+)", r"/d/([A-Za-z0-9_-]+)"):
        m = _re.search(pat, s)
        if m:
            return m.group(1)
    return s
def move_drive_file_to_folder(file_id: str, folder: str) -> dict[str, Any]:
    """Move a Drive item (file, spreadsheet, document or folder) into the given Drive folder.

    Drive folders are files with a folder mimeType, so the same parents API works for both
    ordinary files and nested folders. We keep the old function name for MCP compatibility.
    """
    from googleapiclient.discovery import build
    creds = _google_user_credentials()
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    fid = _extract_drive_folder_id(folder)
    item_id = _extract_drive_folder_id(file_id)
    if not item_id:
        raise ValueError("file_id is required (Drive file/folder id or URL)")
    if not fid:
        raise ValueError("folder is required (Drive folder id or URL)")
    meta = drive.files().get(fileId=str(item_id), fields="id,name,mimeType,parents").execute()
    prev_parents = meta.get("parents", []) or []
    prev = ",".join(prev_parents)
    updated = drive.files().update(
        fileId=str(item_id), addParents=fid, removeParents=prev, fields="id,name,mimeType,parents",
    ).execute()
    return {
        "file_id": str(item_id),
        "item_id": str(item_id),
        "item_name": meta.get("name"),
        "mime_type": meta.get("mimeType"),
        "folder_id": fid,
        "previous_parents": prev_parents,
        "parents": updated.get("parents", []),
        "moved": True,
    }
def remove_drive_item_from_folder(item_id: str, folder: str) -> dict[str, Any]:
    """Remove a Drive item (file or folder) from one concrete parent folder without deleting it."""
    from googleapiclient.discovery import build
    creds = _google_user_credentials()
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    clean_item_id = _extract_drive_folder_id(item_id)
    parent_id = _extract_drive_folder_id(folder)
    if not clean_item_id:
        raise ValueError("item_id is required (Drive file/folder id or URL)")
    if not parent_id:
        raise ValueError("folder is required (Drive folder id or URL)")
    meta = drive.files().get(fileId=str(clean_item_id), fields="id,name,mimeType,parents").execute()
    parents = meta.get("parents", []) or []
    if parent_id not in parents:
        return {
            "item_id": str(clean_item_id),
            "item_name": meta.get("name"),
            "mime_type": meta.get("mimeType"),
            "folder_id": parent_id,
            "previous_parents": parents,
            "parents": parents,
            "removed": False,
            "reason": "item is not in the specified folder",
        }
    updated = drive.files().update(
        fileId=str(clean_item_id), removeParents=parent_id, fields="id,name,mimeType,parents",
    ).execute()
    return {
        "item_id": str(clean_item_id),
        "item_name": meta.get("name"),
        "mime_type": meta.get("mimeType"),
        "folder_id": parent_id,
        "previous_parents": parents,
        "parents": updated.get("parents", []),
        "removed": True,
    }
def _drive_q(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace("'", "\\'")
def _drive_public_meta(item: dict[str, Any]) -> dict[str, Any]:
    mime = item.get("mimeType") or ""
    return {
        "id": item.get("id"), "name": item.get("name"), "mime_type": mime,
        "is_folder": mime == "application/vnd.google-apps.folder",
        "parents": item.get("parents", []) or [], "web_view_link": item.get("webViewLink"),
    }
def list_drive_folder_items(folder: str, page_size: int = 200) -> dict[str, Any]:
    from googleapiclient.discovery import build
    creds = _google_user_credentials()
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    folder_id = _extract_drive_folder_id(folder)
    if not folder_id:
        raise ValueError("folder is required (Drive folder id or URL)")
    parent = drive.files().get(fileId=str(folder_id), fields="id,name,mimeType,parents,webViewLink", supportsAllDrives=True).execute()
    items: list[dict[str, Any]] = []
    token = None
    limit = max(1, min(int(page_size or 200), 1000))
    while True:
        resp = drive.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="nextPageToken,files(id,name,mimeType,parents,webViewLink,modifiedTime)",
            pageSize=min(1000, limit - len(items)), orderBy="folder,name_natural", spaces="drive",
            pageToken=token, supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
        items.extend(resp.get("files", []) or [])
        token = resp.get("nextPageToken")
        if not token or len(items) >= limit:
            break
    return {"folder": _drive_public_meta(parent), "count": len(items), "items": [_drive_public_meta(x) for x in items]}
def create_drive_folder(name: str, parent_folder: str, reuse_existing: bool = True) -> dict[str, Any]:
    from googleapiclient.discovery import build
    creds = _google_user_credentials()
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    parent_id = _extract_drive_folder_id(parent_folder)
    clean_name = str(name or "").strip()
    if not parent_id:
        raise ValueError("parent_folder is required (Drive folder id or URL)")
    if not clean_name:
        raise ValueError("name is required")
    if reuse_existing:
        q = f"'{parent_id}' in parents and trashed=false and mimeType='application/vnd.google-apps.folder' and name='{_drive_q(clean_name)}'"
        found = drive.files().list(q=q, fields="files(id,name,mimeType,parents,webViewLink)", pageSize=10, supportsAllDrives=True, includeItemsFromAllDrives=True).execute().get("files", []) or []
        if found:
            meta = found[0]
            _share_drive_anyone(drive, meta.get("id"))
            return {**_drive_public_meta(meta), "parent_folder_id": parent_id, "created": False, "reused": True, "access": "anyone_with_link_editor"}
    meta = drive.files().create(body={"name": clean_name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]}, fields="id,name,mimeType,parents,webViewLink", supportsAllDrives=True).execute()
    _share_drive_anyone(drive, meta.get("id"))
    return {**_drive_public_meta(meta), "parent_folder_id": parent_id, "created": True, "reused": False, "access": "anyone_with_link_editor"}
_DRIVE_DEFAULT_CATEGORIES = ["Регламенты", "Мотивация сотрудников", "ИИ / Автоматизация", "Финансы", "Обучение", "Отчёты", "Маркетинг / Продажи", "Операционка", "Архив / Разобрать вручную"]
_DRIVE_CATEGORY_KEYWORDS = {
    "Регламенты": ["регламент", "инструкц", "правил", "порядок", "политик", "стандарт", "соп", "sop"],
    "Мотивация сотрудников": ["мотивац", "преми", "бонус", "kpi", "оклад", "зарплат", "вознаграж", "сотрудник"],
    "ИИ / Автоматизация": ["ии", "ai", "gpt", "нейро", "автомат", "бот", "скрипт", "интеграц", "prompt", "промпт"],
    "Финансы": ["финанс", "счет", "счёт", "оплат", "платеж", "платёж", "бюджет", "касс", "ддс", "налог", "прибыл", "расход", "доход"],
    "Обучение": ["обуч", "курс", "тренинг", "адаптац", "онборд", "инструктаж", "гайд", "мануал"],
    "Отчёты": ["отчет", "отчёт", "дашборд", "сводк", "аналитик", "результат", "метрик"],
    "Маркетинг / Продажи": ["маркет", "продаж", "воронк", "лид", "клиент", "реклам", "акци", "crm", "коммерч", "кп"],
    "Операционка": ["операцион", "процесс", "план", "задач", "проект", "созвон", "встреч", "логист", "склад", "закуп"],
}
def _drive_classify_item(name: str, mime_type: str, categories: list[str]) -> tuple[str, str]:
    low = str(name or "").lower()
    scores: dict[str, int] = {c: 0 for c in categories}
    for cat, words in _DRIVE_CATEGORY_KEYWORDS.items():
        if cat not in scores:
            continue
        for w in words:
            if w in low:
                scores[cat] += 2 if len(w) > 3 else 1
    if mime_type == "application/vnd.google-apps.spreadsheet" or low.endswith((".xls", ".xlsx", ".csv")):
        for cat in ("Финансы", "Отчёты"):
            if cat in scores:
                scores[cat] += 1
    best = max(scores.items(), key=lambda kv: kv[1]) if scores else ("", 0)
    fallback = "Архив / Разобрать вручную"
    if best[1] <= 0:
        return (fallback if fallback in categories else (categories[-1] if categories else fallback), "no confident keyword match")
    return best[0], "keyword match"
def organize_drive_folder(folder: str, categories: list[str] | None = None, dry_run: bool = True) -> dict[str, Any]:
    folder_id = _extract_drive_folder_id(folder)
    if not folder_id:
        raise ValueError("folder is required (Drive folder id or URL)")
    cats = [str(x).strip() for x in (categories or _DRIVE_DEFAULT_CATEGORIES) if str(x).strip()]
    if not cats:
        raise ValueError("categories must not be empty")
    listing = list_drive_folder_items(folder_id, page_size=1000)
    existing_by_name = {x["name"]: x for x in listing["items"] if x.get("is_folder")}
    category_folders: dict[str, dict[str, Any]] = {}
    created: list[dict[str, Any]] = []
    for cat in cats:
        if cat in existing_by_name:
            category_folders[cat] = existing_by_name[cat]
        elif dry_run:
            category_folders[cat] = {"id": None, "name": cat, "is_folder": True, "would_create": True}
            created.append({"name": cat, "would_create": True})
        else:
            meta = create_drive_folder(cat, folder_id, reuse_existing=True)
            category_folders[cat] = meta
            if meta.get("created"):
                created.append(meta)
    moves: list[dict[str, Any]] = []
    moved: list[dict[str, Any]] = []
    category_names = set(cats)
    for item in listing["items"]:
        name = item.get("name") or ""
        if item.get("is_folder") and name in category_names:
            continue
        target, reason = _drive_classify_item(name, item.get("mime_type") or "", cats)
        target_meta = category_folders.get(target)
        plan = {"item_id": item.get("id"), "item_name": name, "item_is_folder": bool(item.get("is_folder")), "target_folder": target, "target_folder_id": target_meta.get("id") if target_meta else None, "reason": reason}
        moves.append(plan)
        if not dry_run and target_meta and target_meta.get("id"):
            res = move_drive_file_to_folder(str(item.get("id")), str(target_meta.get("id")))
            moved.append({**plan, "moved": bool(res.get("moved"))})
    return {"folder_id": folder_id, "dry_run": bool(dry_run), "categories": cats, "source_items_count": listing["count"], "category_folders": category_folders, "created_folders": created, "planned_moves": moves, "moved": moved, "moved_count": len(moved)}
def fetch_google_drive_company_payload(
    known_files: dict[str, dict[str, Any]] | None = None,
    known_folders: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    sync_url, token = google_drive_company_sync_config()
    timeout = int(os.getenv("GOOGLE_DRIVE_SYNC_TIMEOUT_SECONDS", "600") or "600")
    used_post = False
    if known_files is not None or known_folders is not None:
        try:
            response = requests.post(
                sync_url,
                json={"token": token, "known_files": known_files or {}, "known_folders": known_folders or {}},
                timeout=timeout,
            )
            used_post = True
        except requests.RequestException:
            response = requests.get(sync_url, params={"token": token}, timeout=timeout)
        else:
            if response.status_code in {404, 405}:
                response = requests.get(sync_url, params={"token": token}, timeout=timeout)
                used_post = False
    else:
        response = requests.get(sync_url, params={"token": token}, timeout=timeout)
    if not response.ok:
        raise RuntimeError(f"Google Apps Script вернул HTTP {response.status_code}: {response.text[:500]}")
    try:
        payload = response.json()
    except ValueError:
        if not used_post:
            raise RuntimeError(f"Google Apps Script вернул не JSON: {response.text[:500]}")
        response = requests.get(sync_url, params={"token": token}, timeout=timeout)
        if not response.ok:
            raise RuntimeError(f"Google Apps Script вернул HTTP {response.status_code}: {response.text[:500]}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(f"Google Apps Script вернул не JSON: {response.text[:500]}") from exc
    if not isinstance(payload, dict) or not payload.get("ok"):
        error = payload.get("error") if isinstance(payload, dict) else "неожиданный формат ответа"
        raise RuntimeError(f"Google Apps Script error: {error}")
    return payload
def fetch_google_drive_company_documents() -> list[dict[str, Any]]:
    payload = fetch_google_drive_company_payload()
    documents = payload.get("documents", [])
    if not isinstance(documents, list):
        raise RuntimeError("Google Apps Script вернул documents не в виде списка")
    return [doc for doc in documents if isinstance(doc, dict)]
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
def ensure_company_drive_root(cur: Any) -> str:
    root_name = os.getenv("GOOGLE_DRIVE_COMPANY_ROOT_NAME", "Google Drive").strip() or "Google Drive"
    cur.execute(
        """
        SELECT id
        FROM company_folders
        WHERE parent_id IS NULL AND lower(name) = lower(%s)
        ORDER BY created_at
        LIMIT 1
        """,
        (root_name,),
    )
    row = cur.fetchone()
    if row:
        return str(row["id"])
    cur.execute(
        """
        SELECT COALESCE(max(sort_order), -1) + 1 AS next_order
        FROM company_folders
        WHERE parent_id IS NULL
        """
    )
    sort_order = cur.fetchone()["next_order"]
    cur.execute(
        """
        INSERT INTO company_folders (parent_id, name, content, sort_order)
        VALUES (NULL, %s, '', %s)
        RETURNING id
        """,
        (root_name, sort_order),
    )
    return str(cur.fetchone()["id"])
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
def sync_google_drive_company_documents() -> dict[str, Any]:
    ensure_company_profile_schema()
    known_files: dict[str, dict[str, Any]] = {}
    known_folders: dict[str, dict[str, Any]] = {}
    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT google_file_id, name, google_updated_at, content_hash, parent_google_folder_id, drive_path
                FROM company_drive_sources
                """
            )
            for row in cur.fetchall():
                known_files[str(row["google_file_id"])] = {
                    "name": row["name"],
                    "updated_at": google_timestamp_for_apps_script(row["google_updated_at"]),
                    "content_hash": row["content_hash"] or "",
                    "parent_folder_id": row["parent_google_folder_id"] or "",
                    "path": row["drive_path"] or "",
                }
            cur.execute(
                """
                SELECT google_folder_id, name, parent_google_folder_id, drive_path
                FROM company_drive_folders
                """
            )
            for row in cur.fetchall():
                known_folders[str(row["google_folder_id"])] = {
                    "name": row["name"],
                    "parent_folder_id": row["parent_google_folder_id"] or "",
                    "path": row["drive_path"] or "",
                }

    payload = fetch_google_drive_company_payload(known_files=known_files, known_folders=known_folders)
    documents_raw = payload.get("documents", [])
    if not isinstance(documents_raw, list):
        raise RuntimeError("Google Apps Script вернул documents не в виде списка")
    folders_raw = payload.get("folders")
    has_folder_listing = isinstance(folders_raw, list)
    folders = [folder for folder in folders_raw if isinstance(folder, dict)] if has_folder_listing else []
    documents = [doc for doc in documents_raw if isinstance(doc, dict)]
    document_errors = payload.get("document_errors", [])
    skipped_files = payload.get("skipped_files", [])
    root_google_folder_id = str(payload.get("folder_id") or "").strip()
    seen_file_ids: set[str] = set()
    seen_folder_ids: set[str] = set()
    created = 0
    updated = 0
    unchanged = 0
    folders_created = 0
    folders_updated = 0
    folders_unchanged = 0
    skipped = 0

    with pg_connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                root_id = ensure_company_drive_root(cur)
                cur.execute("SELECT google_file_id, folder_id, content_hash FROM company_drive_sources")
                existing = {str(row["google_file_id"]): row for row in cur.fetchall()}
                cur.execute("SELECT google_folder_id, folder_id, name, parent_google_folder_id, drive_path FROM company_drive_folders")
                existing_folders = {str(row["google_folder_id"]): row for row in cur.fetchall()}
                google_folder_to_local: dict[str, str] = {
                    google_id: str(row["folder_id"]) for google_id, row in existing_folders.items()
                }

                for folder in sorted(folders, key=lambda item: len(item.get("path_parts") or [])):
                    google_folder_id = str(folder.get("id") or "").strip()
                    name = str(folder.get("name") or "").strip()
                    if not google_folder_id or not name:
                        continue
                    seen_folder_ids.add(google_folder_id)
                    parent_google_folder_id = str(folder.get("parent_folder_id") or "").strip() or None
                    parent_id = root_id
                    if parent_google_folder_id and parent_google_folder_id != root_google_folder_id:
                        parent_id = google_folder_to_local.get(parent_google_folder_id, root_id)
                    drive_path = google_drive_path_from_parts(folder.get("path_parts") or folder.get("path"))
                    source_url = str(folder.get("url") or "").strip() or None
                    existing_folder = existing_folders.get(google_folder_id)
                    if existing_folder:
                        local_folder_id = str(existing_folder["folder_id"])
                        metadata_changed = (
                            str(existing_folder["name"] or "") != name
                            or str(existing_folder["parent_google_folder_id"] or "") != (parent_google_folder_id or "")
                            or str(existing_folder["drive_path"] or "") != drive_path
                        )
                        if metadata_changed:
                            cur.execute(
                                """
                                UPDATE company_folders
                                SET parent_id = %s, name = %s, updated_at = now()
                                WHERE id = %s
                                """,
                                (parent_id, name, local_folder_id),
                            )
                            cur.execute(
                                """
                                UPDATE company_drive_folders
                                SET name = %s,
                                    parent_google_folder_id = %s,
                                    source_url = %s,
                                    drive_path = %s,
                                    raw_json = %s,
                                    last_seen_at = now(),
                                    updated_at = now()
                                WHERE google_folder_id = %s
                                """,
                                (name, parent_google_folder_id, source_url, drive_path, Jsonb(folder), google_folder_id),
                            )
                            folders_updated += 1
                        else:
                            cur.execute(
                                """
                                UPDATE company_drive_folders
                                SET last_seen_at = now(), raw_json = %s
                                WHERE google_folder_id = %s
                                """,
                                (Jsonb(folder), google_folder_id),
                            )
                            folders_unchanged += 1
                    else:
                        cur.execute(
                            """
                            SELECT COALESCE(max(sort_order), -1) + 1 AS next_order
                            FROM company_folders
                            WHERE parent_id = %s
                            """,
                            (parent_id,),
                        )
                        sort_order = cur.fetchone()["next_order"]
                        cur.execute(
                            """
                            INSERT INTO company_folders (parent_id, name, content, sort_order)
                            VALUES (%s, %s, '', %s)
                            RETURNING id
                            """,
                            (parent_id, name, sort_order),
                        )
                        local_folder_id = str(cur.fetchone()["id"])
                        cur.execute(
                            """
                            INSERT INTO company_drive_folders (
                                google_folder_id, folder_id, parent_google_folder_id, name,
                                source_url, drive_path, raw_json
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                            """,
                            (google_folder_id, local_folder_id, parent_google_folder_id, name, source_url, drive_path, Jsonb(folder)),
                        )
                        google_folder_to_local[google_folder_id] = local_folder_id
                        folders_created += 1

                for document in documents:
                    google_file_id = str(document.get("id") or "").strip()
                    name = str(document.get("name") or "").strip()
                    if not google_file_id or not name:
                        skipped += 1
                        continue
                    seen_file_ids.add(google_file_id)

                    mime_type = str(document.get("mime_type") or "").strip()
                    source_url = str(document.get("url") or "").strip() or None
                    google_updated_at = parse_google_timestamp(document.get("updated_at"))
                    parent_google_folder_id = str(document.get("parent_folder_id") or "").strip() or None
                    parent_id = root_id
                    if parent_google_folder_id and parent_google_folder_id != root_google_folder_id:
                        parent_id = google_folder_to_local.get(parent_google_folder_id, root_id)
                    # path_parts holds the parent folder chain only; the file
                    # name is appended below. Never fall back to `path` here:
                    # `path` already includes the file name, which would double
                    # it for root files ([] is falsy) and break the Apps Script
                    # "unchanged" comparison, forcing a re-download every sync.
                    document_path_parts = document.get("path_parts")
                    if not isinstance(document_path_parts, list):
                        document_path_parts = []
                    drive_path = google_drive_path_from_parts(
                        document_path_parts,
                        file_name=name,
                    )
                    existing_row = existing.get(google_file_id)

                    if existing_row:
                        folder_id = str(existing_row["folder_id"])
                        cur.execute("SELECT id FROM company_folders WHERE id = %s", (folder_id,))
                        if not cur.fetchone():
                            existing_row = None
                        elif not parent_google_folder_id and not has_folder_listing:
                            parent_id = folder_id

                    is_unchanged_payload = bool(document.get("unchanged"))
                    if existing_row:
                        folder_id = str(existing_row["folder_id"])
                        if is_unchanged_payload:
                            cur.execute(
                                """
                                UPDATE company_drive_sources
                                SET last_seen_at = now(), raw_json = %s
                                WHERE google_file_id = %s
                                """,
                                (Jsonb(document), google_file_id),
                            )
                            unchanged += 1
                            continue

                        content = google_drive_document_structured_content(document)
                        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                        metadata_changed = False
                        cur.execute(
                            """
                            SELECT f.parent_id, f.name, ds.name AS source_name, ds.mime_type, ds.source_url,
                                   ds.google_updated_at, ds.content_hash, ds.parent_google_folder_id, ds.drive_path
                            FROM company_folders f
                            JOIN company_drive_sources ds ON ds.folder_id = f.id
                            WHERE ds.google_file_id = %s
                            """,
                            (google_file_id,),
                        )
                        current_row = cur.fetchone()
                        if current_row:
                            metadata_changed = (
                                str(current_row["parent_id"]) != str(parent_id)
                                or str(current_row["name"] or "") != name
                                or str(current_row["mime_type"] or "") != mime_type
                                or str(current_row["source_url"] or "") != str(source_url or "")
                                or google_timestamp_for_apps_script(current_row["google_updated_at"]) != google_timestamp_for_apps_script(google_updated_at)
                                or str(current_row["parent_google_folder_id"] or "") != str(parent_google_folder_id or "")
                                or str(current_row["drive_path"] or "") != drive_path
                                or str(current_row["content_hash"] or "") != content_hash
                            )
                        if not metadata_changed:
                            cur.execute(
                                """
                                UPDATE company_drive_sources
                                SET last_seen_at = now(), raw_json = %s
                                WHERE google_file_id = %s
                                """,
                                (Jsonb(document), google_file_id),
                            )
                            unchanged += 1
                            continue
                        cur.execute(
                            """
                            UPDATE company_folders
                            SET parent_id = %s, name = %s, content = %s, updated_at = now()
                            WHERE id = %s
                            """,
                            (parent_id, name, content, folder_id),
                        )
                        cur.execute(
                            """
                            UPDATE company_drive_sources
                            SET name = %s,
                                mime_type = %s,
                                source_url = %s,
                                google_updated_at = %s,
                                raw_json = %s,
                                content_hash = %s,
                                parent_google_folder_id = %s,
                                drive_path = %s,
                                last_seen_at = now(),
                                updated_at = now()
                            WHERE google_file_id = %s
                            """,
                            (
                                name,
                                mime_type,
                                source_url,
                                google_updated_at,
                                Jsonb(document),
                                content_hash,
                                parent_google_folder_id,
                                drive_path,
                                google_file_id,
                            ),
                        )
                        updated += 1
                    else:
                        if is_unchanged_payload:
                            skipped += 1
                            continue
                        content = google_drive_document_structured_content(document)
                        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                        cur.execute(
                            """
                            SELECT COALESCE(max(sort_order), -1) + 1 AS next_order
                            FROM company_folders
                            WHERE parent_id = %s
                            """,
                            (parent_id,),
                        )
                        sort_order = cur.fetchone()["next_order"]
                        cur.execute(
                            """
                            INSERT INTO company_folders (parent_id, name, content, sort_order)
                            VALUES (%s, %s, %s, %s)
                            RETURNING id
                            """,
                            (parent_id, name, content, sort_order),
                        )
                        folder_id = str(cur.fetchone()["id"])
                        cur.execute(
                            """
                            INSERT INTO company_drive_sources (
                                google_file_id, folder_id, name, mime_type, source_url,
                                google_updated_at, raw_json, content_hash, parent_google_folder_id, drive_path
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                google_file_id,
                                folder_id,
                                name,
                                mime_type,
                                source_url,
                                google_updated_at,
                                Jsonb(document),
                                content_hash,
                                parent_google_folder_id,
                                drive_path,
                            ),
                        )
                        created += 1

                # Keep documents that exist in Drive but failed transient
                # extraction this run (e.g. Drive copy rate limit). They are not
                # in `documents`, but deleting them would drop already-synced
                # content only to recreate it on the next sync.
                protected_file_ids = set(seen_file_ids)
                if isinstance(document_errors, list):
                    for err in document_errors:
                        if isinstance(err, dict):
                            err_id = str(err.get("id") or "").strip()
                            if err_id:
                                protected_file_ids.add(err_id)

                if protected_file_ids:
                    cur.execute(
                        """
                        DELETE FROM company_folders
                        WHERE id IN (
                            SELECT folder_id
                            FROM company_drive_sources
                            WHERE google_file_id <> ALL(%s)
                        )
                        """,
                        (list(protected_file_ids),),
                    )
                else:
                    cur.execute(
                        """
                        DELETE FROM company_folders
                        WHERE id IN (SELECT folder_id FROM company_drive_sources)
                        """
                    )
                deleted = cur.rowcount

                if has_folder_listing:
                    if seen_folder_ids:
                        cur.execute(
                            """
                            DELETE FROM company_folders
                            WHERE id IN (
                                SELECT folder_id
                                FROM company_drive_folders
                                WHERE google_folder_id <> ALL(%s)
                            )
                            """,
                            (list(seen_folder_ids),),
                        )
                    else:
                        cur.execute(
                            """
                            DELETE FROM company_folders
                            WHERE id IN (SELECT folder_id FROM company_drive_folders)
                            """
                        )
                    folders_deleted = cur.rowcount
                else:
                    folders_deleted = 0

    return {
        "documents_total": len(documents),
        "folders_total": len(folders),
        "source_folder_id": payload.get("folder_id"),
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "deleted": deleted,
        "folders_created": folders_created,
        "folders_updated": folders_updated,
        "folders_unchanged": folders_unchanged,
        "folders_deleted": folders_deleted,
        "skipped": skipped,
        "document_errors": document_errors if isinstance(document_errors, list) else [],
        "document_errors_count": len(document_errors) if isinstance(document_errors, list) else 0,
        "skipped_files": skipped_files if isinstance(skipped_files, list) else [],
        "skipped_files_count": len(skipped_files) if isinstance(skipped_files, list) else 0,
    }
DRIVE_CALLS_ACCOUNT_KEY = "GOOGLE_DRIVE_TRANSCRIPTS"
def google_drive_calls_sync_config() -> tuple[str, str]:
    sync_url = (
        os.getenv("GOOGLE_CALLS_APPS_SCRIPT_SYNC_URL", "").strip()
        or os.getenv("GOOGLE_APPS_SCRIPT_SYNC_URL", "").strip()
    )
    token = (
        os.getenv("GOOGLE_CALLS_APPS_SCRIPT_SYNC_TOKEN", "").strip()
        or os.getenv("GOOGLE_APPS_SCRIPT_SYNC_TOKEN", "").strip()
    )
    if not sync_url:
        raise ValueError("Укажите GOOGLE_CALLS_APPS_SCRIPT_SYNC_URL или GOOGLE_APPS_SCRIPT_SYNC_URL в .env")
    if not token:
        raise ValueError("Укажите GOOGLE_CALLS_APPS_SCRIPT_SYNC_TOKEN в .env")
    return sync_url, token
def fetch_google_drive_call_transcripts() -> list[dict[str, Any]]:
    sync_url, token = google_drive_calls_sync_config()
    timeout = int(os.getenv("GOOGLE_DRIVE_SYNC_TIMEOUT_SECONDS", "600") or "600")
    response = requests.get(sync_url, params={"token": token}, timeout=timeout)
    if not response.ok:
        raise RuntimeError(f"Google Apps Script вернул HTTP {response.status_code}: {response.text[:500]}")
    payload = response.json()
    if not isinstance(payload, dict) or not payload.get("ok"):
        error = payload.get("error") if isinstance(payload, dict) else "неожиданный формат ответа"
        raise RuntimeError(f"Google Apps Script error: {error}")
    transcripts = payload.get("transcripts", [])
    if not isinstance(transcripts, list):
        raise RuntimeError("Google Apps Script вернул transcripts не в виде списка")
    return [item for item in transcripts if isinstance(item, dict)]
def drive_call_topic(item: dict[str, Any]) -> str:
    parent_name = str(item.get("parent_folder_name") or "").strip()
    path_parts = [str(part).strip() for part in (item.get("path_parts") or []) if str(part).strip()]
    if parent_name:
        return parent_name
    if path_parts:
        return path_parts[-1]
    return "Созвон из Google Drive"
def parse_drive_call_datetime(item: dict[str, Any]) -> datetime:
    path_parts = [str(part).strip() for part in (item.get("path_parts") or []) if str(part).strip()]
    candidates = [
        str(item.get("parent_folder_name") or ""),
        *reversed(path_parts),
        str(item.get("path") or ""),
        str(item.get("name") or ""),
    ]
    datetime_patterns = [
        r"(?P<year>20\d{2})[-_.](?P<month>\d{1,2})[-_.](?P<day>\d{1,2})[ T_-]+(?P<hour>\d{1,2})[:.-](?P<minute>\d{2})",
        r"(?P<day>\d{1,2})[.-](?P<month>\d{1,2})[.-](?P<year>20\d{2})[ T_-]+(?P<hour>\d{1,2})[:.-](?P<minute>\d{2})",
    ]
    date_patterns = [
        r"(?P<year>20\d{2})[-_.](?P<month>\d{1,2})[-_.](?P<day>\d{1,2})",
        r"(?P<day>\d{1,2})[.-](?P<month>\d{1,2})[.-](?P<year>20\d{2})",
    ]

    for candidate in candidates:
        for pattern in datetime_patterns:
            match = re.search(pattern, candidate)
            if not match:
                continue
            try:
                return datetime(
                    int(match.group("year")),
                    int(match.group("month")),
                    int(match.group("day")),
                    int(match.group("hour")),
                    int(match.group("minute")),
                    tzinfo=MSK_TZ,
                )
            except ValueError:
                continue

    for candidate in candidates:
        for pattern in date_patterns:
            match = re.search(pattern, candidate)
            if not match:
                continue
            try:
                return datetime(
                    int(match.group("year")),
                    int(match.group("month")),
                    int(match.group("day")),
                    0,
                    0,
                    tzinfo=MSK_TZ,
                )
            except ValueError:
                continue

    legacy_match = re.search(r"(?P<day>\d{1,2})\s+(?P<month>[а-яА-ЯёЁ]+)\s+(?P<year>20\d{2})", " ".join(candidates))
    if legacy_match:
        month_name = legacy_match.group("month").lower().replace("ё", "е")
        month_map = {value[1].replace("ё", "е"): key for key, value in RU_MONTH_NAMES.items()}
        try:
            if month_name in month_map:
                return datetime(
                    int(legacy_match.group("year")),
                    month_map[month_name],
                    int(legacy_match.group("day")),
                    0,
                    0,
                    tzinfo=MSK_TZ,
                )
        except ValueError:
            pass

    updated_at = parse_datetime(item.get("updated_at"))
    if updated_at:
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=MSK_TZ)
        return updated_at.astimezone(MSK_TZ)
    return datetime.now(MSK_TZ)
def parse_transcript_offset(value: str) -> str | None:
    match = re.search(r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?:[.,](\d{1,3}))?", value)
    if not match:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    millis = (match.group(4) or "000").ljust(3, "0")[:3]
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis}"
def parse_drive_transcript_txt(text: str) -> list[dict[str, Any]]:
    lines = text.replace("\ufeff", "").splitlines()
    cues: list[dict[str, Any]] = []
    time_value = r"(?:(?:\d{1,2}:)?\d{1,2}:\d{2})(?:[.,]\d{1,3})?"
    speaker_range_re = re.compile(
        rf"^\[(?P<start>{time_value})\s*(?:-->|-|–|—|\?)\s*(?P<end>{time_value})\]\s*(?P<speaker>[^:]+):\s*(?P<text>.*)$"
    )
    speaker_single_time_re = re.compile(
        rf"^\[(?P<start>{time_value})\]\s*(?P<speaker>[^:]+):\s*(?P<text>.*)$"
    )
    speaker_plain_re = re.compile(r"^(?P<speaker>[^:]{1,80}):\s*(?P<text>.+)$")

    def clean_speaker(value: str) -> str:
        cleaned = re.sub(r"^\s*\d+\]\s*", "", value).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned[:120]

    def append_text(raw_value: str) -> None:
        value = raw_value.strip()
        if not value:
            return
        if cues:
            cues[-1]["text"] = f"{cues[-1].get('text', '').rstrip()} {value}".strip()
            cues[-1]["raw"] = f"{cues[-1].get('raw', '')}\n{raw_value}".strip()
            return
        cues.append(
            {
                "segment_index": 1,
                "start": None,
                "end": None,
                "speaker": None,
                "text": value,
                "raw": raw_value,
            }
        )

    def start_cue(start: str | None, end: str | None, speaker: str | None, cue_text: str, raw_line: str) -> None:
        cues.append(
            {
                "segment_index": 1,
                "start": parse_transcript_offset(start or ""),
                "end": parse_transcript_offset(end or ""),
                "speaker": clean_speaker(speaker or "") or None,
                "text": cue_text.strip(),
                "raw": raw_line,
            }
        )

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.upper() == "WEBVTT":
            continue

        range_speaker_match = speaker_range_re.match(line)
        if range_speaker_match:
            start_cue(
                range_speaker_match.group("start"),
                range_speaker_match.group("end"),
                range_speaker_match.group("speaker"),
                range_speaker_match.group("text"),
                raw_line,
            )
            continue

        single_time_speaker_match = speaker_single_time_re.match(line)
        if single_time_speaker_match:
            start_cue(
                single_time_speaker_match.group("start"),
                None,
                single_time_speaker_match.group("speaker"),
                single_time_speaker_match.group("text"),
                raw_line,
            )
            continue

        plain_speaker_match = speaker_plain_re.match(line)
        if plain_speaker_match and not re.match(r"^(https?|Источник|Тип|Обновлено)\b", line, re.IGNORECASE):
            start_cue(
                None,
                None,
                plain_speaker_match.group("speaker"),
                plain_speaker_match.group("text"),
                raw_line,
            )
            continue

        append_text(raw_line)

    return [cue for cue in cues if str(cue.get("text") or "").strip()]
def drive_transcript_participants(cues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    participants: list[dict[str, Any]] = []
    seen: set[str] = set()
    ignored = {"unknown", "speaker", "webvtt", "transcript", "участник", "спикер"}
    for cue in cues:
        speaker = str(cue.get("speaker") or "").strip()
        speaker_key = speaker.lower()
        if not speaker or speaker_key in ignored or speaker_key in seen:
            continue
        seen.add(speaker_key)
        participants.append({"name": speaker, "source": "transcript_speaker"})
    return participants
def sync_google_drive_call_transcripts() -> dict[str, Any]:
    ensure_zoom_schema()
    transcripts = fetch_google_drive_call_transcripts()
    synced_calls = 0
    synced_segments = 0
    synced_participants = 0
    seen_uuids: list[str] = []

    with pg_connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                for item in transcripts:
                    file_id = str(item.get("id") or "").strip()
                    content = str(item.get("content") or "").strip()
                    if not file_id or not content:
                        continue
                    zoom_uuid = f"drive_transcript:{file_id}"
                    seen_uuids.append(zoom_uuid)
                    start_msk = parse_drive_call_datetime(item)
                    start_utc = start_msk.astimezone(timezone.utc)
                    cues = parse_drive_transcript_txt(content)
                    participants = drive_transcript_participants(cues)
                    topic = drive_call_topic(item)
                    raw_json = dict(item)
                    raw_json["source"] = "google_drive_transcript_txt"
                    raw_json["parsed_segments_count"] = len(cues)
                    raw_json["parsed_participants_count"] = len(participants)

                    cur.execute(
                        """
                        INSERT INTO zoom_calls (
                            zoom_account_key, zoom_user_email, zoom_meeting_id, zoom_uuid,
                            topic, technical_topic, start_time_utc, start_time_msk,
                            end_time_msk, call_date, duration_min, timezone, share_url,
                            transcript_text, transcript_format, raw_json, synced_at, updated_at
                        )
                        VALUES (%s, NULL, NULL, %s, %s, %s, %s, %s, NULL, %s, NULL, %s, %s, %s, 'txt', %s, now(), now())
                        ON CONFLICT (zoom_uuid) DO UPDATE SET
                            zoom_account_key = EXCLUDED.zoom_account_key,
                            topic = EXCLUDED.topic,
                            technical_topic = EXCLUDED.technical_topic,
                            start_time_utc = EXCLUDED.start_time_utc,
                            start_time_msk = EXCLUDED.start_time_msk,
                            call_date = EXCLUDED.call_date,
                            timezone = EXCLUDED.timezone,
                            share_url = EXCLUDED.share_url,
                            transcript_text = EXCLUDED.transcript_text,
                            transcript_format = EXCLUDED.transcript_format,
                            raw_json = EXCLUDED.raw_json,
                            synced_at = now(),
                            updated_at = now()
                        RETURNING id
                        """,
                        (
                            DRIVE_CALLS_ACCOUNT_KEY,
                            zoom_uuid,
                            topic,
                            topic,
                            start_utc,
                            start_msk,
                            start_msk.date(),
                            "Europe/Moscow",
                            item.get("url"),
                            content,
                            pg_json(raw_json),
                        ),
                    )
                    call_id = cur.fetchone()["id"]
                    cur.execute("DELETE FROM zoom_call_participants WHERE call_id = %s", (call_id,))
                    cur.execute("DELETE FROM zoom_call_transcript_segments WHERE call_id = %s", (call_id,))
                    for participant in participants:
                        cur.execute(
                            """
                            INSERT INTO zoom_call_participants (call_id, participant_name, raw_json)
                            VALUES (%s, %s, %s)
                            ON CONFLICT DO NOTHING
                            """,
                            (call_id, participant["name"], pg_json(participant)),
                        )
                    for cue_index, cue in enumerate(cues, start=1):
                        cur.execute(
                            """
                            INSERT INTO zoom_call_transcript_segments (
                                call_id, segment_index, cue_index, start_offset, end_offset, speaker, text, raw_json
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (call_id, segment_index, cue_index) DO UPDATE SET
                                start_offset = EXCLUDED.start_offset,
                                end_offset = EXCLUDED.end_offset,
                                speaker = EXCLUDED.speaker,
                                text = EXCLUDED.text,
                                raw_json = EXCLUDED.raw_json
                            """,
                            (
                                call_id,
                                int(cue.get("segment_index") or 1),
                                cue_index,
                                cue.get("start"),
                                cue.get("end"),
                                cue.get("speaker"),
                                cue.get("text") or "",
                                pg_json(cue),
                            ),
                        )
                    synced_calls += 1
                    synced_segments += len(cues)
                    synced_participants += len(participants)

                if seen_uuids:
                    cur.execute(
                        """
                        DELETE FROM zoom_calls
                        WHERE zoom_account_key = %s AND NOT (zoom_uuid = ANY(%s))
                        """,
                        (DRIVE_CALLS_ACCOUNT_KEY, seen_uuids),
                    )
                    removed_calls = cur.rowcount
                else:
                    cur.execute("DELETE FROM zoom_calls WHERE zoom_account_key = %s", (DRIVE_CALLS_ACCOUNT_KEY,))
                    removed_calls = cur.rowcount

    result = {
        "calls_synced": synced_calls,
        "transcript_files_synced": len(transcripts),
        "segments_synced": synced_segments,
        "participants_synced": synced_participants,
        "removed_calls": removed_calls,
    }
    record_integration_sync_success("google_drive_zoom_transcripts", result)
    return result
def google_drive_event_secret_valid(secret: str) -> bool:
    expected = os.getenv("GOOGLE_DRIVE_EVENT_SECRET", "").strip()
    if not expected:
        return False
    return hmac.compare_digest(secret, expected)
GOOGLE_DRIVE_SYNC_ADVISORY_LOCK_KEY = 728145903
def ensure_integration_sync_status_schema() -> None:
    if not postgres_enabled():
        return
    with pg_connect() as conn:
        with conn.cursor() as cur:
            if not pg_table_exists(cur, "public.integration_sync_status"):
                raise RuntimeError(
                    "PostgreSQL table public.integration_sync_status is missing. "
                    "Apply database migrations before using integration sync status."
                )
def record_integration_sync_success(sync_key: str, payload: dict[str, Any] | None = None) -> None:
    if not postgres_enabled():
        return
    ensure_integration_sync_status_schema()
    with pg_connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO integration_sync_status (
                        sync_key, last_success_at, last_attempt_at, status, raw_json, updated_at
                    )
                    VALUES (%s, now(), now(), 'success', %s, now())
                    ON CONFLICT (sync_key) DO UPDATE SET
                        last_success_at = EXCLUDED.last_success_at,
                        last_attempt_at = EXCLUDED.last_attempt_at,
                        status = EXCLUDED.status,
                        raw_json = EXCLUDED.raw_json,
                        updated_at = now()
                    """,
                    (sync_key, pg_json(payload or {})),
                )
def load_latest_integration_sync_success(sync_keys: list[str]) -> datetime | None:
    if not postgres_enabled() or not sync_keys:
        return None
    ensure_integration_sync_status_schema()
    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT max(last_success_at) AS last_success_at
                FROM integration_sync_status
                WHERE sync_key = ANY(%s) AND status = 'success'
                """,
                (sync_keys,),
            )
            row = cur.fetchone()
    return row["last_success_at"] if row else None
def is_technical_participant_name(name: Any) -> bool:
    return str(name or "").strip().lower() in ZOOM_TECHNICAL_PARTICIPANT_NAMES
def clean_zoom_speaker_names(speakers: Any) -> list[str]:
    """Distinct, order-preserving real speaker names from transcript speaker labels."""
    result: list[str] = []
    seen: set[str] = set()
    for raw in speakers or []:
        name = str(raw or "").strip()
        key = name.lower()
        if not name or key in ZOOM_SPEAKER_NOISE_NAMES or key in seen:
            continue
        seen.add(key)
        result.append(name)
    return result
def resolve_zoom_participants(
    api_participants: list[dict[str, Any]] | None,
    segment_speakers: Any = None,
) -> list[dict[str, Any]]:
    """Return display participants preferring real transcript speaker names over
    shared/technical Zoom account names.

    When people share one Zoom account ("Координатор") and rename themselves on the
    call, the transcript carries their real names while the Zoom participant list
    only has the account name. We surface the transcript names and drop the shared
    technical account when at least one real speaker is present; real (non-technical)
    Zoom participants who never spoke are still kept. Falls back to the raw
    participant list when no usable transcript speakers exist.
    """
    speakers = clean_zoom_speaker_names(segment_speakers)
    real_speakers = [s for s in speakers if not is_technical_participant_name(s)]
    real_speaker_keys = {s.lower() for s in real_speakers}
    have_real = bool(real_speakers)

    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(name: Any, email: Any) -> None:
        name = str(name or "").strip()
        email = str(email or "").strip()
        if not name and not email:
            return
        key = (name.lower(), email.lower())
        if key in seen:
            return
        seen.add(key)
        result.append({"name": name or None, "email": email or None})

    # 1) Authoritative identities: real transcript speakers.
    for speaker in real_speakers:
        add(speaker, None)

    # 2) Zoom participants: drop the shared technical account when real speakers
    #    cover the call; skip names already represented by a speaker.
    for participant in api_participants or []:
        name = str(participant.get("name") or "").strip()
        if have_real and is_technical_participant_name(name):
            continue
        if name and name.lower() in real_speaker_keys:
            continue
        add(name, participant.get("email"))

    # 3) Fallback: nothing resolved (no transcript, no usable participants).
    if not result:
        for participant in api_participants or []:
            add(participant.get("name"), participant.get("email"))
    return result
def load_zoom_calls_tree() -> dict[str, Any]:
    ensure_zoom_schema()
    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT zc.*, COALESCE(
                    jsonb_agg(
                        jsonb_build_object(
                            'name', zcp.participant_name,
                            'email', zcp.participant_email
                        )
                        ORDER BY zcp.participant_name NULLS LAST, zcp.participant_email NULLS LAST
                    ) FILTER (WHERE zcp.id IS NOT NULL),
                    '[]'::jsonb
                ) AS participants_json,
                (
                    SELECT COALESCE(jsonb_agg(DISTINCT s.speaker) FILTER (WHERE s.speaker IS NOT NULL), '[]'::jsonb)
                    FROM zoom_call_transcript_segments s
                    WHERE s.call_id = zc.id
                ) AS speakers_json
                FROM zoom_calls zc
                LEFT JOIN zoom_call_participants zcp ON zcp.call_id = zc.id
                GROUP BY zc.id
                ORDER BY zc.call_date DESC, zc.start_time_msk DESC
                """
            )
            rows = cur.fetchall()
    years_map: dict[int, dict[str, Any]] = {}
    latest_synced_at = None
    for row in rows:
        synced_at = row.get("synced_at")
        if synced_at and (latest_synced_at is None or synced_at > latest_synced_at):
            latest_synced_at = synced_at
        call_date = row["call_date"]
        year = call_date.year
        month = call_date.month
        participants = resolve_zoom_participants(
            row.get("participants_json") or [],
            row.get("speakers_json") or [],
        )
        call_payload = zoom_call_row_payload(row, participants)
        year_payload = years_map.setdefault(year, {"year": year, "months": {}})
        month_payload = year_payload["months"].setdefault(
            month,
            {
                "month": month,
                "title": f"{RU_MONTH_NAMES[month][0]} {year}",
                "dates": {},
            },
        )
        date_key = call_payload["date"]
        date_payload = month_payload["dates"].setdefault(
            date_key,
            {
                "date": date_key,
                "date_text": call_payload["date_text"],
                "calls": [],
            },
        )
        date_payload["calls"].append(call_payload)

    years = []
    for year_payload in sorted(years_map.values(), key=lambda item: item["year"], reverse=True):
        months = []
        for month_payload in sorted(year_payload["months"].values(), key=lambda item: item["month"], reverse=True):
            dates = sorted(month_payload["dates"].values(), key=lambda item: item["date"], reverse=True)
            month_payload = dict(month_payload)
            month_payload["dates"] = dates
            months.append(month_payload)
        years.append({"year": year_payload["year"], "months": months})
    latest_success_at = load_latest_integration_sync_success(["zoom_api_calls", "google_drive_zoom_transcripts"])
    display_updated_at = latest_success_at or latest_synced_at
    return {
        "years": years,
        "total": len(rows),
        "updated_at": iso_or_none(display_updated_at),
        "updated_at_text": format_datetime_msk_label(display_updated_at),
    }
@app.route("/google-drive/events/<secret>", methods=["GET", "POST"])
def google_drive_event_webhook(secret: str):
    """Near-realtime company Drive sync.

    The Apps Script time-driven trigger (every minute) pings this endpoint only
    when it detects a real change (file created/updated/deleted/moved/renamed)
    in the watched Google Drive folder. We then run the existing incremental
    sync, so create/update/delete is reflected within ~1 minute.
    """
    if not google_drive_event_secret_valid(secret):
        return jsonify({"error": "forbidden"}), 403
    if request.method == "GET":
        return jsonify({"ok": True, "message": "Google Drive change event endpoint is ready."})

    inline = str(os.getenv("GOOGLE_DRIVE_EVENT_PROCESS_INLINE", "1")).strip().lower() not in {"0", "false", "no", "off"}
    if not inline:
        return jsonify({"ok": True, "processed_inline": False, "message": "Inline processing disabled."})

    # Serialize across processes (hourly cron / manual button / other pings).
    # If another sync holds the lock, return 409 so the Apps Script keeps its
    # change marker and retries on the next minute instead of double-running.
    with pg_connect() as lock_conn:
        with lock_conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s) AS locked", (GOOGLE_DRIVE_SYNC_ADVISORY_LOCK_KEY,))
            if not bool(cur.fetchone()["locked"]):
                return jsonify({"ok": False, "busy": True, "message": "Company Drive sync already running."}), 409
            try:
                result = sync_google_drive_company_documents()
            except Exception as exc:  # noqa: BLE001
                # Log so transient failures (e.g. a momentary Apps Script /exec
                # hiccup) are diagnosable; returning 500 makes the trigger retry.
                app.logger.exception("google-drive change webhook sync failed")
                return jsonify({"ok": False, "error": str(exc)}), 500
            finally:
                cur.execute("SELECT pg_advisory_unlock(%s)", (GOOGLE_DRIVE_SYNC_ADVISORY_LOCK_KEY,))
    return jsonify({"ok": True, "processed_inline": True, "result": result})
@app.post("/api/zoom-calls/sync-google-drive")
def api_zoom_calls_sync_google_drive():
    try:
        result = sync_google_drive_call_transcripts()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Не удалось подтянуть transcript.txt из Google Drive: {exc}"}), 500
    result["tree"] = load_zoom_calls_tree()
    return jsonify(result)
@app.post("/api/company-folders/sync-google-drive")
def api_company_folders_sync_google_drive():
    try:
        result = sync_google_drive_company_documents()
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 400
    return jsonify({"result": result, "message": "Данные из Google Drive подтянуты"})
