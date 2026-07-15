"""Attachment store shared by b24bot (capture) and mcp.context_server (read / re-upload).

Every file an employee sends the bot is captured here so that:
  1. the FULL extracted text is available to the agent on demand — no 12k prompt truncation
     (the legal agent could only see the first ~1/8 of a contract before this);
  2. the raw bytes can be re-attached to a Bitrix task / comment / result later.

The short random `token` is the only handle. It is injected into the prompt of the dialog
that received the file, and the agent passes it back to get_attachment_text /
attach_files_to_task / add_bitrix_task_comment(attachment_ids=...). Tokens are unguessable,
so no per-call caller identity is needed — the token itself is the capability.

Storage: raw bytes live under ATTACH_DIR on the box (2 GB RAM / ~25 GB free disk — filesystem,
not the model context, holds the payload); the DB row indexes them + keeps the extracted text.
A size-bounded retention sweep keeps the folder from growing without bound.
"""
from __future__ import annotations

import logging
import os
import re
import secrets
from pathlib import Path
from typing import Any

from shared.db import connect

ATTACH_DIR = Path(os.getenv("B24_ATTACH_DIR", "/var/www/albery/.b24_attachments"))
# Keep files re-attachable for this many days, then the sweep may delete the bytes (text stays).
ATTACH_RETENTION_DAYS = int(os.getenv("B24_ATTACH_RETENTION_DAYS", "30") or "30")
_MAX_STORE_BYTES = int(os.getenv("B24_ATTACH_MAX_BYTES", str(40 * 1024 * 1024)) or str(40 * 1024 * 1024))

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.\-]+")


def _safe_name(name: str) -> str:
    base = _SAFE_NAME_RE.sub("_", (name or "file").strip()) or "file"
    return base[-80:]


def _new_token() -> str:
    # Short, URL-safe, unguessable. 'att_' prefix makes it obvious in prompts/logs.
    return "att_" + secrets.token_urlsafe(9)


def store_attachment(
    *,
    data: bytes,
    file_name: str,
    kind: str,
    extracted_text: str,
    agent_slug: str | None,
    dialog_id: str,
    bitrix_user_id: Any = None,
    mime: str | None = None,
    source_disk_file_id: Any = None,
) -> str | None:
    """Persist one incoming file (bytes + extracted text) and return its token, or None on failure.
    Best-effort: any error is logged and swallowed so it never breaks the chat reply."""
    try:
        if not data or len(data) > _MAX_STORE_BYTES:
            # Still index the text even if bytes are too big to keep for re-upload.
            data = data if data and len(data) <= _MAX_STORE_BYTES else b""
        token = _new_token()
        ext = file_name.rsplit(".", 1)[-1].lower() if "." in (file_name or "") else ""
        file_path = ""
        if data:
            ATTACH_DIR.mkdir(parents=True, exist_ok=True)
            dest = ATTACH_DIR / f"{token}__{_safe_name(file_name)}"
            dest.write_bytes(data)
            file_path = str(dest)
        text = (extracted_text or "").replace("\x00", "")  # NUL (0x00) is illegal in PG text
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO bitrix_bot_attachments "
                    "(token, agent_slug, dialog_id, bitrix_user_id, file_name, ext, kind, mime, "
                    " byte_size, char_len, extracted_text, file_path, source_disk_file_id) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (token, agent_slug, str(dialog_id), _to_int(bitrix_user_id),
                     file_name or "file", ext, kind, mime, len(data) if data else 0,
                     len(text), text, file_path or None, _to_int(source_disk_file_id)),
                )
        return token
    except Exception as exc:  # noqa: BLE001
        logging.warning("attachments.store failed (%s): %s", file_name, repr(exc)[:160])
        return None


def find_by_disk_file_id(disk_file_id: Any) -> dict[str, Any] | None:
    """The freshest stored attachment for a Bitrix disk file id — lets task-comment reads reuse
    the already-downloaded/recognized text instead of re-downloading and re-OCRing every call."""
    fid = _to_int(disk_file_id)
    if fid is None:
        return None
    try:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT token, file_name, ext, kind, char_len, extracted_text "
                    "FROM bitrix_bot_attachments WHERE source_disk_file_id = %s "
                    "ORDER BY created_at DESC LIMIT 1",
                    (fid,),
                )
                return cur.fetchone()
    except Exception as exc:  # noqa: BLE001
        logging.warning("attachments.find_by_disk_file_id failed (%s): %s", fid, repr(exc)[:120])
        return None


def get_attachment(token: str) -> dict[str, Any] | None:
    """Return the attachment row (without bytes) for a token, or None."""
    tok = str(token or "").strip()
    if not tok:
        return None
    try:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT token, agent_slug, dialog_id, bitrix_user_id, file_name, ext, kind, "
                    "       mime, byte_size, char_len, extracted_text, file_path, bitrix_disk_id "
                    "FROM bitrix_bot_attachments WHERE token = %s",
                    (tok,),
                )
                return cur.fetchone()
    except Exception as exc:  # noqa: BLE001
        logging.warning("attachments.get failed (%s): %s", tok, repr(exc)[:160])
        return None


def attachment_bytes(token: str) -> tuple[bytes, str] | None:
    """Read the stored raw bytes + file name for a token, or None if unavailable."""
    row = get_attachment(token)
    if not row:
        return None
    path = row.get("file_path")
    if not path:
        return None
    try:
        return Path(path).read_bytes(), row.get("file_name") or "file"
    except Exception as exc:  # noqa: BLE001
        logging.warning("attachments.bytes read failed (%s): %s", token, repr(exc)[:160])
        return None


def set_disk_id(token: str, disk_id: Any) -> None:
    """Remember the Bitrix disk file id after a re-upload, so repeated attaches reuse it."""
    did = _to_int(disk_id)
    if did is None:
        return
    try:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE bitrix_bot_attachments SET bitrix_disk_id = %s WHERE token = %s",
                            (did, str(token)))
    except Exception as exc:  # noqa: BLE001
        logging.warning("attachments.set_disk_id failed (%s): %s", token, repr(exc)[:120])


def _to_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
