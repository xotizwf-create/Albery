"""Channel-neutral resolver for internal signed export handoffs.

This module intentionally has no Flask/app imports so the durable Telegram worker can consume the
same generated files as Bitrix without starting unrelated schedulers.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import time
from typing import Any, Callable
from urllib.parse import unquote

from config import EXPORT_DIR

ZOOM_EXPORT_DIR = EXPORT_DIR / "zoom"
EXPORT_HANDOFF_RE = re.compile(
    r"(?:https?://[^/\s\]\)\"'<>]+)?/zoom-export/"
    r"(\d{9,12})/([0-9a-f]{8,32})/([^\s\]\)\"'<>]+)",
    re.I,
)


def export_token(filename: str, expires_at: int) -> str:
    secret = (os.getenv("FLASK_SECRET_KEY") or os.getenv("MCP_SHARED_SECRET")
              or "albery-zoom-export").encode("utf-8")
    message = f"{expires_at}:{os.path.basename(filename)}".encode("utf-8")
    return hmac.new(secret, message, hashlib.sha256).hexdigest()[:32]


def display_name(filename: str) -> str:
    safe = os.path.basename(filename)
    try:
        name = (ZOOM_EXPORT_DIR / f"{safe}.name").read_text(encoding="utf-8").strip()
        if name:
            return os.path.basename(name)
    except OSError:
        pass
    return safe


def extract_export_artifacts(
    text: str,
    *,
    repair_fn: Callable[[str], str] | None = None,
) -> tuple[str, list[dict[str, Any]], int]:
    """Consume valid internal export URLs and return clean text plus exact local files."""
    if not text or "/zoom-export/" not in text:
        return text or "", [], 0
    repaired = repair_fn(text) if repair_fn is not None else text
    artifacts: list[dict[str, Any]] = []
    seen: set[tuple[int, str, str]] = set()
    invalid = 0

    def consume(match: re.Match[str]) -> str:
        nonlocal invalid
        expires_at = int(match.group(1))
        token = match.group(2)
        filename = os.path.basename(unquote(match.group(3)))
        identity = (expires_at, token, filename)
        if identity in seen:
            return ""
        seen.add(identity)
        path = (ZOOM_EXPORT_DIR / filename).resolve()
        valid = (
            bool(filename)
            and path.parent == ZOOM_EXPORT_DIR.resolve()
            and path.is_file()
            and time.time() <= expires_at
            and hmac.compare_digest(token, export_token(filename, expires_at))
        )
        if not valid:
            invalid += 1
            return ""
        try:
            data = path.read_bytes()
        except OSError:
            invalid += 1
            return ""
        name = display_name(filename)
        artifacts.append({
            "filename": filename,
            "display_name": name,
            "data": data,
            "byte_size": len(data),
            "mime": __import__("mimetypes").guess_type(name)[0] or "application/octet-stream",
        })
        return ""

    cleaned = EXPORT_HANDOFF_RE.sub(consume, repaired)
    cleaned = re.sub(r"\[URL=\s*\](.*?)\[/URL\]", r"\1", cleaned, flags=re.I | re.S)
    cleaned = re.sub(r"\[([^\]]+)\]\(\s*\)", r"\1", cleaned)
    cleaned = re.sub(
        r"\(\s*(?:ссылка|link)\s+(?:действует|valid)[^\)]*(?:минут|minutes?)[^\)]*\)",
        "",
        cleaned,
        flags=re.I,
    )
    cleaned = re.sub(r"(?i)\bпо ссылке\s*:\s*(?=\n|$)", "во вложении", cleaned)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if artifacts:
        note = "📎 Файл прикреплён к сообщению." if len(artifacts) == 1 else "📎 Файлы прикреплены к сообщениям."
        if note not in cleaned:
            cleaned = (cleaned + "\n\n" + note).strip()
    if invalid:
        failure = "Не удалось безопасно приложить сформированный файл. Повторите создание документа."
        cleaned = (cleaned + "\n\n" + failure).strip()
    return cleaned, artifacts, invalid
