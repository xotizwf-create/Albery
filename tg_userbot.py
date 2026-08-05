"""MTProto user session of the owner's manager account (@AlberyAIManager) — «глаза» агента.

The Bot API cannot see the channels/groups the account is subscribed to; a USER session can
see everything the account sees. This module keeps that session on the box and gives the
agent read access: list dialogs (including ЗАКРЫТЫЕ каналы и закрытые групповые чаты, which
have no @username and no public web preview), read a single chat, search inside it, pull
fresh posts for the weekly digest.

Security: the session file (.tg_userbot.session, chmod 600, gitignored) is равносильно
полному доступу к аккаунту — it never leaves the server and is never committed. Login is a
two-step interactive flow (scripts/tg_userbot_login.py) because Telegram sends the code to
the owner's app.

telethon is imported lazily so the rest of the service (and the test suite) works without it.
"""
from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

APP_ROOT = Path(__file__).resolve().parent
SESSION_BASE = APP_ROOT / ".tg_userbot"           # telethon appends .session
SESSION_FILE = APP_ROOT / ".tg_userbot.session"
MSK = ZoneInfo("Europe/Moscow")

# Диалогов у живого аккаунта много, а закрытый чат ищется только перебором (по @username его
# не найти — его нет). Потолок общий для поиска и перечисления.
DIALOG_SCAN_LIMIT = 1000


_AUTH_PROBE: dict[str, tuple[float, bool]] = {}
AUTH_PROBE_TTL_S = 300


def _probe_authorized() -> bool:
    """Живая проверка: сессия действительно вошла в аккаунт (нужен один сетевой вызов)."""
    async def go():
        client = _client()
        await client.connect()
        try:
            return bool(await client.is_user_authorized())
        finally:
            await client.disconnect()
    return asyncio.run(go())


def session_ready(max_age_s: int = AUTH_PROBE_TTL_S) -> bool:
    """Сессия есть И она авторизована.

    Одного файла мало: telethon создаёт .tg_userbot.session при первом же подключении — даже
    если вход не завершён. 05.08.2026 незаконченная попытка входа оставила такой пустой файл,
    и проверка «файл есть» стала бы враньём: инструменты рапортовали бы о готовности и падали
    бы на первом же запросе внутренней ошибкой вместо внятного «сессия не подключена».
    Авторизация проверяется сетевым вызовом, поэтому ответ кэшируется на max_age_s.
    """
    if not SESSION_FILE.is_file():
        return False
    cached = _AUTH_PROBE.get("state")
    if cached and (time.time() - cached[0]) < max_age_s:
        return cached[1]
    try:
        authorized = _probe_authorized()
    except Exception:  # noqa: BLE001 — сеть/ключи недоступны: считаем, что сессии нет
        authorized = False
    _AUTH_PROBE["state"] = (time.time(), authorized)
    return authorized


def require_session() -> None:
    """Единая формулировка отказа: агент должен объяснить владельцу, что именно сделать."""
    if not session_ready():
        raise RuntimeError(
            "Сессия аккаунта @AlberyAIManager не подключена, поэтому закрытые каналы и "
            "закрытые групповые чаты недоступны. Подключение делается на сервере: "
            "scripts/tg_userbot_login.py request <телефон> + confirm <код из Telegram>, "
            "либо qr — привязкой устройства."
        )


def _client():
    from telethon import TelegramClient  # lazy: optional dependency
    from shared.db import load_env_value  # .env-фолбэк, когда процесс запущен не из systemd

    api_id = int(load_env_value("TG_API_ID") or 0)
    api_hash = load_env_value("TG_API_HASH")
    if not api_id or not api_hash:
        raise RuntimeError("TG_API_ID/TG_API_HASH не настроены в .env")
    return TelegramClient(str(SESSION_BASE), api_id, api_hash)


def _secure_session() -> None:
    import os

    try:
        if SESSION_FILE.is_file():
            os.chmod(SESSION_FILE, 0o600)
    except OSError:
        pass


def _run(coro):
    result = asyncio.run(coro)
    _secure_session()
    return result


# --- shape helpers -----------------------------------------------------------------------

def _public_username(entity) -> str | None:
    """Публичное имя чата, если оно есть. Его отсутствие = чат закрытый (только по инвайту)."""
    name = getattr(entity, "username", None)
    if name:
        return name
    for extra in (getattr(entity, "usernames", None) or []):
        found = getattr(extra, "username", None)
        if found:
            return found
    return None


def _dialog_kind(dialog) -> str:
    if dialog.is_channel and not dialog.is_group:
        return "channel"
    return "group" if dialog.is_group else "private"


def _msk_stamp(value: datetime | None) -> str | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(MSK).strftime("%d.%m.%Y %H:%M")


def _dialog_row(dialog) -> dict:
    entity = dialog.entity
    username = _public_username(entity)
    kind = _dialog_kind(dialog)
    return {
        "id": dialog.id,
        "name": dialog.name or "",
        "type": kind,
        # «Закрытый» = нет публичного @username: ни ссылки t.me/<имя>, ни веб-превью t.me/s/<имя>,
        # читать такой чат можно ТОЛЬКО этой сессией.
        "closed": kind != "private" and not username,
        "username": username,
        "unread": dialog.unread_count,
        "participants": getattr(entity, "participants_count", None),
        "last_message_at": _msk_stamp(getattr(dialog, "date", None)),
    }


def _marked_id(entity) -> int:
    """Id в том же «отмеченном» виде, в каком его показывают диалоги: канал и супергруппа —
    -100…, обычная группа — -…, человек — как есть. Так id из join_chat совпадает с id из
    list_dialogs, и чат потом находится по нему."""
    raw = int(getattr(entity, "id", 0) or 0)
    if raw <= 0 or getattr(entity, "first_name", None) is not None:
        return raw
    if hasattr(entity, "broadcast") or hasattr(entity, "megagroup"):
        return int(f"-100{raw}")
    return -raw


def _entity_row(entity) -> dict:
    """Тот же вид, что и у диалога, но для чата, полученного не из списка диалогов
    (только что вступили по приглашению / публичный канал без подписки)."""
    username = _public_username(entity)
    kind = "channel" if getattr(entity, "broadcast", False) else "group"
    return {
        "id": _marked_id(entity),
        "name": getattr(entity, "title", "") or "",
        "type": kind,
        "closed": not username,
        "username": username,
        "unread": 0,
        "participants": getattr(entity, "participants_count", None),
        "last_message_at": None,
    }


def _id_variants(chat_id) -> set[str]:
    """'-1001234567890' и '1234567890' — один и тот же чат: Telegram показывает оба вида."""
    text = str(chat_id)
    out = {text, text.lstrip("-")}
    if text.startswith("-100"):
        out.add(text[4:])
    return out


def _normalize_query(raw: str) -> str:
    query = (raw or "").strip()
    query = re.sub(r"^https?://", "", query, flags=re.IGNORECASE)
    query = re.sub(r"^(t\.me|telegram\.me)/(s/)?", "", query, flags=re.IGNORECASE)
    return query.strip("/").strip()


def _dialog_score(row: dict, query: str) -> int:
    """Насколько диалог подходит запросу: 3 — id, 2 — @username, 1 — точное имя, 0 — часть имени."""
    plain = _normalize_query(query).lstrip("@").casefold()
    if not plain:
        return -1
    if plain in {v.casefold() for v in _id_variants(row["id"])}:
        return 3
    if row.get("username") and row["username"].casefold() == plain:
        return 2
    title = (row.get("name") or "").casefold()
    if title == plain:
        return 1
    return 0 if plain in title else -1


# --- reading -----------------------------------------------------------------------------

def whoami() -> dict:
    async def go():
        async with _client() as client:
            me = await client.get_me()
            return {"id": me.id, "username": me.username,
                    "name": " ".join(x for x in (me.first_name, me.last_name) if x)}
    return _run(go())


def list_dialogs(limit: int = DIALOG_SCAN_LIMIT, kinds: list[str] | None = None,
                 only_closed: bool = False, query: str | None = None) -> list[dict]:
    """Everything the account sees: channels, groups, private chats.

    kinds — фильтр по типу ('channel'/'group'/'private'); only_closed — только закрытые
    каналы и группы (без публичного @username); query — часть названия или @username.
    """
    require_session()
    wanted = {k.strip().lower() for k in (kinds or []) if k.strip()}
    needle = _normalize_query(query or "").lstrip("@").casefold()

    async def go():
        out = []
        async with _client() as client:
            async for dialog in client.iter_dialogs(limit=limit):
                row = _dialog_row(dialog)
                if wanted and row["type"] not in wanted:
                    continue
                if only_closed and not row["closed"]:
                    continue
                if needle and needle not in (row["name"] or "").casefold() \
                        and needle not in (row["username"] or "").casefold() \
                        and needle not in {v.casefold() for v in _id_variants(row["id"])}:
                    continue
                out.append(row)
        return out
    return _run(go())


async def _find_dialog(client, chat: str):
    """(entity, строка чата) по id, @username или названию. Закрытые чаты ищутся ТОЛЬКО перебором
    диалогов: у них нет публичного имени, и client.get_entity по названию их не находит."""
    best, best_score = None, -1
    async for dialog in client.iter_dialogs(limit=DIALOG_SCAN_LIMIT):
        row = _dialog_row(dialog)
        score = _dialog_score(row, chat)
        if score > best_score:
            best, best_score = (dialog.entity, row), score
        if score == 3:
            break
    if best_score >= 0:
        return best
    plain = _normalize_query(chat)
    if plain and not plain.startswith("+"):
        try:  # публичный канал, на который аккаунт не подписан — его всё равно видно
            entity = await client.get_entity(plain if plain.startswith("@") else "@" + plain)
        except Exception as exc:  # noqa: BLE001
            raise LookupError(
                f"Чат «{chat}» не найден среди диалогов аккаунта @AlberyAIManager ({str(exc)[:80]}). "
                "Закрытый канал или группу видно только после вступления — пришли ссылку-приглашение."
            ) from exc
        return entity, _entity_row(entity)
    raise LookupError(
        f"Чат «{chat}» не найден среди диалогов аккаунта @AlberyAIManager. Посмотри список "
        "через list_telegram_chats и назови чат его id или точным названием."
    )


def _sender_name(message) -> str:
    sender = getattr(message, "sender", None)
    if sender is None:
        return str(getattr(message, "post_author", "") or "")
    name = " ".join(x for x in (getattr(sender, "first_name", None),
                                getattr(sender, "last_name", None)) if x)
    if name:
        return name
    if getattr(sender, "title", None):
        return sender.title
    username = _public_username(sender)
    return f"@{username}" if username else str(getattr(sender, "id", "") or "")


_MEDIA_LABELS = (("photo", "фото"), ("video", "видео"), ("voice", "голосовое"),
                 ("audio", "аудио"), ("sticker", "стикер"), ("poll", "опрос"),
                 ("document", "файл"))


def _media_label(message) -> str | None:
    if not getattr(message, "media", None):
        return None
    for attr, label in _MEDIA_LABELS:
        if getattr(message, attr, None):
            return label
    return "вложение"


def read_chat(chat: str, limit: int = 50, since_days: int | None = None,
              query: str | None = None, chars: int = 1200) -> dict:
    """Сообщения одного чата — в том числе закрытого канала и закрытой группы.

    chat — id, @username или название; query — поиск по тексту внутри чата;
    since_days — не старше стольких дней. Сообщения возвращаются по возрастанию времени.
    """
    require_session()
    limit = max(1, min(int(limit or 50), 300))
    chars = max(100, min(int(chars or 1200), 4000))
    since = (datetime.now(timezone.utc) - timedelta(days=int(since_days))) if since_days else None

    async def go():
        async with _client() as client:
            entity, row = await _find_dialog(client, chat)
            messages = []
            kwargs = {"limit": limit}
            if query:
                kwargs["search"] = str(query)
            async for message in client.iter_messages(entity, **kwargs):
                if since and message.date and message.date < since:
                    break
                text = (message.text or "").strip()
                media = _media_label(message)
                if not text and not media:
                    continue
                messages.append({
                    "id": message.id,
                    "date": _msk_stamp(message.date),
                    "from": _sender_name(message),
                    "text": text[:chars],
                    "media": media,
                    "reply_to": getattr(message, "reply_to_msg_id", None),
                })
            messages.reverse()
            return {"chat": row, "messages": messages, "count": len(messages)}
    return _run(go())


def join_chat(invite: str) -> dict:
    """Вступить в закрытый канал/группу по ссылке-приглашению (t.me/+hash) или в публичный по @имени.

    Без вступления закрытый чат не читается вообще: у него нет ни публичной ссылки, ни превью.
    """
    require_session()
    raw = _normalize_query(invite)
    invite_hash = raw[1:] if raw.startswith("+") else None
    if raw.lower().startswith("joinchat/"):
        invite_hash = raw.split("/", 1)[1]

    async def go():
        from telethon.errors import UserAlreadyParticipantError
        from telethon.tl.functions.channels import JoinChannelRequest
        from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest

        async with _client() as client:
            if invite_hash:
                try:
                    updates = await client(ImportChatInviteRequest(invite_hash))
                    entity = (getattr(updates, "chats", None) or [None])[0]
                    joined = True
                except UserAlreadyParticipantError:
                    invite_info = await client(CheckChatInviteRequest(invite_hash))
                    entity = getattr(invite_info, "chat", None)
                    joined = False
            else:
                updates = await client(JoinChannelRequest(raw.lstrip("@")))
                entity = (getattr(updates, "chats", None) or [None])[0]
                joined = True
            if entity is None:
                raise RuntimeError("Telegram не вернул чат по этому приглашению — проверь ссылку.")
            return {"joined": joined, "already_member": not joined, "chat": _entity_row(entity)}
    return _run(go())


# --- writing (owner's decision 27.07.2026) ------------------------------------------------

def send_message(peer_id: int, text: str) -> int:
    """Написать человеку от имени аккаунта менеджера.

    Нужно там, где бот бессилен: Telegram отдаёт боту доступ только к тем собеседникам,
    которые сами написали в бизнес-аккаунт после подключения бота. Всем остальным бот
    получает PEER_ID_INVALID, а у аккаунта диалог есть, и он может написать.

    Раньше запись через эту сессию была намеренно не реализована. Владелец 27.07.2026
    прямо потребовал возможность писать всем, кто уже есть в базе, — это его решение.
    """
    async def go():
        async with _client() as client:
            sent = await client.send_message(int(peer_id), str(text or "")[:4096])
            return int(sent.id)
    return _run(go())


def edit_message(peer_id: int, message_id: int, text: str) -> None:
    """Изменить своё сообщение от имени аккаунта менеджера."""
    async def go():
        async with _client() as client:
            await client.edit_message(int(peer_id), int(message_id), str(text or "")[:4096])
    _run(go())


def delete_message(peer_id: int, message_id: int) -> None:
    """Удалить сообщение у обеих сторон от имени аккаунта менеджера."""
    async def go():
        async with _client() as client:
            await client.delete_messages(int(peer_id), [int(message_id)], revoke=True)
    _run(go())


# --- digest source -------------------------------------------------------------------------

def collect_posts(only_names: list[str] | None = None, since_days: int = 7,
                  per_chat_cap: int = 9000, post_chars: int = 1200,
                  include_groups: bool = False, max_chats: int = 60) -> tuple[list[dict], list[str]]:
    """Свежие посты каналов аккаунта (и групп, если include_groups) — закрытые тоже.

    only_names: id, @username или часть названия; пусто -> ВСЕ каналы аккаунта.
    Возвращает ([{id,title,username,closed,type,posts:[...]}], проблемы).
    """
    require_session()
    since = datetime.now(timezone.utc) - timedelta(days=since_days)
    wanted = [n.strip() for n in (only_names or []) if str(n).strip()]

    def _matches(row: dict) -> bool:
        return not wanted or any(_dialog_score(row, name) >= 0 for name in wanted)

    async def go():
        chats: list[dict] = []
        problems: list[str] = []
        async with _client() as client:
            picked = []
            async for dialog in client.iter_dialogs(limit=DIALOG_SCAN_LIMIT):
                row = _dialog_row(dialog)
                if row["type"] == "channel" or (include_groups and row["type"] == "group"):
                    if _matches(row):
                        picked.append((dialog.entity, row))
                if len(picked) >= max_chats:
                    break
            for entity, row in picked:
                try:
                    posts, used = [], 0
                    async for message in client.iter_messages(entity, limit=200):
                        if message.date < since:
                            break
                        text = (message.text or "").strip()
                        if not text:
                            continue
                        piece = {"date": _msk_stamp(message.date), "text": text[:post_chars]}
                        if used + len(piece["text"]) > per_chat_cap:
                            break
                        posts.append(piece)
                        used += len(piece["text"])
                    if posts:
                        posts.reverse()
                        chats.append({**row, "posts": posts})
                except Exception as exc:  # noqa: BLE001
                    problems.append(f"{row['name']} — {str(exc)[:100]}")
        return chats, problems

    return _run(go())


def chat_label(row: dict) -> str:
    if row.get("username"):
        return f"{row['name']} (t.me/{row['username']})"
    return f"{row['name']} (закрытый {'канал' if row.get('type') == 'channel' else 'чат'}, id {row['id']})"


def fetch_posts(since_days: int = 7, only_names: list[str] | None = None,
                per_chat_cap: int = 9000, include_groups: bool = False,
                max_chats: int = 60) -> tuple[list[tuple[str, list[str]]], list[str]]:
    """Плоский вид collect_posts для недельного обзора: ([(подпись чата, строки постов)], проблемы)."""
    chats, problems = collect_posts(only_names=only_names, since_days=since_days,
                                    per_chat_cap=per_chat_cap, include_groups=include_groups,
                                    max_chats=max_chats)
    sections = [(chat_label(chat), [f"[{p['date']}] {p['text']}" for p in chat["posts"]])
                for chat in chats]
    return sections, problems
