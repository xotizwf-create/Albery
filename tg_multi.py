"""Telegram transport for the same logical employee agents used by Bitrix.

Profiles live in ``agents`` and own identity, role, instructions, skills and MCP rights.  This
module owns only Telegram polling, channel-scoped history, explicit access/actor mapping and
durable delivery.  The separate IU customer bot remains in ``tg_agent.py`` and is never exposed
to employee tools.  The module runs inside ``albery-tg`` and therefore must not import
``app``/``b24bot`` because those imports start unrelated schedulers.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import requests

import tg_agent as core
from shared.agent_channel_runtime import ChannelContext, build_agent_policy, load_profile_knowledge
from shared.channel_artifacts import extract_export_artifacts

log = logging.getLogger("tg_multi")

_POLL_TIMEOUT = 50
_RELOAD_S = float(os.getenv("TG_MULTI_RELOAD_S", "60") or 60)
_threads: dict[str, threading.Thread] = {}
_workers_started = False
_legacy_offsets: dict[str, int] = {}
_LEASE_S = int(os.getenv("TG_MULTI_LEASE_S", "240") or 240)
_MAX_ATTEMPTS = int(os.getenv("TG_MULTI_MAX_ATTEMPTS", "3") or 3)
_DELIVERY_ATTEMPTS = int(os.getenv("TG_MULTI_DELIVERY_ATTEMPTS", "5") or 5)
_WORKER_POLL_S = float(os.getenv("TG_MULTI_WORKER_POLL_S", "1") or 1)
_MEDIA_MAX_BYTES = int(os.getenv("TG_MEDIA_MAX_BYTES", str(20 * 1024 * 1024)) or 20 * 1024 * 1024)
_MSK = timezone(timedelta(hours=3))


class TelegramAPIError(RuntimeError):
    def __init__(self, method: str, description: str, *, status_code: int = 0):
        super().__init__(f"{method}: {description[:200]}")
        self.status_code = int(status_code or 0)

    @property
    def retryable(self) -> bool:
        return self.status_code == 429 or self.status_code >= 500


class TelegramDeliveryAmbiguous(RuntimeError):
    """The provider may have accepted the call; automatic replay could duplicate a reply."""


def load_agents() -> list[dict]:
    """Активные агенты с телеграмным мостом. Пустой список — база недоступна или агентов нет.

    Агенты живут в общей таблице `agents` — той же, что и субагенты Битрикса. Отличается только
    мост: там bitrix_bot_id, здесь telegram_bot_token. Благодаря этому у телеграмного агента
    есть всё то же самое: свой коннектор agent-<slug> с набором MCP-инструментов, подключённые
    инструкции, база знаний и личные инструкции."""
    try:
        with core._db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT slug, name, telegram_username AS username,"
                            " telegram_bot_token AS bot_token, role_prompt,"
                            " telegram_bot_user_id AS bot_user_id"
                            " FROM agents WHERE is_active AND telegram_bot_token IS NOT NULL"
                            " ORDER BY created_at")
                return [dict(r) for r in cur.fetchall()]
    except Exception:  # noqa: BLE001
        log.warning("не удалось прочитать список Telegram-агентов", exc_info=True)
        return []


def api(token: str, method: str, http_timeout: int = 35, **params):
    try:
        document = params.pop("document", None)
        if document is not None:
            file_name, data = document
            resp = requests.post(
                f"https://api.telegram.org/bot{token}/{method}",
                data={key: str(value) for key, value in params.items()},
                files={"document": (os.path.basename(str(file_name or "file")), data)},
                timeout=http_timeout,
            )
        else:
            resp = requests.post(f"https://api.telegram.org/bot{token}/{method}", json=params,
                                 timeout=http_timeout)
    except (requests.Timeout, requests.ConnectionError) as exc:
        # Do not expose the token-bearing request URL from requests' exception string.
        raise TelegramDeliveryAmbiguous(f"{method}: network outcome is unknown") from exc
    data = resp.json() if resp.content else {}
    if not (isinstance(data, dict) and data.get("ok")):
        description = str(data.get("description") if isinstance(data, dict) else "Telegram rejected request")
        status = int(data.get("error_code") or resp.status_code or 0) if isinstance(data, dict) else resp.status_code
        raise TelegramAPIError(method, description, status_code=status)
    return data.get("result")


def describe(token: str) -> dict:
    """Кто этот бот в Telegram. Используется и при создании агента — проверить токен."""
    me = api(token, "getMe", http_timeout=15) or {}
    return {"name": str(me.get("first_name") or "").strip(),
            "username": str(me.get("username") or "").strip(),
            "bot_user_id": me.get("id")}


# --- создание бота через @BotFather -------------------------------------------------------------
# Проверено 22.07.2026: аккаунт компании может писать BotFather от лица бизнес-подключения, И ЕГО
# ОТВЕТЫ ПРИХОДЯТ обратно в бизнес-журнал. Значит диалог /newbot агент проводит сам, и владельцу
# не нужно вручную регистрировать бота и переносить токен.

BOTFATHER_ID = 93372553
_TOKEN_RE = re.compile(r"\b(\d{6,}:[A-Za-z0-9_-]{30,})\b")


def _botfather_say(text: str) -> None:
    ok, err = core.send_as_account(BOTFATHER_ID, text)
    if not ok:
        raise RuntimeError(f"BotFather недоступен: {err}")


def _journal_size() -> int:
    """Сколько строк в бизнес-журнале сейчас. Отметка «до отправки» для поиска ответа."""
    try:
        return len(core.BUSINESS_LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        return 0


def _botfather_wait(from_line: int, timeout_s: int = 25) -> str:
    """Дождаться ответа BotFather, появившегося ПОСЛЕ строки from_line.

    Ориентируемся на позицию в файле, а не на время: журнал пишет другой процесс (служба
    albery-tg), и сравнение его меток времени с нашими даёт осечки — при быстром ответе метки
    совпадают до микросекунды, и ответ терялся."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            lines = core.BUSINESS_LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
        for raw in lines[from_line:]:
            try:
                rec = json.loads(raw)
            except Exception:  # noqa: BLE001
                continue
            if str(rec.get("from_id")) == str(BOTFATHER_ID):
                return str(rec.get("text") or "")
        time.sleep(1.5)
    return ""


def create_bot_via_botfather(display_name: str, username: str) -> dict:
    """Провести диалог /newbot и вернуть токен нового бота.

    Каждый шаг ждёт ответа BotFather: он может отказать (имя занято, неверный формат), и тогда
    его текст возвращается владельцу как есть — гадать, что не так, не нужно."""
    username = username.strip().lstrip("@")
    if not username.lower().endswith("bot"):
        raise ValueError("Telegram требует, чтобы имя бота заканчивалось на «bot».")
    # Ответы BotFather не проверяем по конкретным словам: это чужой сервис, формулировки там
    # меняются. Достаточно, что он ответил и не отказал — отказ он говорит прямо.
    def _step(send_text: str, timeout_s: int = 25) -> str:
        mark = _journal_size()
        _botfather_say(send_text)
        got = _botfather_wait(mark, timeout_s=timeout_s)
        if not got.strip():
            raise RuntimeError("BotFather молчит — попробуйте позже.")
        if re.search(r"\b(sorry|invalid|error)\b", got, re.IGNORECASE):
            raise RuntimeError(f"BotFather отказал: {got[:250]}")
        return got

    _step("/newbot")
    _step(display_name)

    # Занятый username — самый частый отказ; _step вернёт слова BotFather как есть.
    reply = _step(username, timeout_s=30)
    found = _TOKEN_RE.search(reply)
    if not found:
        raise RuntimeError(f"BotFather не выдал токен: {reply[:250]}")
    return {"token": found.group(1), "username": username, "name": display_name}


def delete_bot_via_botfather(username: str) -> str:
    """Удалить бота в @BotFather. Необратимо: @username освобождается не сразу."""
    username = username.strip().lstrip("@")
    if not username:
        raise ValueError("Нужен @username бота.")

    def _step(send_text: str, timeout_s: int = 25) -> str:
        mark = _journal_size()
        _botfather_say(send_text)
        got = _botfather_wait(mark, timeout_s=timeout_s)
        if not got.strip():
            raise RuntimeError("BotFather молчит — попробуйте позже.")
        return got

    _step("/deletebot")
    reply = _step("@" + username)
    # BotFather просит подтверждение фразой «Yes, I am totally sure.»
    if "sure" in reply.lower() or "yes" in reply.lower():
        reply = _step("Yes, I am totally sure.")
    if re.search(r"\b(sorry|invalid|error)\b", reply, re.IGNORECASE):
        raise RuntimeError(f"BotFather отказал: {reply[:250]}")
    return reply[:300]


def revoke_token_via_botfather(username: str) -> str:
    """Отозвать токен бота и получить новый (BotFather /revoke).

    Старый токен перестаёт работать сразу, поэтому вызывающий ОБЯЗАН сохранить новый в базу —
    иначе поток опроса останется со старым и бот замолчит."""
    username = username.strip().lstrip("@")
    if not username:
        raise ValueError("Нужен @username бота.")

    def _step(send_text: str, timeout_s: int = 25) -> str:
        mark = _journal_size()
        _botfather_say(send_text)
        got = _botfather_wait(mark, timeout_s=timeout_s)
        if not got.strip():
            raise RuntimeError("BotFather молчит — попробуйте позже.")
        return got

    _step("/revoke")
    reply = _step("@" + username, timeout_s=30)
    found = _TOKEN_RE.search(reply)
    if not found:
        raise RuntimeError(f"BotFather не выдал новый токен: {reply[:250]}")
    return found.group(1)


def _react(token: str, chat_id, message_id, emoji: str) -> None:
    """Реакция на сообщение — тот же сигнал, что у агента в Битриксе: 👀 прочитал, 👍 ответил.
    Косметика: ошибка не должна мешать ответу."""
    if not message_id:
        return
    try:
        api(token, "setMessageReaction", http_timeout=15, chat_id=chat_id,
            message_id=int(message_id),
            reaction=[{"type": "emoji", "emoji": emoji}] if emoji else [])
    except Exception as exc:  # noqa: BLE001
        log.debug("реакция %s не поставлена: %s", emoji, str(exc)[:120])


def _select_access_row(rows: list, tg_user_id, username: str):
    """Prefer immutable Telegram identity; username is bootstrap-only.

    Telegram usernames are mutable and can later belong to somebody else.  Once an allow-list
    row has learned a numeric Telegram id, a matching username must never override a different
    id.  A sender without a numeric id cannot be safely bootstrapped.
    """
    if tg_user_id is None:
        return None
    stable = next(
        (r for r in rows if r["tg_user_id"] is not None
         and str(r["tg_user_id"]) == str(tg_user_id)),
        None,
    )
    if stable is not None or not username:
        return stable
    return next(
        (r for r in rows if r["tg_user_id"] is None
         and str(r["username"] or "").lower() == username),
        None,
    )


def _access_identity(slug: str, sender: dict) -> dict:
    """Resolve Telegram access and an optional explicit Bitrix actor mapping.

    Empty, unavailable or non-matching access is fail-closed.  A username may bootstrap the
    stable Telegram id once; delegated Bitrix writes still require ``bitrix_user_id``.
    """
    username = str(sender.get("username") or "").strip().lstrip("@").lower()
    tg_user_id = sender.get("id")
    try:
        with core._db() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id, username, tg_user_id, bitrix_user_id, display_name "
                        "FROM telegram_bot_access WHERE bot = %s AND is_active ORDER BY id",
                        (slug,),
                    )
                    rows = list(cur.fetchall())
                    if not rows:
                        return {"allowed": False, "reason": "access_not_configured"}
                    match = _select_access_row(rows, tg_user_id, username)
                    if match is None:
                        return {"allowed": False, "reason": "user_not_allowed"}
                    if tg_user_id is not None and match["tg_user_id"] is None:
                        cur.execute(
                            "UPDATE telegram_bot_access SET tg_user_id = %s, "
                            "display_name = COALESCE(display_name, %s) WHERE id = %s",
                            (int(tg_user_id), _display_name(sender) or None, match["id"]),
                        )
                    return {
                        "allowed": True,
                        "username": str(match["username"] or username),
                        "display_name": str(match["display_name"] or _display_name(sender)),
                        "bitrix_user_id": (
                            int(match["bitrix_user_id"]) if match["bitrix_user_id"] is not None else None
                        ),
                    }
    except Exception:  # noqa: BLE001
        log.warning("Telegram access check failed closed for %s", slug, exc_info=True)
        return {"allowed": False, "reason": "access_unavailable"}


def _display_name(sender: dict) -> str:
    return " ".join(
        p for p in (str(sender.get("first_name") or "").strip(),
                    str(sender.get("last_name") or "").strip()) if p
    ).strip()


def _recent_history(slug: str, chat_id, limit: int = 20) -> list[tuple[str, str]]:
    try:
        with core._db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT direction, text FROM telegram_bot_messages "
                    "WHERE bot = %s AND dialog_id = %s AND status = 'ok' ORDER BY id DESC LIMIT %s",
                    (slug, str(chat_id), max(1, min(int(limit), 50))),
                )
                rows = list(reversed(cur.fetchall()))
    except Exception:  # noqa: BLE001
        log.warning("Telegram history unavailable for %s", slug, exc_info=True)
        return []
    return [(str(r["direction"]), str(r["text"] or "")) for r in rows]


def _run_agent_turn(agent: dict, chat_id, sender: dict, text: str, identity: dict,
                    *, history: list[tuple[str, str]] | None = None) -> str:
    slug = str(agent["slug"])
    knowledge = load_profile_knowledge(slug)
    context = ChannelContext(
        channel="telegram",
        conversation_id=str(chat_id),
        requester_name=str(identity.get("display_name") or _display_name(sender)
                           or sender.get("username") or ""),
        requester_platform_id=str(sender.get("id") or ""),
        requester_bitrix_user_id=identity.get("bitrix_user_id"),
    )
    parts = [
        build_agent_policy(
            agent,
            context,
            core_instructions=knowledge["core_instructions"],
            selected_skills=knowledge["selected_skills"],
            personal_instructions=knowledge["personal_instructions"],
            now=datetime.now(_MSK),
        )
    ]
    history = _recent_history(slug, chat_id) if history is None else history
    if history:
        rendered = []
        for direction, body in history:
            label = "Пользователь" if direction == "in" else "Ассистент"
            rendered.append(f"{label}: {body[:6000]}")
        parts.append("История этого Telegram-диалога:\n" + "\n".join(rendered))
    parts.append("Текущее сообщение пользователя:\n" + str(text))
    extra = os.getenv("TG_MULTI_EXTRA_TOOLSETS", "web").strip().strip(",")
    connector = f"agent-{slug}"
    toolsets = f"{connector},{extra}" if extra else connector
    answer = core.hermes_answer("\n\n".join(parts), f"tg-{slug}-{chat_id}", toolsets=toolsets)
    return core._strip_markup((answer or "").strip())


def _denial_text(reason: str) -> str:
    if reason == "access_not_configured":
        return ("Этот внутренний агент пока закрыт: администратор ещё не настроил список доступа. "
                "Ничего не было выполнено.")
    if reason == "access_unavailable":
        return ("Не удалось безопасно проверить доступ. Агент временно не выполняет запросы; "
                "попробуйте позже.")
    return ("У вас нет доступа к этому внутреннему агенту Albery. "
            "Обратитесь к Александру Никитенко.")


def _answer(agent: dict, chat_id, sender: dict, text: str, message_id=None) -> None:
    """Compatibility synchronous path. Live channel-neutral mode uses the durable workers."""
    slug = agent["slug"]
    history = _recent_history(slug, chat_id)
    core.journal(slug, chat_id, "in", text, kind="bot_dm", user=sender)
    identity = _access_identity(slug, sender)
    if not identity.get("allowed"):
        answer = _denial_text(str(identity.get("reason") or ""))
        status, denied = "ok", True
    else:
        denied = False
        _react(agent["bot_token"], chat_id, message_id, "👀")
        try:
            answer = _run_agent_turn(agent, chat_id, sender, text, identity, history=history)
            status = "ok" if answer else "error"
        except Exception as exc:  # noqa: BLE001
            log.warning("мозг не ответил (%s): %s", slug, str(exc)[:200])
            answer, status = "Агент временно не смог завершить ход. Попробуйте позже.", "error"
    try:
        api(agent["bot_token"], "sendMessage", chat_id=chat_id, text=answer[:4000])
        if not denied and status == "ok":
            _react(agent["bot_token"], chat_id, message_id, "👍")
    except Exception as exc:  # noqa: BLE001
        status = "error"
        log.warning("ответ не доставлен (%s): %s", slug, str(exc)[:200])
    core.journal(slug, chat_id, "out", answer, kind="bot_dm", user=sender, status=status,
                 meta={"denied": True} if denied else None)


def _durable_enabled() -> bool:
    return os.getenv("TG_CHANNEL_NEUTRAL_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}


def _next_offset(slug: str) -> int:
    with core._db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT next_offset FROM telegram_agent_offsets WHERE agent_slug = %s", (slug,))
            row = cur.fetchone()
    return int(row["next_offset"] if row else 0)


def _capture_updates(agent: dict, updates: list[dict]) -> int:
    """Commit raw updates and the provider offset in one transaction."""
    if not updates:
        return 0
    slug = str(agent["slug"])
    next_offset = 0
    inserted = 0
    with core._db() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                for update in updates:
                    provider_id = int(update.get("update_id") or 0)
                    if provider_id <= 0:
                        continue
                    cur.execute(
                        "INSERT INTO telegram_agent_updates (agent_slug, provider_update_id, payload) "
                        "VALUES (%s, %s, %s::jsonb) ON CONFLICT (agent_slug, provider_update_id) DO NOTHING",
                        (slug, provider_id, json.dumps(update, ensure_ascii=False)),
                    )
                    inserted += int(cur.rowcount or 0)
                    next_offset = max(next_offset, provider_id + 1)
                if next_offset:
                    cur.execute(
                        "INSERT INTO telegram_agent_offsets (agent_slug, next_offset) VALUES (%s, %s) "
                        "ON CONFLICT (agent_slug) DO UPDATE SET next_offset = "
                        "GREATEST(telegram_agent_offsets.next_offset, EXCLUDED.next_offset), updated_at = now()",
                        (slug, next_offset),
                    )
    return inserted


def _claim_update(worker_id: str) -> dict | None:
    with core._db() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "WITH picked AS (SELECT id FROM telegram_agent_updates "
                    "WHERE status IN ('pending','retry') AND available_at <= now() "
                    "ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1) "
                    "UPDATE telegram_agent_updates u SET status = 'brain_running', attempts = attempts + 1, "
                    "locked_at = now(), locked_until = now() + (%s * interval '1 second'), "
                    "locked_by = %s, updated_at = now() FROM picked WHERE u.id = picked.id RETURNING u.*",
                    (_LEASE_S, worker_id),
                )
                row = cur.fetchone()
    return dict(row) if row else None


def _agent_for_slug(slug: str) -> dict | None:
    return next((agent for agent in load_agents() if str(agent.get("slug")) == str(slug)), None)


def _message_has_supported_content(message: dict) -> bool:
    return bool(
        str(message.get("text") or message.get("caption") or "").strip()
        or message.get("photo")
        or message.get("voice")
        or message.get("audio")
        or message.get("document")
    )


def _telegram_file_bytes(token: str, file_id: str, declared_size=None) -> bytes:
    if declared_size is not None and int(declared_size or 0) > _MEDIA_MAX_BYTES:
        raise ValueError("Telegram-файл превышает безопасный лимит размера")
    meta = api(token, "getFile", http_timeout=25, file_id=file_id) or {}
    file_path = str(meta.get("file_path") or "").strip()
    if not file_path:
        raise RuntimeError("Telegram не вернул путь к файлу")
    try:
        with requests.get(
            f"https://api.telegram.org/file/bot{token}/{file_path}", stream=True, timeout=60
        ) as response:
            if response.status_code != 200:
                raise RuntimeError(f"Telegram не отдал файл: HTTP {response.status_code}")
            chunks = []
            size = 0
            for chunk in response.iter_content(65536):
                if not chunk:
                    continue
                size += len(chunk)
                if size > _MEDIA_MAX_BYTES:
                    raise ValueError("Telegram-файл превышает безопасный лимит размера")
                chunks.append(chunk)
            return b"".join(chunks)
    except (requests.Timeout, requests.ConnectionError) as exc:
        # Never include requests' token-bearing URL in an exception or log line.
        raise RuntimeError("Не удалось безопасно скачать Telegram-файл") from exc


def _telegram_message_text(agent: dict, message: dict, identity: dict) -> str:
    """Turn Telegram media into channel-neutral text without giving Groq agent authority."""
    base = str(message.get("text") or message.get("caption") or "").strip()
    media = None
    kind = ""
    name = ""
    mime = ""
    photos = message.get("photo") or []
    if photos:
        media = photos[-1]
        kind, name, mime = "image", "telegram-photo.jpg", "image/jpeg"
    elif message.get("voice"):
        media = message["voice"]
        kind, name, mime = "audio", "telegram-voice.ogg", str(media.get("mime_type") or "audio/ogg")
    elif message.get("audio"):
        media = message["audio"]
        name = str(media.get("file_name") or "telegram-audio.mp3")
        kind, mime = "audio", str(media.get("mime_type") or "audio/mpeg")
    elif message.get("document"):
        media = message["document"]
        name = str(media.get("file_name") or "telegram-document")
        mime = str(media.get("mime_type") or "")
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if mime.startswith("image/") or ext in {"png", "jpg", "jpeg", "gif", "webp"}:
            kind = "image"
        elif mime.startswith("audio/") or ext in {
            "mp3", "m4a", "aac", "ogg", "oga", "opus", "wav", "webm", "flac"
        }:
            kind = "audio"
        else:
            kind = "document"
    if not media:
        return base
    data = _telegram_file_bytes(
        str(agent["bot_token"]), str(media.get("file_id") or ""), media.get("file_size")
    )
    from shared.media_ingestion import extract_document, recognize_image, transcribe_audio
    if kind == "image":
        extracted = recognize_image(data, name)
        label = "Распознанный скриншот/изображение"
    elif kind == "audio":
        extracted = transcribe_audio(data, name)
        label = "Расшифровка голосового/аудио"
    else:
        extracted = extract_document(data, name)
        label = "Извлечённый текст документа"
    try:
        import attachments
        attachment_id = attachments.store_attachment(
            data=data,
            file_name=name,
            kind=kind,
            extracted_text=extracted or "",
            agent_slug=str(agent["slug"]),
            dialog_id=str((message.get("chat") or {}).get("id") or ""),
            bitrix_user_id=identity.get("bitrix_user_id"),
            mime=mime or None,
        )
    except Exception:  # noqa: BLE001
        log.warning("Telegram attachment store failed for %s", agent["slug"], exc_info=True)
        attachment_id = None
    if extracted:
        media_block = f"[{label} «{name}»" + (
            f", attachment_id={attachment_id}" if attachment_id else ""
        ) + f"]\n{extracted[:12000]}"
    else:
        media_block = (
            f"[Файл «{name}» получен, но прочитать его не удалось"
            + (f", attachment_id={attachment_id}" if attachment_id else "")
            + ". Честно сообщи об этом и не выдумывай содержимое.]"
        )
    return (base + "\n\n" + media_block).strip()


def _finish_update(update: dict, *, chat_id, answer: str, review: bool = False,
                   error: str | None = None) -> None:
    status = "review" if review else "done"
    idem = f"telegram-agent-update:{update['agent_slug']}:{update['provider_update_id']}"
    clean_answer, artifacts, invalid_count = extract_export_artifacts(answer)
    if review:
        # Ambiguous model/tool outcomes must never make a generated file look authoritative.
        artifacts = []
    artifact_tokens: list[str] = []
    if artifacts:
        try:
            import attachments
            for artifact in artifacts:
                token = attachments.store_attachment(
                    data=artifact["data"],
                    file_name=artifact["display_name"],
                    kind="agent_doc",
                    extracted_text="",
                    agent_slug=str(update["agent_slug"]),
                    dialog_id=str(chat_id),
                    mime=artifact.get("mime"),
                )
                if not token:
                    raise RuntimeError("artifact persistence failed")
                artifact_tokens.append(token)
        except Exception as exc:  # noqa: BLE001
            log.warning("Telegram generated artifact persistence failed for %s: %s",
                        update["agent_slug"], type(exc).__name__)
            artifact_tokens = []
            clean_answer = (clean_answer + "\n\nНе удалось безопасно сохранить файл для отправки. "
                            "Повторите создание документа.").strip()
    if invalid_count and not clean_answer:
        clean_answer = "Не удалось безопасно приложить сформированный файл. Повторите создание документа."
    with core._db() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO telegram_agent_outbox "
                    "(agent_slug, update_id, chat_id, text, idempotency_key, part_no) "
                    "VALUES (%s, %s, %s, %s, %s, 0) ON CONFLICT (idempotency_key) DO NOTHING",
                    (update["agent_slug"], update["id"], str(chat_id), clean_answer[:4000], idem),
                )
                for part_no, token in enumerate(artifact_tokens, start=1):
                    cur.execute(
                        "INSERT INTO telegram_agent_outbox "
                        "(agent_slug, update_id, chat_id, text, idempotency_key, part_no, attachment_token) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                        "ON CONFLICT (idempotency_key) DO NOTHING",
                        (update["agent_slug"], update["id"], str(chat_id), "",
                         f"{idem}:artifact:{part_no}", part_no, token),
                    )
                cur.execute(
                    "UPDATE telegram_agent_updates SET status = %s, locked_at = NULL, locked_until = NULL, "
                    "locked_by = NULL, last_error = %s, completed_at = now(), updated_at = now() WHERE id = %s",
                    (status, (error or "")[:500] or None, update["id"]),
                )


def _ignore_update(update_id: int, reason: str) -> None:
    with core._db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE telegram_agent_updates SET status = 'ignored', last_error = %s, "
                "locked_at = NULL, locked_until = NULL, locked_by = NULL, completed_at = now(), "
                "updated_at = now() WHERE id = %s",
                (reason[:500], update_id),
            )


def _process_update(update: dict) -> None:
    payload = update.get("payload")
    if isinstance(payload, str):
        payload = json.loads(payload)
    payload = payload if isinstance(payload, dict) else {}
    msg = payload.get("message") or {}
    chat = msg.get("chat") or {}
    sender = msg.get("from") or {}
    text_value = (msg.get("text") or msg.get("caption") or "").strip()
    if chat.get("type") != "private" or not _message_has_supported_content(msg) or sender.get("is_bot"):
        _ignore_update(int(update["id"]), "unsupported or empty update")
        return
    agent = _agent_for_slug(str(update["agent_slug"]))
    if not agent:
        _ignore_update(int(update["id"]), "agent disabled or removed")
        return
    chat_id = chat.get("id")
    history = _recent_history(agent["slug"], chat_id)
    core.journal(agent["slug"], chat_id, "in", text_value, kind="bot_dm", user=sender,
                 meta={"provider_update_id": update["provider_update_id"]})
    identity = _access_identity(agent["slug"], sender)
    if not identity.get("allowed"):
        _finish_update(update, chat_id=chat_id,
                       answer=_denial_text(str(identity.get("reason") or "")))
        return
    # The durable brain turn may take tens of seconds. Preserve the old channel UX without
    # weakening the queue boundary: this content-free acknowledgement is best-effort and its
    # provider outcome never controls update, brain or delivery state.
    _react(str(agent["bot_token"]), chat_id, msg.get("message_id"), "👀")
    try:
        text_value = _telegram_message_text(agent, msg, identity)
    except Exception as exc:  # noqa: BLE001
        log.warning("Telegram media could not be prepared for %s: %s", agent["slug"], type(exc).__name__)
        _finish_update(
            update,
            chat_id=chat_id,
            answer=("Не удалось безопасно получить или прочитать вложение. Никаких действий по "
                    "этому запросу не выполнено; пришлите файл ещё раз или продублируйте текстом."),
            error="media preparation failed: " + type(exc).__name__,
        )
        return
    try:
        answer = _run_agent_turn(agent, chat_id, sender, text_value, identity, history=history)
        if not answer:
            raise RuntimeError("empty agent answer")
    except Exception as exc:  # noqa: BLE001
        # A live model turn may already have performed an external write. Never replay it after
        # an unclassified failure; make the ambiguous outcome visible to the user and operator.
        log.warning("durable Telegram brain needs review (%s): %s", agent["slug"], str(exc)[:200])
        answer = ("Ход прервался после начала работы. Возможное действие не будет повторено "
                  "автоматически. Не дублируйте запрос, пока не проверите результат.")
        _finish_update(update, chat_id=chat_id, answer=answer, review=True, error=str(exc))
        return
    _finish_update(update, chat_id=chat_id, answer=answer)


def _claim_outbox(worker_id: str) -> dict | None:
    with core._db() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "WITH picked AS (SELECT id FROM telegram_agent_outbox "
                    "WHERE status IN ('pending','retry') AND available_at <= now() "
                    "ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1) "
                    "UPDATE telegram_agent_outbox o SET status = 'leased', locked_at = now(), "
                    "locked_until = now() + (%s * interval '1 second'), locked_by = %s, "
                    "updated_at = now() FROM picked WHERE o.id = picked.id RETURNING o.*",
                    (_LEASE_S, worker_id),
                )
                row = cur.fetchone()
    return dict(row) if row else None


def _set_outbox_status(outbox_id: int, status: str, *, error: str | None = None,
                       provider_message_id: str | None = None, delay_s: int = 0) -> None:
    with core._db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE telegram_agent_outbox SET status = %s, last_error = %s, "
                "provider_message_id = COALESCE(%s, provider_message_id), "
                "available_at = now() + (%s * interval '1 second'), locked_at = NULL, "
                "locked_until = NULL, locked_by = NULL, sent_at = CASE WHEN %s = 'sent' THEN now() "
                "ELSE sent_at END, updated_at = now() WHERE id = %s",
                (status, (error or "")[:500] or None, provider_message_id, delay_s, status, outbox_id),
            )


def _finalize_update_reaction(agent: dict, item: dict) -> bool:
    """Replace `eyes` with `thumbs up` only after the whole captured reply is sent.

    The reaction is cosmetic. Any database/provider problem must leave the committed delivery
    untouched and must never cause the outbox item to be replayed.
    """
    update_id = item.get("update_id")
    if update_id is None:
        return False
    try:
        with core._db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT u.payload FROM telegram_agent_updates u "
                    "WHERE u.id = %s AND u.agent_slug = %s AND u.status = 'done' "
                    "AND NOT EXISTS (SELECT 1 FROM telegram_agent_outbox o "
                    "WHERE o.update_id = u.id AND o.status <> 'sent')",
                    (update_id, str(item["agent_slug"])),
                )
                row = cur.fetchone()
        if not row:
            return False
        payload = row.get("payload") if hasattr(row, "get") else row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        payload = payload if isinstance(payload, dict) else {}
        message = payload.get("message") or {}
        sender = message.get("from") or {}
        chat_id = (message.get("chat") or {}).get("id")
        message_id = message.get("message_id")
        if chat_id is None or message_id is None or str(chat_id) != str(item.get("chat_id")):
            return False
        if not _access_identity(str(item["agent_slug"]), sender).get("allowed"):
            return False
        _react(str(agent["bot_token"]), chat_id, message_id, "👍")
        return True
    except Exception as exc:  # noqa: BLE001
        log.debug(
            "final Telegram reaction was not set for %s: %s",
            item.get("agent_slug"),
            type(exc).__name__,
        )
        return False


def _process_outbox(item: dict) -> None:
    agent = _agent_for_slug(str(item["agent_slug"]))
    if not agent:
        _set_outbox_status(int(item["id"]), "error", error="agent disabled or removed")
        return
    with core._db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE telegram_agent_outbox SET status = 'sending', attempts = attempts + 1, "
                "locked_until = now() + (%s * interval '1 second'), updated_at = now() WHERE id = %s",
                (_LEASE_S, item["id"]),
            )
    try:
        if item.get("attachment_token"):
            import attachments
            blob = attachments.attachment_bytes(str(item["attachment_token"]))
            if not blob:
                raise TelegramAPIError("sendDocument", "stored artifact is unavailable", status_code=400)
            data, file_name = blob
            result = api(
                agent["bot_token"],
                "sendDocument",
                chat_id=item["chat_id"],
                document=(file_name, data),
            )
        else:
            result = api(agent["bot_token"], "sendMessage", chat_id=item["chat_id"], text=item["text"])
    except TelegramDeliveryAmbiguous as exc:
        _set_outbox_status(int(item["id"]), "review", error=str(exc))
        return
    except TelegramAPIError as exc:
        attempts = int(item.get("attempts") or 0) + 1
        status = "retry" if exc.retryable and attempts < _DELIVERY_ATTEMPTS else "error"
        _set_outbox_status(int(item["id"]), status, error=str(exc), delay_s=30 if status == "retry" else 0)
        return
    except Exception as exc:  # noqa: BLE001
        _set_outbox_status(int(item["id"]), "review", error="unknown provider outcome")
        log.warning("Telegram delivery outcome unknown for %s: %s", item["agent_slug"], type(exc).__name__)
        return
    provider_id = result.get("message_id") if isinstance(result, dict) else None
    _set_outbox_status(int(item["id"]), "sent", provider_message_id=str(provider_id or "") or None)
    _finalize_update_reaction(agent, item)
    journal_text = item["text"] or ("📎 " + str(item.get("attachment_token") or "file"))
    core.journal(item["agent_slug"], item["chat_id"], "out", journal_text, kind="bot_dm",
                 status="ok", meta={"durable_outbox_id": item["id"],
                                      "native_artifact": bool(item.get("attachment_token"))})


def _recover_durable() -> None:
    """Recover only stages whose external outcome is known; ambiguous stages stop for review."""
    with core._db() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE telegram_agent_outbox SET status = 'retry', available_at = now(), "
                    "locked_at = NULL, locked_until = NULL, locked_by = NULL, updated_at = now() "
                    "WHERE status = 'leased' AND locked_until < now()"
                )
                cur.execute(
                    "UPDATE telegram_agent_outbox SET status = 'review', "
                    "last_error = 'worker stopped during provider call; outcome unknown', "
                    "locked_at = NULL, locked_until = NULL, locked_by = NULL, updated_at = now() "
                    "WHERE status = 'sending' AND locked_until < now()"
                )
                cur.execute(
                    "SELECT id, agent_slug, provider_update_id, payload FROM telegram_agent_updates "
                    "WHERE status = 'brain_running' AND locked_until < now() FOR UPDATE"
                )
                stuck = list(cur.fetchall())
                for row in stuck:
                    payload = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
                    chat_id = ((payload.get("message") or {}).get("chat") or {}).get("id")
                    cur.execute(
                        "UPDATE telegram_agent_updates SET status = 'review', "
                        "last_error = 'worker stopped during model turn; external effects may exist', "
                        "locked_at = NULL, locked_until = NULL, locked_by = NULL, updated_at = now() "
                        "WHERE id = %s",
                        (row["id"],),
                    )
                    if chat_id is not None:
                        cur.execute(
                            "INSERT INTO telegram_agent_outbox "
                            "(agent_slug, update_id, chat_id, text, idempotency_key) "
                            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (idempotency_key) DO NOTHING",
                            (row["agent_slug"], row["id"], str(chat_id),
                             "Ход был прерван. Возможное действие не повторяется автоматически; "
                             "проверьте результат перед новым запросом.",
                             f"telegram-agent-update:{row['agent_slug']}:{row['provider_update_id']}"),
                        )


def _brain_worker_loop() -> None:
    worker_id = "tg-brain-" + uuid4().hex[:8]
    while True:
        try:
            row = _claim_update(worker_id)
            if row:
                _process_update(row)
            else:
                time.sleep(_WORKER_POLL_S)
        except Exception:  # noqa: BLE001
            log.exception("Telegram durable brain worker failed")
            time.sleep(_WORKER_POLL_S)


def _outbox_worker_loop() -> None:
    worker_id = "tg-outbox-" + uuid4().hex[:8]
    while True:
        try:
            row = _claim_outbox(worker_id)
            if row:
                _process_outbox(row)
            else:
                time.sleep(_WORKER_POLL_S)
        except Exception:  # noqa: BLE001
            log.exception("Telegram durable outbox worker failed")
            time.sleep(_WORKER_POLL_S)


def _start_durable_workers() -> None:
    global _workers_started
    if _workers_started:
        return
    _recover_durable()
    _workers_started = True
    threading.Thread(target=_brain_worker_loop, daemon=True, name="tg-agent-brain").start()
    threading.Thread(target=_outbox_worker_loop, daemon=True, name="tg-agent-outbox").start()


def _is_wanted(slug: str, token: str | None = None) -> bool:
    """Работает ли этот агент с этим токеном.

    Удалили в кабинете — бот обязан замолчать, а не отвечать от имени несуществующего агента
    (владелец поймал это 22.07.2026). Отозвали токен в BotFather и выпустили новый — поток со
    старым токеном должен завершиться, иначе бот навсегда останется молчащим: Telegram отвечает
    ему 401, а перечитать токен потоку негде."""
    try:
        with core._db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT telegram_bot_token FROM agents WHERE slug = %s AND is_active"
                            " AND telegram_bot_token IS NOT NULL", (slug,))
                row = cur.fetchone()
                if not row:
                    return False
                return token is None or str(row["telegram_bot_token"]) == str(token)
    except Exception:  # noqa: BLE001
        # База недоступна — не глушим уже работающего агента из-за сбоя связи.
        return True


def _poll(agent: dict) -> None:
    slug = agent["slug"]
    token = agent["bot_token"]
    log.info("Telegram-агент «%s» (@%s) начал работу", agent.get("name"), agent.get("username"))
    while True:
        if not _is_wanted(slug, token):
            log.info("агент %s удалён, выключен или ему сменили токен — поток остановлен", slug)
            _threads.pop(slug, None)
            return
        try:
            offset = (_next_offset(slug) if _durable_enabled()
                      else _legacy_offsets.get(slug, 0))
            updates = api(token, "getUpdates", http_timeout=_POLL_TIMEOUT + 15,
                          timeout=_POLL_TIMEOUT, offset=offset,
                          allowed_updates=["message"])
        except Exception as exc:  # noqa: BLE001
            log.warning("getUpdates (%s): %s", slug, str(exc)[:150])
            time.sleep(5)
            continue
        if _durable_enabled():
            try:
                _capture_updates(agent, list(updates or []))
            except Exception:  # noqa: BLE001
                # The offset stays unchanged when capture fails; Telegram will replay and the DB
                # unique key will deduplicate anything already committed.
                log.exception("durable Telegram capture failed for %s", slug)
                time.sleep(2)
            continue
        for upd in updates or []:
            _legacy_offsets[slug] = max(
                _legacy_offsets.get(slug, 0), int(upd.get("update_id", 0)) + 1
            )
            msg = upd.get("message") or {}
            chat = msg.get("chat") or {}
            text = (msg.get("text") or msg.get("caption") or "").strip()
            sender = msg.get("from") or {}
            if chat.get("type") != "private" or not text or sender.get("is_bot"):
                continue
            # Проверяем перед КАЖДЫМ ответом: длинный опрос висит до минуты, и без этой
            # проверки удалённый агент успел бы ответить ещё раз.
            if not _is_wanted(slug, token):
                log.info("агент %s удалён или сменил токен — сообщение без ответа", slug)
                _threads.pop(slug, None)
                return
            try:
                _answer(agent, chat.get("id"), sender, text, msg.get("message_id"))
            except Exception:  # noqa: BLE001
                log.exception("ход агента %s упал", slug)


def start_all() -> None:
    """Поднять поток на каждого активного агента и следить за появлением новых."""
    if _durable_enabled():
        _start_durable_workers()

    def supervisor():
        while True:
            for agent in load_agents():
                slug = agent["slug"]
                alive = _threads.get(slug)
                if alive and alive.is_alive():
                    continue
                th = threading.Thread(target=_poll, args=(agent,), daemon=True,
                                      name=f"tg-{slug}")
                _threads[slug] = th
                th.start()
            time.sleep(_RELOAD_S)

    threading.Thread(target=supervisor, daemon=True, name="tg-multi-supervisor").start()
