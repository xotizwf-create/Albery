"""Файлы, которые оператор отправляет клиенту из рабочего окна.

Входящие вложения живут в Telegram и достаются по `file_id` (funnel_workspace_media).
У исходящего файла такого идентификатора ещё нет: браузер приносит байты, а доставку
делает отдельный процесс-воркер спустя секунды. Поэтому файл кладётся на диск, а в
очередь отправки едет только токен — короткий, неугадываемый и не содержащий пути.

Имя файла и MIME-тип клиента дальше не используются как есть: имя чистится, а вся
запись хранится рядом с байтами в служебном JSON. Браузер при отправке передаёт лишь
токен, поэтому подменить имя или тип между загрузкой и отправкой невозможно.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import time
import unicodedata
from pathlib import Path
from typing import Any, BinaryIO

log = logging.getLogger(__name__)

_DEFAULT_DIR = "/var/www/albery/.funnel_outgoing"
_DEFAULT_MAX_BYTES = 20 * 1024 * 1024
#: Бот Telegram не примет документ крупнее 50 МБ, каким бы ни была настройка.
_TELEGRAM_DOCUMENT_LIMIT = 50 * 1024 * 1024
_CHUNK_BYTES = 256 * 1024
#: Сколько дней файл остаётся на диске после загрузки: хватает на повтор и разбор сбоя.
_RETENTION_SECONDS = 7 * 24 * 3600
_VALID_TOKEN = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
_VALID_MIME = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$")


class UploadError(RuntimeError):
    """Понятный оператору отказ по файлу."""

    def __init__(self, message: str, *, code: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def outgoing_dir() -> Path:
    return Path(os.getenv("FUNNEL_WORKSPACE_OUTGOING_DIR", _DEFAULT_DIR))


def outgoing_max_bytes() -> int:
    try:
        value = int(os.getenv("FUNNEL_WORKSPACE_OUTGOING_MAX_BYTES", "") or _DEFAULT_MAX_BYTES)
    except ValueError:
        value = _DEFAULT_MAX_BYTES
    if value <= 0:
        value = _DEFAULT_MAX_BYTES
    return min(value, _TELEGRAM_DOCUMENT_LIMIT)


def safe_file_name(value: Any) -> str:
    """Имя для показа и для Telegram — без разделителей пути и управляющих символов."""

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
        return "файл"
    if len(raw) > 180:
        stem, extension = os.path.splitext(raw)
        raw = f"{stem[: max(1, 180 - len(extension))]}{extension[:20]}"
    return raw


def _clean_mime(value: Any) -> str:
    mime = str(value or "").split(";", 1)[0].strip().lower()
    if not mime or not _VALID_MIME.fullmatch(mime) or len(mime) > 180:
        return "application/octet-stream"
    return mime


def _paths(token: str) -> tuple[Path, Path]:
    base = outgoing_dir()
    return base / f"{token}.bin", base / f"{token}.json"


def store_upload(stream: BinaryIO, *, file_name: Any, mime_type: Any) -> dict[str, Any]:
    """Сохранить файл оператора и вернуть его описание с токеном.

    Читается кусками с проверкой лимита на каждом: сервер не должен принимать в память
    файл, который всё равно отвергнет.
    """

    token = secrets.token_urlsafe(24)
    limit = outgoing_max_bytes()
    directory = outgoing_dir()
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    data_path, meta_path = _paths(token)

    size = 0
    try:
        with open(data_path, "wb") as target:
            while True:
                chunk = stream.read(_CHUNK_BYTES)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise UploadError(
                        f"Файл больше допустимых {limit // (1024 * 1024)} МБ.",
                        code="file_too_large",
                        status_code=413,
                    )
                target.write(chunk)
    except UploadError:
        data_path.unlink(missing_ok=True)
        raise
    except OSError as exc:
        data_path.unlink(missing_ok=True)
        raise UploadError("Не удалось сохранить файл на сервере.", code="upload_failed", status_code=500) from exc

    if size == 0:
        data_path.unlink(missing_ok=True)
        raise UploadError("Файл пустой.", code="empty_file")

    descriptor = {
        "token": token,
        "file_name": safe_file_name(file_name),
        "mime_type": _clean_mime(mime_type),
        "file_size": size,
        "created_at": time.time(),
    }
    try:
        meta_path.write_text(json.dumps(descriptor, ensure_ascii=False), encoding="utf-8")
        os.chmod(data_path, 0o600)
        os.chmod(meta_path, 0o600)
    except OSError as exc:
        data_path.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)
        raise UploadError("Не удалось сохранить файл на сервере.", code="upload_failed", status_code=500) from exc

    sweep_expired()
    return {key: descriptor[key] for key in ("token", "file_name", "mime_type", "file_size")}


def resolve_upload(token: Any) -> dict[str, Any]:
    """Описание сохранённого файла вместе с путём к байтам."""

    clean = str(token or "").strip()
    if not _VALID_TOKEN.fullmatch(clean):
        raise UploadError("Файл не найден.", code="upload_not_found", status_code=400)
    data_path, meta_path = _paths(clean)
    try:
        descriptor = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise UploadError(
            "Файл больше не доступен — загрузите его заново.",
            code="upload_not_found",
            status_code=400,
        ) from None
    if not data_path.is_file():
        raise UploadError(
            "Файл больше не доступен — загрузите его заново.",
            code="upload_not_found",
            status_code=400,
        )
    return {
        "token": clean,
        "path": data_path,
        "file_name": safe_file_name(descriptor.get("file_name")),
        "mime_type": _clean_mime(descriptor.get("mime_type")),
        "file_size": data_path.stat().st_size,
    }


def sweep_expired(*, now: float | None = None) -> int:
    """Удалить давно загруженные файлы. Ошибки чистки не мешают отправке."""

    moment = time.time() if now is None else now
    directory = outgoing_dir()
    removed = 0
    try:
        entries = list(directory.iterdir())
    except OSError:
        return 0
    for entry in entries:
        try:
            if moment - entry.stat().st_mtime <= _RETENTION_SECONDS:
                continue
            entry.unlink()
            removed += 1
        except OSError as exc:  # noqa: PERF203 - чистка не должна ломать отправку
            log.warning("outgoing sweep failed for %s: %s", entry.name, repr(exc)[:120])
    return removed
