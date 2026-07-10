"""MTProto user session of the owner's manager account (@AlberyAIManager) — «глаза» агента.

The Bot API cannot see the channels/groups the account is subscribed to; a USER session can
see everything the account sees. This module keeps that session on the box and gives the
agent read-only access: list dialogs, pull fresh channel posts for the weekly digest.

Security: the session file (.tg_userbot.session, chmod 600, gitignored) is равносильно
полному доступу к аккаунту — it never leaves the server and is never committed. Login is a
two-step interactive flow (scripts/tg_userbot_login.py) because Telegram sends the code to
the owner's app. Writes/replies through this session are NOT implemented on purpose (phase 2,
отдельное решение владельца).

telethon is imported lazily so the rest of the service (and the test suite) works without it.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent
SESSION_BASE = APP_ROOT / ".tg_userbot"           # telethon appends .session
SESSION_FILE = APP_ROOT / ".tg_userbot.session"


def session_ready() -> bool:
    return SESSION_FILE.is_file()


def _client():
    from telethon import TelegramClient  # lazy: optional dependency
    api_id = int(os.getenv("TG_API_ID", "0") or 0)
    api_hash = os.getenv("TG_API_HASH", "").strip()
    if not api_id or not api_hash:
        raise RuntimeError("TG_API_ID/TG_API_HASH не настроены в .env")
    return TelegramClient(str(SESSION_BASE), api_id, api_hash)


def _secure_session() -> None:
    try:
        if SESSION_FILE.is_file():
            os.chmod(SESSION_FILE, 0o600)
    except OSError:
        pass


def _run(coro):
    result = asyncio.run(coro)
    _secure_session()
    return result


def whoami() -> dict:
    async def go():
        async with _client() as client:
            me = await client.get_me()
            return {"id": me.id, "username": me.username,
                    "name": " ".join(x for x in (me.first_name, me.last_name) if x)}
    return _run(go())


def list_dialogs(limit: int = 500) -> list[dict]:
    """Everything the account sees: channels, groups, private chats (id/name/type/unread)."""
    async def go():
        out = []
        async with _client() as client:
            async for d in client.iter_dialogs(limit=limit):
                kind = "channel" if (d.is_channel and not d.is_group) else \
                       "group" if d.is_group else "private"
                out.append({"id": d.id, "name": d.name or "", "type": kind,
                            "unread": d.unread_count,
                            "username": getattr(d.entity, "username", None)})
        return out
    return _run(go())


def fetch_posts(since_days: int = 7, only_names: list[str] | None = None,
                per_chat_cap: int = 9000, include_groups: bool = False,
                max_chats: int = 60) -> tuple[list[tuple[str, list[str]]], list[str]]:
    """Fresh posts from the account's channels (and optionally groups).
    only_names: limit to channels whose @username or title matches (the watchlist);
    empty/None -> ALL subscribed channels. Returns ([(chat_label, posts)], problems)."""
    wanted = {n.strip().lstrip("@").lower() for n in (only_names or []) if n.strip()}
    since = datetime.now(timezone.utc) - timedelta(days=since_days)

    def _matches(d) -> bool:
        if not wanted:
            return True
        uname = (d.get("username") or "").lower()
        title = (d.get("name") or "").lower()
        return any(w == uname or w in title for w in wanted)

    async def go():
        sections: list[tuple[str, list[str]]] = []
        problems: list[str] = []
        async with _client() as client:
            dialogs = []
            async for d in client.iter_dialogs(limit=500):
                is_channel = d.is_channel and not d.is_group
                if is_channel or (include_groups and d.is_group):
                    uname = getattr(d.entity, "username", None)
                    dialogs.append({"entity": d.entity, "name": d.name or "?",
                                    "username": uname})
            picked = [d for d in dialogs if _matches(d)][:max_chats]
            for d in picked:
                label = f"{d['name']}" + (f" (t.me/{d['username']})" if d["username"] else "")
                try:
                    posts, used = [], 0
                    async for m in client.iter_messages(d["entity"], limit=200):
                        if m.date < since:
                            break
                        text = (m.text or "").strip()
                        if not text:
                            continue
                        piece = f"[{m.date.strftime('%d.%m %H:%M')}] {text[:1200]}"
                        if used + len(piece) > per_chat_cap:
                            break
                        posts.append(piece)
                        used += len(piece)
                    if posts:
                        sections.append((label, list(reversed(posts))))
                except Exception as exc:  # noqa: BLE001
                    problems.append(f"{label} — {str(exc)[:100]}")
        return sections, problems

    return _run(go())
