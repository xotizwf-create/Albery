"""Почта поставщиков: чтение, разбор и отправка через Gmail API.

Отдельный токен и отдельный файл. Почтовый доступ НЕ кладётся в
`/root/.hermes/secure/google_oauth_token.json`: там живёт доступ к Диску, Таблицам и
Документам, и перезапись его согласием с другого аккаунта отняла бы у системы всё
остальное. Почта — это `google_mail_token.json`, права ровно два: читать и отправлять.

Отправка здесь СЫРАЯ и границы автоотправки не знает — её ставит слой выше
(`mail_policy.py`). Разделение намеренное: транспорт не должен решать, что можно
отправить внешнему контрагенту.
"""
from __future__ import annotations

import base64
import json
import os
import re
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Any

MAIL_TOKEN_PATHS = (
    "/root/.hermes/secure/google_mail_token.json",
    os.getenv("ALBERY_MAIL_TOKEN", "").strip() or "/dev/null",
)
# Клиент OAuth берём тот же, что у остальной интеграции: отдельное приложение
# заводить незачем, а согласие всё равно даётся заново и своим набором прав.
OAUTH_CLIENT_PATHS = (
    "/root/.hermes/secure/google_oauth_token.json",
    "/root/.hermes/google_token.json",
)
MAIL_SCOPES = (
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
)

# Один ответ не должен заливать модель: письма бывают с километровыми цитатами.
_BODY_CHAR_BUDGET = 8000
_LIST_CHAR_BUDGET = 20000


class MailNotConnected(RuntimeError):
    """Почтовый доступ ещё не выдан — сообщение об этом обязано быть внятным."""


def oauth_client() -> tuple[str, str]:
    """client_id/client_secret действующего приложения (без обращения к сети)."""
    for path in OAUTH_CLIENT_PATHS:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except OSError:
            continue
        cid, secret = data.get("client_id"), data.get("client_secret")
        if cid and secret:
            return str(cid), str(secret)
    raise MailNotConnected("не найден OAuth-клиент Google в защищённом хранилище")


def _token_path() -> str:
    for path in MAIL_TOKEN_PATHS:
        if path and path != "/dev/null" and os.path.exists(path):
            return path
    return MAIL_TOKEN_PATHS[0]


def _persist(path: str, creds: Any) -> None:
    """Записать токен атомарно и только для root: это ключ от переписки."""
    target = os.path.abspath(path)
    tmp = f"{target}.tmp.{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(creds.to_json().encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def mail_credentials() -> Any:
    from google.oauth2.credentials import Credentials
    import google.auth.transport.requests as gtr

    path = _token_path()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as exc:
        raise MailNotConnected(
            "почтовый доступ не выдан: нет токена. Нужно пройти согласие Google "
            "и сохранить токен через mail.save_token_from_code()."
        ) from exc
    creds = Credentials.from_authorized_user_info(data, data.get("scopes") or list(MAIL_SCOPES))
    if not creds.valid:
        creds.refresh(gtr.Request())
        _persist(path, creds)
    return creds


def save_token_from_code(code: str, redirect_uri: str = "http://localhost:1") -> dict[str, Any]:
    """Обменять код согласия на токен и сохранить его. Возвращает адрес ящика."""
    from google_auth_oauthlib.flow import Flow

    cid, secret = oauth_client()
    flow = Flow.from_client_config(
        {"installed": {"client_id": cid, "client_secret": secret,
                       "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                       "token_uri": "https://oauth2.googleapis.com/token"}},
        scopes=list(MAIL_SCOPES), redirect_uri=redirect_uri,
    )
    flow.fetch_token(code=str(code).strip())
    creds = flow.credentials
    _persist(MAIL_TOKEN_PATHS[0], creds)
    return {"saved_to": MAIL_TOKEN_PATHS[0], "address": mail_address(creds)}


def _service(creds: Any = None) -> Any:
    from googleapiclient.discovery import build
    return build("gmail", "v1", credentials=creds or mail_credentials(), cache_discovery=False)


def mail_address(creds: Any = None) -> str:
    """Какой ящик подключён. Полезно и для проверки, и чтобы не писать не из того адреса."""
    profile = _service(creds).users().getProfile(userId="me").execute()
    return str(profile.get("emailAddress") or "")


# --- разбор письма ---------------------------------------------------------------------

_QUOTE_MARKERS = (
    re.compile(r"^\s*>", re.M),
    re.compile(r"^-{2,}\s*(Пересылаемое|Исходное|Original|Forwarded)", re.M | re.I),
    re.compile(r"^\s*\d{1,2}\s+\w+\s+\d{4}.*(написал|wrote)\s*:?\s*$", re.M | re.I),
    re.compile(r"^\s*On\s+.+wrote:\s*$", re.M),
)


def strip_quoted(text: str) -> str:
    """Убрать процитированную переписку: она удваивает объём и путает разбор.

    Режем по САМОМУ РАННЕМУ маркеру цитаты — ниже него уже чужой текст, который в
    прошлых письмах разбирался как новый и порождал выдуманные «новые условия».
    """
    body = str(text or "")
    cut = len(body)
    for pattern in _QUOTE_MARKERS:
        found = pattern.search(body)
        if found:
            cut = min(cut, found.start())
    return body[:cut].rstrip()


def _decode(data: str) -> str:
    if not data:
        return ""
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return ""


def _walk_parts(payload: dict[str, Any]) -> tuple[str, str, list[dict[str, Any]]]:
    """(текст, html, вложения) из дерева частей письма."""
    text_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[dict[str, Any]] = []

    def walk(part: dict[str, Any]) -> None:
        mime = str(part.get("mimeType") or "")
        body = part.get("body") or {}
        filename = str(part.get("filename") or "")
        if filename and body.get("attachmentId"):
            headers = {str(h.get("name", "")).lower(): str(h.get("value", ""))
                       for h in (part.get("headers") or [])}
            # Картинки подписи вставлены в тело письма и имеют Content-ID. Это логотипы,
            # а не документы поставщика: 17.08.2026 четыре таких PNG попали в разбор и
            # выглядели как содержательные вложения.
            inline = ("inline" in headers.get("content-disposition", "").lower()
                      or bool(headers.get("content-id")))
            attachments.append({
                "filename": filename,
                "mime_type": mime,
                "size": int(body.get("size") or 0),
                "attachment_id": body.get("attachmentId"),
                "inline": inline,
            })
        elif mime == "text/plain":
            text_parts.append(_decode(body.get("data") or ""))
        elif mime == "text/html":
            html_parts.append(_decode(body.get("data") or ""))
        for child in part.get("parts") or []:
            walk(child)

    walk(payload or {})
    return "\n".join(t for t in text_parts if t), "\n".join(html_parts), attachments


def _html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", str(html or ""))
    text = re.sub(r"(?i)<br\s*/?>|</p>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
    return re.sub(r"[ \t]{2,}", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()


def parse_message(raw: dict[str, Any], *, keep_quoted: bool = False) -> dict[str, Any]:
    """Письмо в вид, пригодный для разбора: заголовки, чистый текст, вложения."""
    headers = {str(h.get("name", "")).lower(): str(h.get("value", ""))
               for h in ((raw.get("payload") or {}).get("headers") or [])}
    text, html, attachments = _walk_parts(raw.get("payload") or {})
    body = text or _html_to_text(html)
    if not keep_quoted:
        body = strip_quoted(body)
    truncated = len(body) > _BODY_CHAR_BUDGET
    if truncated:
        body = body[:_BODY_CHAR_BUDGET]
    from_name, from_addr = parseaddr(headers.get("from", ""))
    result = {
        "message_id": raw.get("id"),
        "thread_id": raw.get("threadId"),
        "date": headers.get("date", ""),
        "subject": headers.get("subject", ""),
        "from_name": from_name,
        "from": from_addr or headers.get("from", ""),
        "to": headers.get("to", ""),
        "cc": headers.get("cc", ""),
        "labels": raw.get("labelIds") or [],
        "body": body,
        "attachments": attachments,
    }
    if truncated:
        result["body_truncated"] = True
    return result


# --- чтение ----------------------------------------------------------------------------

# Gmail ограничивает ОДНОВРЕМЕННОСТЬ внутри пакета: пакет на 40 писем вернул шесть
# ответов «429 Too many concurrent requests» (замер 17.08.2026). Поэтому пакеты дробим и
# отбитые письма повторяем — скорость не должна покупаться потерянными ответами
# поставщиков. Молча пропущенное письмо здесь дороже лишней секунды.
_BATCH_CHUNK = 12
_BATCH_ATTEMPTS = 3


def _batch_fetch_metadata(svc: Any, ids: list[str]) -> tuple[dict[str, Any], list[str]]:
    """Карточки писем пакетами, с повтором отбитых. Возвращает (полученное, не сдавшиеся)."""
    import time as _time

    fetched: dict[str, Any] = {}
    pending = list(ids)
    for attempt in range(_BATCH_ATTEMPTS):
        if not pending:
            break
        failed: list[str] = []

        def collect(request_id: str, response: Any, exception: Any) -> None:
            if exception is not None:
                failed.append(request_id)
            else:
                fetched[request_id] = response

        for start in range(0, len(pending), _BATCH_CHUNK):
            batch = svc.new_batch_http_request(callback=collect)
            for mid in pending[start:start + _BATCH_CHUNK]:
                batch.add(svc.users().messages().get(
                    userId="me", id=mid, format="metadata",
                    metadataHeaders=["From", "Subject", "Date", "To"],
                ), request_id=mid)
            batch.execute()

        pending = failed
        if pending and attempt + 1 < _BATCH_ATTEMPTS:
            _time.sleep(0.7 * (attempt + 1))
    return fetched, pending


def mail_search(query: str, max_results: int = 20, *, creds: Any = None) -> dict[str, Any]:
    """Найти письма по синтаксису Gmail (from:, subject:, newer_than:, label: и т.д.).

    Возвращает КРАТКИЕ карточки без тел писем — список не должен стоить столько же,
    сколько чтение. Тело берётся отдельным mail_read по нужному письму.
    """
    svc = _service(creds)
    listing = svc.users().messages().list(
        userId="me", q=str(query or ""), maxResults=max(1, min(int(max_results or 20), 100)),
    ).execute()
    ids = [m["id"] for m in (listing.get("messages") or [])]
    fetched, errors = _batch_fetch_metadata(svc, ids)

    items: list[dict[str, Any]] = []
    used = 0
    for mid in ids:  # порядок выдачи Gmail важен: свежие сверху
        raw = fetched.get(mid)
        if not raw:
            continue
        card = parse_message(raw)
        card.pop("body", None)
        card.pop("attachments", None)
        card["snippet"] = str(raw.get("snippet") or "")[:300]
        size = len(json.dumps(card, ensure_ascii=False))
        if used + size > _LIST_CHAR_BUDGET:
            break
        items.append(card)
        used += size
    result = {
        "query": query,
        "found": len(ids),
        "returned": len(items),
        "messages": items,
        "estimate": listing.get("resultSizeEstimate"),
    }
    if errors:
        # Молчаливо потерянное письмо — это пропущенный ответ поставщика.
        result["failed"] = errors
    if len(items) < len(ids):
        # «Вернул 38 из 53» без пояснения выглядит как потеря писем. Это не потеря, а
        # предел объёма ответа — но сказать об этом обязаны мы, а не пусть догадываются.
        result["note"] = (
            f"Показаны {len(items)} писем из {len(ids)} — упёрлись в предел объёма ответа. "
            "Сузьте запрос (отправитель, период, ярлык), чтобы увидеть остальные."
        )
    return result


def mail_read(message_id: str, *, keep_quoted: bool = False, creds: Any = None) -> dict[str, Any]:
    """Прочитать одно письмо целиком (без процитированной переписки)."""
    raw = _service(creds).users().messages().get(
        userId="me", id=str(message_id), format="full").execute()
    return parse_message(raw, keep_quoted=keep_quoted)


def mail_thread(thread_id: str, *, creds: Any = None) -> dict[str, Any]:
    """Вся переписка одной ветки по порядку — по ней и делаются выводы."""
    raw = _service(creds).users().threads().get(
        userId="me", id=str(thread_id), format="full").execute()
    messages = [parse_message(m) for m in (raw.get("messages") or [])]
    return {"thread_id": thread_id, "count": len(messages), "messages": messages}


# --- отправка (сырая; границы ставит mail_policy) ---------------------------------------

def _build_mime(to: str, subject: str, body: str, *, cc: str = "",
                reply_to_message_id: str = "", from_addr: str = "") -> dict[str, str]:
    message = EmailMessage()
    message["To"] = to
    if cc:
        message["Cc"] = cc
    if from_addr:
        message["From"] = from_addr
    message["Subject"] = subject
    if reply_to_message_id:
        # Без этих заголовков ответ уходит новой веткой, и переписка рассыпается.
        message["In-Reply-To"] = reply_to_message_id
        message["References"] = reply_to_message_id
    message.set_content(body)
    return {"raw": base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")}


def mail_create_draft(to: str, subject: str, body: str, *, cc: str = "",
                      thread_id: str = "", reply_to_message_id: str = "",
                      creds: Any = None) -> dict[str, Any]:
    """Создать черновик. Основной путь для всего, что не разрешено к автоотправке."""
    payload: dict[str, Any] = {"message": _build_mime(
        to, subject, body, cc=cc, reply_to_message_id=reply_to_message_id)}
    if thread_id:
        payload["message"]["threadId"] = thread_id
    draft = _service(creds).users().drafts().create(userId="me", body=payload).execute()
    return {"draft_id": draft.get("id"), "message_id": (draft.get("message") or {}).get("id"),
            "to": to, "subject": subject}


def mail_send_raw(to: str, subject: str, body: str, *, cc: str = "", thread_id: str = "",
                  reply_to_message_id: str = "", creds: Any = None) -> dict[str, Any]:
    """Отправить письмо. НЕ вызывать напрямую из инструментов агента — только через
    mail_policy, который решает, можно ли отправлять без человека."""
    payload = _build_mime(to, subject, body, cc=cc, reply_to_message_id=reply_to_message_id)
    if thread_id:
        payload["threadId"] = thread_id
    sent = _service(creds).users().messages().send(userId="me", body=payload).execute()
    return {"message_id": sent.get("id"), "thread_id": sent.get("threadId"),
            "to": to, "subject": subject}


# --- вложения --------------------------------------------------------------------------
#
# Условия поставщика чаще всего приходят ФАЙЛОМ: у stuff-textile.ru в теле письма общие
# слова, а цены — в «ПРАЙС STUFF - 01.08.2026 - НДС». Письмо без разбора вложения прочитано
# наполовину, поэтому неудача разбора обязана быть СКАЗАНА вслух: пустой текст неотличим
# от «прайса не было», и такой поставщик молча выпадет из работы.

_ATTACHMENT_MAX_BYTES = 25 * 1024 * 1024
_ATTACHMENT_TEXT_BUDGET = 12000
# Провайдер зрения отвечает 429, когда фото идут подряд: фабрики шлют их пачками.
_VISION_ATTEMPTS = 3
_VISION_BACKOFF_S = 2.5
_VISION_PACE_S = 1.2

_IMAGE_EXTS = ("png", "jpg", "jpeg", "webp", "gif", "bmp", "tif", "tiff", "heic")
_DOC_EXTS = ("pdf", "docx", "xlsx", "xlsm", "xls", "doc", "rtf", "odt", "ods", "odp",
             "pptx", "ppt", "csv", "tsv", "txt", "md", "markdown", "json", "xml",
             "htm", "html", "mht", "mhtml", "zip", "log", "yaml", "yml")


# Распознавание картинки возвращает вместе с текстом рассуждения модели («The user wants
# me to extract text…»). Для агента это не содержимое фотографии, а шум, который он примет
# за описание товара. Вырезаем и сам блок, и «голое» вступление до него.
_REASONING_RE = re.compile(r"<think>.*?</think>|<thinking>.*?</thinking>", re.S | re.I)
_REASONING_LEAD_RE = re.compile(
    r"^\s*(?:<think>|<thinking>)?\s*(?:The user wants|Пользователь (?:хочет|просит)).*?"
    r"(?:\n\s*\n|$)", re.S | re.I)


def _strip_model_reasoning(text: str) -> str:
    cleaned = _REASONING_RE.sub(" ", str(text or ""))
    if not _REASONING_RE.search(str(text or "")):
        cleaned = _REASONING_LEAD_RE.sub("", cleaned, count=1)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def _attachment_kind(ext: str) -> str:
    """image — читаем зрением, document — разбираем текстом, other — честно отказываем."""
    if ext in _IMAGE_EXTS:
        return "image"
    if ext in _DOC_EXTS:
        return "document"
    return "other"


def mail_attachment_bytes(message_id: str, attachment_id: str, *, creds: Any = None) -> bytes:
    """Скачать вложение. Пустой attachment_id означает встроенный файл без тела."""
    if not attachment_id:
        raise ValueError("у вложения нет attachment_id — скачивать нечего")
    part = _service(creds).users().messages().attachments().get(
        userId="me", messageId=str(message_id), id=str(attachment_id)).execute()
    return base64.urlsafe_b64decode((part.get("data") or "").encode("ascii") + b"==")


def mail_attachment_text(message_id: str, attachment: dict[str, Any], *,
                         creds: Any = None) -> dict[str, Any]:
    """Разобрать одно вложение в текст. Возвращает и результат, и честную причину отказа."""
    name = str(attachment.get("filename") or "")
    size = int(attachment.get("size") or 0)
    out: dict[str, Any] = {"filename": name, "size": size,
                           "mime_type": attachment.get("mime_type", "")}
    if size > _ATTACHMENT_MAX_BYTES:
        out["error"] = (f"файл {size // 1024 // 1024} МБ — больше предела "
                        f"{_ATTACHMENT_MAX_BYTES // 1024 // 1024} МБ, не скачивался")
        return out
    try:
        blob = mail_attachment_bytes(message_id, attachment.get("attachment_id"), creds=creds)
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"не удалось скачать: {type(exc).__name__}: {str(exc)[:160]}"
        return out

    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    out["kind"] = _attachment_kind(ext)
    try:
        if out["kind"] == "image":
            # Фото товара и сканы прайсов — обычная форма ответа фабрики, их читаем
            # зрением. Берём ОБЁРТКУ с перебором провайдеров, а не codex напрямую:
            # на этой коробке codex не залогинен, и прямой вызов молча возвращал пусто —
            # все фото товара выглядели как нечитаемые.
            #
            # Повторы нужны потому, что в одном письме фабрики шлют по десять фото, и
            # провайдер отвечает 429: без повтора половина карточек товара просто пропала бы,
            # причём с формулировкой «скан плохого качества» — то есть обвинили бы поставщика.
            import time as _time

            from b24bot import _b24_vision_ocr
            text = ""
            for attempt in range(_VISION_ATTEMPTS):
                text = _b24_vision_ocr(blob, name) or ""
                if text:
                    break
                if attempt + 1 < _VISION_ATTEMPTS:
                    _time.sleep(_VISION_BACKOFF_S * (attempt + 1))
        elif out["kind"] == "document":
            from b24bot import _b24_extract_document
            text = _b24_extract_document(blob, name) or ""
        else:
            # НИКАКОГО decode(utf-8) для неизвестных двоичных файлов: он выдаёт мусор,
            # неотличимый от текста. 17.08.2026 так «разобрались» PNG из подписи письма —
            # 12 000 символов бинарного шума, которые уехали бы в анализ как условия
            # поставщика. Честный отказ полезнее правдоподобной чепухи.
            out["error"] = (f"формат «{ext or 'без расширения'}» не поддержан для разбора — "
                            "файл надо открыть человеку")
            return out
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"разбор упал: {type(exc).__name__}: {str(exc)[:160]}"
        return out

    text = _strip_model_reasoning(text).strip()
    if not text:
        # Именно здесь молчание опаснее всего: «прайс не прочитан» и «прайса нет» для
        # человека выглядят одинаково, а решения по ним противоположные.
        #
        # И причину называем ЧЕСТНО. Для картинок пустой результат почти всегда означает,
        # что провайдер зрения не ответил (лимит 429 при пачке фото из одного письма),
        # а не что фабрика прислала плохой скан. Свалить свою неудачу на поставщика —
        # значит забраковать нормального поставщика.
        out["error"] = (
            "распознать не удалось: провайдер зрения не ответил (обычно исчерпан лимит "
            "при пачке фото) — файл надо открыть глазами, поставщик тут ни при чём"
            if out["kind"] == "image" else
            "содержимое не извлеклось (возможно, скан или защищённый файл) — "
            "условия из этого файла надо смотреть глазами")
        return out
    if len(text) > _ATTACHMENT_TEXT_BUDGET:
        out["truncated"] = True
        out["note"] = (f"показаны первые {_ATTACHMENT_TEXT_BUDGET} символов из {len(text)} — "
                       "прайс длинный, ищите нужные позиции по артикулу или категории")
        text = text[:_ATTACHMENT_TEXT_BUDGET]
    out["text"] = text
    return out


def mail_read_full(message_id: str, *, creds: Any = None) -> dict[str, Any]:
    """Письмо ЦЕЛИКОМ: текст плюс разобранные вложения — за один вызов.

    Отдельный вызов на каждое вложение стоил бы лишнего хода модели на каждый файл;
    здесь всё письмо приходит одним куском, как его и читает человек.
    """
    letter = mail_read(message_id, creds=creds)
    parsed: list[dict[str, Any]] = []
    skipped_signature: list[str] = []
    for att in letter.get("attachments") or []:
        # Логотип из подписи разбирать незачем — это трата хода и мусор в анализе.
        if att.get("inline") and _attachment_kind(
                (att.get("filename") or "").rsplit(".", 1)[-1].lower()) == "image":
            skipped_signature.append(att.get("filename", ""))
            continue
        if parsed and att.get("filename", "").rsplit(".", 1)[-1].lower() in _IMAGE_EXTS:
            import time as _t
            _t.sleep(_VISION_PACE_S)
        parsed.append(mail_attachment_text(message_id, att, creds=creds))
    letter["attachments"] = parsed
    if skipped_signature:
        letter["signature_images_skipped"] = skipped_signature
    unreadable = [a["filename"] for a in parsed if a.get("error")]
    if unreadable:
        letter["attachments_unreadable"] = unreadable
        letter["attachments_warning"] = (
            "Не разобраны вложения: " + ", ".join(unreadable) +
            ". Условия могли остаться именно в них — не считайте письмо прочитанным.")
    return letter


# Адрес, на который письмо не доставлено, почтовик называет в теле отбойника.
_BOUNCE_ADDRESS_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_BOUNCE_SENDERS = ("mailer-daemon", "postmaster")


def mail_bounces(newer_than: str = "7d", *, own_address: str = "",
                 creds: Any = None) -> dict[str, Any]:
    """Какие письма НЕ дошли. Возвращает список адресов с причиной.

    Успешная отправка через API означает «принято к доставке», а не «доставлено»:
    17.08.2026 два письма вернулись с «Адрес не найден», и это осталось незамеченным,
    потому что проверялся код ответа, а не судьба письма. Для запроса поставщику такая
    тишина означает потерянный контакт — никто никогда не узнает, что письмо не дошло.
    """
    svc = _service(creds)
    me = own_address or mail_address(creds)
    query = f"from:mailer-daemon OR from:postmaster newer_than:{newer_than}"
    listing = svc.users().messages().list(userId="me", q=query, maxResults=50).execute()
    ids = [m["id"] for m in (listing.get("messages") or [])]

    bounced: list[dict[str, Any]] = []
    for mid in ids:
        raw = svc.users().messages().get(userId="me", id=mid, format="full").execute()
        parsed = parse_message(raw)
        sender = parsed["from"].lower()
        if not any(marker in sender for marker in _BOUNCE_SENDERS):
            continue
        haystack = f"{parsed['subject']} {parsed['body']}"
        candidates = [a for a in _BOUNCE_ADDRESS_RE.findall(haystack)
                      if a.lower() != me.lower()
                      and not any(m in a.lower() for m in _BOUNCE_SENDERS)]
        # Первый адрес в теле отбойника — тот, до которого не дошло.
        failed_address = candidates[0] if candidates else ""
        reason = " ".join(parsed["body"].split())[:200]
        bounced.append({
            "message_id": parsed["message_id"],
            "date": parsed["date"],
            "failed_address": failed_address,
            "subject": parsed["subject"],
            "reason": reason,
        })
    return {
        "period": newer_than,
        "count": len(bounced),
        "bounces": bounced,
        "addresses": sorted({b["failed_address"] for b in bounced if b["failed_address"]}),
    }


def mail_add_label(message_id: str, label: str, *, creds: Any = None) -> dict[str, Any]:
    """Пометить письмо — так агент не разбирает одно и то же дважды."""
    svc = _service(creds)
    existing = {lbl["name"]: lbl["id"] for lbl in
                (svc.users().labels().list(userId="me").execute().get("labels") or [])}
    label_id = existing.get(label)
    if not label_id:
        created = svc.users().labels().create(
            userId="me", body={"name": label, "labelListVisibility": "labelShow",
                               "messageListVisibility": "show"}).execute()
        label_id = created.get("id")
    svc.users().messages().modify(
        userId="me", id=str(message_id), body={"addLabelIds": [label_id]}).execute()
    return {"message_id": message_id, "label": label, "label_id": label_id}
