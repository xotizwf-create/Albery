"""Authenticated Telegram attachment proxy for the funnel workspace.

Telegram's bot token is part of the provider download URL.  That URL must never
reach the browser, so this module resolves and downloads files server-side,
enforces strict bounds, and returns a same-origin response.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import time
import unicodedata
from contextlib import closing
from tempfile import SpooledTemporaryFile
from typing import Any, Callable, Mapping
from urllib.parse import quote

import requests
from flask import Response, send_file

from shared.db import connect as pg_connect
from shared.db import load_env_value


_GET_FILE_RESPONSE_MAX_BYTES = 64 * 1024
_DEFAULT_ATTACHMENT_MAX_BYTES = 20 * 1024 * 1024
_DEFAULT_TOTAL_TIMEOUT_SECONDS = 20
_DOWNLOAD_CHUNK_BYTES = 64 * 1024
_SPOOL_MEMORY_BYTES = 1024 * 1024
_VALID_FILE_PATH = re.compile(r"^[A-Za-z0-9._/-]+$")
_VALID_MIME = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$")
_PHOTO_MIMES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})
_AUDIO_MIMES = frozenset(
    {
        "audio/aac",
        "audio/flac",
        "audio/mp4",
        "audio/mpeg",
        "audio/ogg",
        "audio/wav",
        "audio/webm",
        "audio/x-wav",
    }
)
_VIDEO_MIMES = frozenset(
    {
        "video/mp4",
        "video/mpeg",
        "video/ogg",
        "video/quicktime",
        "video/webm",
    }
)
_MEDIA_TYPES = frozenset(
    {
        "photo",
        "video",
        "video_note",
        "voice",
        "audio",
        "document",
        "sticker",
        "animation",
    }
)


class AttachmentProxyError(RuntimeError):
    """A safe, operator-facing attachment failure."""

    def __init__(self, message: str, *, status_code: int, code: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def _positive_env_int(name: str, default: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or str(default))
    except (TypeError, ValueError):
        value = default
    if value <= 0:
        value = default
    return min(value, maximum)


def attachment_max_bytes() -> int:
    return _positive_env_int(
        "FUNNEL_WORKSPACE_ATTACHMENT_MAX_BYTES",
        _DEFAULT_ATTACHMENT_MAX_BYTES,
        100 * 1024 * 1024,
    )


def attachment_timeout_seconds() -> int:
    return _positive_env_int(
        "FUNNEL_WORKSPACE_ATTACHMENT_TIMEOUT_SECONDS",
        _DEFAULT_TOTAL_TIMEOUT_SECONDS,
        60,
    )


def _positive_size(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _clean_mime(value: Any) -> str | None:
    mime = str(value or "").split(";", 1)[0].strip().lower()
    if not mime or not _VALID_MIME.fullmatch(mime):
        return None
    return mime


def _safe_filename(value: Any, media_type: str, mime_type: str | None) -> str:
    raw = unicodedata.normalize("NFC", str(value or ""))
    raw = raw.replace("\\", "/").rsplit("/", 1)[-1]
    raw = "".join(
        character
        if unicodedata.category(character)[0] != "C"
        and character not in {'"', "<", ">", ":", "/", "\\", "|", "?", "*"}
        else "_"
        for character in raw
    )
    raw = re.sub(r"\s+", " ", raw).strip(" .")
    if raw in {"", ".", ".."}:
        extension = {
            "photo": ".jpg",
            "voice": ".ogg",
            "audio": mimetypes.guess_extension(mime_type or "") or ".audio",
            "video": mimetypes.guess_extension(mime_type or "") or ".mp4",
            "video_note": ".mp4",
            "animation": ".mp4",
            "sticker": ".webp",
        }.get(media_type, mimetypes.guess_extension(mime_type or "") or ".bin")
        raw = f"{media_type or 'attachment'}{extension}"
    if len(raw) > 180:
        stem, extension = os.path.splitext(raw)
        raw = f"{stem[: max(1, 180 - len(extension))]}{extension[:20]}"
    return raw


def _safe_content_type(media_type: str, declared_mime: str | None) -> str:
    if media_type == "photo":
        return declared_mime if declared_mime in _PHOTO_MIMES else "image/jpeg"
    if media_type in {"voice", "audio"}:
        if declared_mime in _AUDIO_MIMES:
            return declared_mime
        return "audio/ogg" if media_type == "voice" else "audio/mpeg"
    if media_type in {"video", "video_note", "animation"} and declared_mime in _VIDEO_MIMES:
        return declared_mime
    return "application/octet-stream"


def _provider_media(metadata: Any) -> dict[str, Any]:
    if not isinstance(metadata, Mapping):
        return {}
    nested = metadata.get("telegram_media")
    media = dict(nested) if isinstance(nested, Mapping) else {}
    # Early workspace builds kept the same fields flat.  Read them for a
    # migration-free transition, while preferring the namespaced shape.
    for key in (
        "file_id",
        "file_unique_id",
        "file_name",
        "mime_type",
        "file_size",
        "media_type",
    ):
        if media.get(key) in (None, "") and metadata.get(key) not in (None, ""):
            media[key] = metadata.get(key)
    return media


def _provider_file_id(media: Mapping[str, Any]) -> str:
    file_id = str(media.get("file_id") or "").strip()
    if (
        not file_id
        or len(file_id) > 4096
        or any(unicodedata.category(character)[0] == "C" for character in file_id)
    ):
        return ""
    return file_id


def attachment_descriptor(metadata: Any, message_id: Any) -> dict[str, Any] | None:
    """Return only UI-safe media fields; provider file identifiers stay server-side."""

    if not isinstance(metadata, Mapping):
        return None
    media = _provider_media(metadata)
    if not _provider_file_id(media):
        return None
    raw_type = (
        media.get("media_type")
        or metadata.get("telegram_media_type")
        or metadata.get("media_type")
        or "document"
    )
    media_type = str(raw_type).strip().lower()
    if media_type not in _MEDIA_TYPES:
        media_type = "document"
    declared_mime = _clean_mime(media.get("mime_type"))
    content_type = _safe_content_type(media_type, declared_mime)
    filename = _safe_filename(media.get("file_name"), media_type, content_type)
    try:
        normalized_message_id = int(message_id)
    except (TypeError, ValueError):
        return None
    if normalized_message_id <= 0:
        return None
    base_url = f"/api/funnel-workspace/messages/{normalized_message_id}/attachment"
    return {
        "media_type": media_type,
        "file_name": filename,
        "mime_type": content_type,
        "file_size": _positive_size(media.get("file_size")),
        "url": base_url,
        "download_url": f"{base_url}?download=1",
    }


def _load_message_media(
    message_id: int,
    *,
    connect: Callable[[], Any] | None = None,
) -> tuple[dict[str, Any], str]:
    if message_id <= 0:
        raise AttachmentProxyError(
            "Вложение не найдено.",
            status_code=404,
            code="attachment_not_found",
        )
    factory = connect or pg_connect
    with factory() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT m.metadata, c.source_key
                  FROM funnel_workspace_messages m
                  JOIN funnel_workspace_conversations c
                    ON c.id = m.conversation_id
                 WHERE m.id = %s
                """,
                (message_id,),
            )
            row = cur.fetchone()
    # Вложение живёт в Telegram у обоих каналов — и у бизнес-переписки, и у чата с ботом.
    if not row or str(row.get("source_key") or "") not in {"telegram", "telegram_bot"}:
        raise AttachmentProxyError(
            "Вложение не найдено.",
            status_code=404,
            code="attachment_not_found",
        )
    metadata = row.get("metadata")
    descriptor = attachment_descriptor(metadata, message_id)
    media = _provider_media(metadata)
    file_id = _provider_file_id(media)
    if descriptor is None or not file_id:
        raise AttachmentProxyError(
            "У сообщения нет доступного вложения.",
            status_code=404,
            code="attachment_not_available",
        )
    return descriptor, file_id


def _remaining_timeout(started_at: float, timeout_seconds: int) -> tuple[float, float]:
    remaining = timeout_seconds - (time.monotonic() - started_at)
    if remaining <= 0:
        raise AttachmentProxyError(
            "Telegram не успел вернуть вложение. Попробуйте позже.",
            status_code=504,
            code="attachment_timeout",
        )
    return min(3.05, max(0.1, remaining)), max(0.1, remaining)


def _validate_provider_file_path(value: Any) -> str:
    path = str(value or "").strip()
    if (
        not path
        or len(path) > 1024
        or path.startswith("/")
        or "\\" in path
        or "?" in path
        or "#" in path
        or not _VALID_FILE_PATH.fullmatch(path)
    ):
        raise AttachmentProxyError(
            "Telegram вернул некорректный путь к вложению.",
            status_code=502,
            code="telegram_invalid_file_path",
        )
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise AttachmentProxyError(
            "Telegram вернул некорректный путь к вложению.",
            status_code=502,
            code="telegram_invalid_file_path",
        )
    return path


def _iter_response_bytes(response: Any, chunk_size: int = _DOWNLOAD_CHUNK_BYTES):
    yield from response.iter_content(chunk_size=chunk_size)


def _get_file_path(
    *,
    token: str,
    file_id: str,
    started_at: float,
    timeout_seconds: int,
    max_bytes: int,
    http_get: Callable[..., Any],
) -> str:
    url = f"https://api.telegram.org/bot{token}/getFile"
    try:
        provider_response = http_get(
            url,
            params={"file_id": file_id},
            stream=True,
            timeout=_remaining_timeout(started_at, timeout_seconds),
            allow_redirects=False,
        )
        with closing(provider_response) as response:
            if response.status_code != 200:
                raise AttachmentProxyError(
                    "Telegram не смог подготовить вложение.",
                    status_code=502,
                    code="telegram_get_file_failed",
                )
            content_length = _positive_size(response.headers.get("Content-Length"))
            if content_length and content_length > _GET_FILE_RESPONSE_MAX_BYTES:
                raise AttachmentProxyError(
                    "Telegram вернул некорректный ответ.",
                    status_code=502,
                    code="telegram_response_too_large",
                )
            chunks: list[bytes] = []
            total = 0
            for chunk in _iter_response_bytes(response, 8192):
                if not chunk:
                    continue
                total += len(chunk)
                if total > _GET_FILE_RESPONSE_MAX_BYTES:
                    raise AttachmentProxyError(
                        "Telegram вернул некорректный ответ.",
                        status_code=502,
                        code="telegram_response_too_large",
                    )
                chunks.append(chunk)
                _remaining_timeout(started_at, timeout_seconds)
    except requests.RequestException:
        raise AttachmentProxyError(
            "Telegram временно недоступен. Попробуйте позже.",
            status_code=502,
            code="telegram_unavailable",
        ) from None
    try:
        payload = json.loads(b"".join(chunks))
    except (TypeError, ValueError, UnicodeDecodeError):
        raise AttachmentProxyError(
            "Telegram вернул некорректный ответ.",
            status_code=502,
            code="telegram_invalid_response",
        ) from None
    if not isinstance(payload, Mapping) or payload.get("ok") is not True:
        raise AttachmentProxyError(
            "Telegram не смог подготовить вложение.",
            status_code=502,
            code="telegram_get_file_failed",
        )
    result = payload.get("result")
    if not isinstance(result, Mapping):
        raise AttachmentProxyError(
            "Telegram вернул некорректный ответ.",
            status_code=502,
            code="telegram_invalid_response",
        )
    provider_size = _positive_size(result.get("file_size"))
    if provider_size and provider_size > max_bytes:
        raise AttachmentProxyError(
            "Вложение слишком большое для просмотра в рабочем окне.",
            status_code=413,
            code="attachment_too_large",
        )
    return _validate_provider_file_path(result.get("file_path"))


def build_attachment_response(
    message_id: int,
    *,
    force_download: bool = False,
    connect: Callable[[], Any] | None = None,
    http_get: Callable[..., Any] | None = None,
) -> Response:
    descriptor, file_id = _load_message_media(message_id, connect=connect)
    token = load_env_value("TG_AGENT_BOT_TOKEN")
    if not token:
        raise AttachmentProxyError(
            "Telegram-вложения временно недоступны.",
            status_code=503,
            code="telegram_not_configured",
        )
    max_bytes = attachment_max_bytes()
    declared_size = _positive_size(descriptor.get("file_size"))
    if declared_size and declared_size > max_bytes:
        raise AttachmentProxyError(
            "Вложение слишком большое для просмотра в рабочем окне.",
            status_code=413,
            code="attachment_too_large",
        )
    timeout_seconds = attachment_timeout_seconds()
    started_at = time.monotonic()
    getter = http_get or requests.get
    file_path = _get_file_path(
        token=token,
        file_id=file_id,
        started_at=started_at,
        timeout_seconds=timeout_seconds,
        max_bytes=max_bytes,
        http_get=getter,
    )
    quoted_path = quote(file_path, safe="/._-")
    download_url = f"https://api.telegram.org/file/bot{token}/{quoted_path}"
    spool = SpooledTemporaryFile(max_size=min(_SPOOL_MEMORY_BYTES, max_bytes), mode="w+b")
    total = 0
    try:
        try:
            provider_response = getter(
                download_url,
                stream=True,
                timeout=_remaining_timeout(started_at, timeout_seconds),
                allow_redirects=False,
            )
            with closing(provider_response) as response:
                if response.status_code != 200:
                    raise AttachmentProxyError(
                        "Telegram не смог вернуть вложение.",
                        status_code=502,
                        code="telegram_download_failed",
                    )
                content_length = _positive_size(response.headers.get("Content-Length"))
                if content_length and content_length > max_bytes:
                    raise AttachmentProxyError(
                        "Вложение слишком большое для просмотра в рабочем окне.",
                        status_code=413,
                        code="attachment_too_large",
                    )
                for chunk in _iter_response_bytes(response):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        raise AttachmentProxyError(
                            "Вложение слишком большое для просмотра в рабочем окне.",
                            status_code=413,
                            code="attachment_too_large",
                        )
                    spool.write(chunk)
                    _remaining_timeout(started_at, timeout_seconds)
        except requests.RequestException:
            raise AttachmentProxyError(
                "Telegram временно недоступен. Попробуйте позже.",
                status_code=502,
                code="telegram_unavailable",
            ) from None
        spool.seek(0)
        inline_media = descriptor["media_type"] in {"photo", "voice", "audio"}
        response = send_file(
            spool,
            mimetype=descriptor["mime_type"],
            as_attachment=force_download or not inline_media,
            download_name=descriptor["file_name"],
            conditional=False,
            etag=False,
            last_modified=None,
            max_age=0,
        )
        response.content_length = total
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.call_on_close(spool.close)
        return response
    except Exception:
        spool.close()
        raise
