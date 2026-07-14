"""Albery AI agent in Telegram — a standalone lightweight service (albery-tg.service).

Deliberately ISOLATED from the main albery.service: its own process, long polling, no Flask
import — the production web app is never touched or restarted by this feature. LLM turns reuse
the proven b24bot pattern (`hermes -z … -t albery,web --yolo` subprocess), one at a time.

What it does (phase 1, owner-approved 2026-07-09):
  * private chat with the OWNER (whitelist TG_AGENT_OWNER_IDS): questions go to the brain with
    the full albery MCP connector + web; strangers get a polite refusal and никакого LLM;
  * channel watchlist (/add_channel /del_channel /channels) + weekly digest of the public
    channels' t.me/s/ previews (tg_digest.py, albery-tg-digest.timer) — WB news, org practices,
    «что внедрить/обновить у нас»; /digest runs it on demand;
  * Telegram Business bridge (owner connects the bot to his Premium account in
    Settings → Telegram Business → Chatbots): business_connection is stored, incoming
    business messages are LOGGED to .tg_business_log.jsonl — читаем, но НЕ отвечаем от имени
    владельца, пока TG_BUSINESS_AUTOREPLY!=1 (phase 2, отдельное включение).

Secrets: TG_AGENT_BOT_TOKEN lives only in /var/www/albery/.env (never in git).
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(),
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tg_agent")

APP_ROOT = Path(__file__).resolve().parent
STATE_PATH = APP_ROOT / ".tg_agent_state.json"
BUSINESS_LOG_PATH = APP_ROOT / ".tg_business_log.jsonl"
_state_lock = threading.Lock()
_hermes_lock = threading.Lock()


def _load_env_file() -> None:
    """The service normally gets env via systemd EnvironmentFile; this is the manual-run fallback."""
    env_path = APP_ROOT / ".env"
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    except OSError:
        pass


def bot_token() -> str:
    return os.getenv("TG_AGENT_BOT_TOKEN", "").strip()


def owner_ids() -> set[int]:
    raw = os.getenv("TG_AGENT_OWNER_IDS", "")
    out = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            out.add(int(part))
    return out


def owner_usernames() -> set[str]:
    """Whitelist by @username — the Bot API cannot resolve a username to an id up front, but
    every incoming update carries from.username, so this is how the owner account is granted
    access before it ever wrote to the bot. Owner rule 2026-07-09: ONLY @AlberyAIManager."""
    raw = os.getenv("TG_AGENT_OWNER_USERNAMES", "AlberyAIManager")
    return {u.strip().lstrip("@").lower() for u in raw.replace(";", ",").split(",") if u.strip()}


def is_owner(user) -> bool:
    """`user` is the update's `from` dict (id + username) or a bare id."""
    if isinstance(user, dict):
        if to_int_safe(user.get("id")) in owner_ids():
            return True
        return str(user.get("username") or "").lower() in owner_usernames()
    try:
        return int(user) in owner_ids()
    except (TypeError, ValueError):
        return False


def to_int_safe(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _remember_owner_chat(user: dict) -> None:
    """Persist the owner's numeric id once they write — digests and notifications need a chat id,
    and a username alone cannot receive messages."""
    uid = to_int_safe(user.get("id"))
    if not uid:
        return
    with _state_lock:
        state = load_state()
        seen = set(state.get("owner_chat_ids") or [])
        if uid not in seen:
            seen.add(uid)
            state["owner_chat_ids"] = sorted(seen)
            save_state(state)


def delivery_targets() -> list[int]:
    """Chats that receive digests/notifications: explicit env ids + owners seen via username."""
    return sorted(owner_ids() | set(load_state().get("owner_chat_ids") or []))


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, STATE_PATH)
    try:
        os.chmod(STATE_PATH, 0o600)
    except OSError:
        pass


def api(method: str, http_timeout: int = 35, **params):
    resp = requests.post(f"https://api.telegram.org/bot{bot_token()}/{method}",
                         json=params, timeout=http_timeout)
    data = resp.json() if resp.content else {}
    if not (isinstance(data, dict) and data.get("ok")):
        raise RuntimeError(f"{method}: {str(data)[:300]}")
    return data.get("result")


_MARKUP_RE = re.compile(r"\[/?(?:b|i|u|s|url(?:=[^\]]*)?)\]|</?(?:b|i|u|s|strong|em)>", re.IGNORECASE)


def _strip_markup(text: str) -> str:
    """The model mixes Bitrix BB-codes ([b]…[/b]) and HTML (<b>…</b>) into its answers; this bot
    sends PLAIN text (no parse_mode), so those tags reached people literally («какие-то символы
    <b>» — владелец, 2026-07-14). Strip them; bold emphasis is lost, garbage is worse."""
    text = _MARKUP_RE.sub("", text or "")
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text, flags=re.S)
    return text


def send_text(chat_id, text: str) -> None:
    """Plain-text send with chunking (TG hard limit 4096)."""
    text = _strip_markup((text or "").strip()) or "(пустой ответ)"
    for i in range(0, len(text), 4000):
        api("sendMessage", chat_id=chat_id, text=text[i:i + 4000],
            disable_web_page_preview=True)


# --- channel watchlist ---------------------------------------------------------------------

_CHANNEL_RE = re.compile(r"^[A-Za-z0-9_]{4,64}$")


def normalize_channel(raw: str) -> str | None:
    """'@name' / 'https://t.me/name' / 't.me/s/name?x=1' / 'name' -> 'name' (None if invalid)."""
    s = (raw or "").strip().rstrip("/").split("?", 1)[0]
    s = re.sub(r"^https?://", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^(t\.me|telegram\.me)/(s/)?", "", s, flags=re.IGNORECASE)
    s = s.lstrip("@").strip()
    # joinchat/+invite links are private chats — the public-preview digest cannot read those
    if s.startswith("+") or s.lower().startswith("joinchat"):
        return None
    return s if _CHANNEL_RE.match(s) else None


def channels() -> list[str]:
    return list(load_state().get("channels") or [])


def set_channels(names: list[str]) -> None:
    with _state_lock:
        state = load_state()
        state["channels"] = sorted(set(names))
        save_state(state)


# --- LLM turn (the b24bot-proven hermes CLI pattern, one at a time) ------------------------

_HERMES_ERROR_RE = re.compile(
    r"^(API call failed|Ошибка LLM|Error:|Traceback \(most recent call last\))", re.IGNORECASE)


def hermes_answer(prompt: str, session_prefix: str, toolsets: str | None = None,
                  timeout_s: int | None = None) -> str:
    toolsets = toolsets or os.getenv("TG_AGENT_TOOLSETS", "albery,web")
    timeout_s = timeout_s or int(os.getenv("TG_AGENT_HERMES_TIMEOUT", "420"))
    # Fresh session per run (hermes >=0.17 resumes --continue sessions; memory is prompt-injected)
    run_session = f"{session_prefix}-r{uuid.uuid4().hex[:8]}"
    cmd = ["hermes", "-z", prompt, "--continue", run_session, "-t", toolsets, "--yolo"]
    with _hermes_lock:  # a 2GB box: never run two brain turns from this service at once
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    answer = (proc.stdout or "").strip()
    if proc.returncode != 0 or not answer or _HERMES_ERROR_RE.match(answer):
        raise RuntimeError(f"hermes turn failed rc={proc.returncode}: "
                           f"{(answer or proc.stderr or '')[:300]}")
    return answer


def _history(chat_id) -> list[list[str]]:
    return list((load_state().get("history") or {}).get(str(chat_id)) or [])


def _remember(chat_id, question: str, answer: str) -> None:
    with _state_lock:
        state = load_state()
        hist = state.setdefault("history", {}).setdefault(str(chat_id), [])
        hist.append([question[:500], answer[:1500]])
        del hist[:-6]  # keep the last 6 exchanges
        save_state(state)


def owner_turn(chat_id, user_text: str) -> str:
    parts = [
        "Ты — ИИ-агент Албери в Telegram (личный ассистент владельца). Отвечай по-русски, "
        "кратко и по делу, обычным текстом без markdown-разметки. У тебя есть инструменты "
        "компании (Bitrix, знания, Google) и веб-поиск — используй их, когда нужно.",
    ]
    hist = _history(chat_id)
    if hist:
        convo = "\n".join(f"Владелец: {q}\nАссистент: {a}" for q, a in hist)
        parts.append("История диалога (помни её):\n" + convo)
    parts.append("Сообщение владельца:\n" + user_text)
    answer = hermes_answer("\n\n".join(parts), f"tg-{chat_id}")
    _remember(chat_id, user_text, answer)
    return answer


# --- update handling ------------------------------------------------------------------------

HELP_TEXT = (
    "Я — ИИ-агент Албери в Telegram.\n\n"
    "Команды:\n"
    "/channels — список каналов еженедельного обзора\n"
    "/add_channel <@канал или ссылка, можно несколько> — следить только за этими\n"
    "/del_channel <канал> — убрать из списка\n"
    "/chats — что видит подключённая сессия аккаунта (каналы/группы/чаты)\n"
    "/digest — собрать обзор прямо сейчас\n"
    "/new — начать новую сессию (забыть историю)\n\n"
    "Любое другое сообщение — вопрос к агенту (инструменты компании + веб).\n\n"
    "Обзор каналов: если подключена сессия менеджер-аккаунта, я читаю ВСЕ каналы, на которые "
    "подписан аккаунт (список /add_channel тогда работает как фильтр; пустой список = все). "
    "Без сессии — только публичные каналы из списка."
)


def handle_command(chat_id, text: str) -> bool:
    """True when the message was a command and is fully handled."""
    cmd, _, args = text.strip().partition(" ")
    cmd = cmd.lower().split("@", 1)[0]
    if cmd in ("/start", "/help"):
        send_text(chat_id, HELP_TEXT)
    elif cmd == "/channels":
        names = channels()
        send_text(chat_id, ("Каналы обзора:\n" + "\n".join(f"• t.me/{n}" for n in names))
                  if names else "Список пуст. Добавьте: /add_channel @канал (можно несколько).")
    elif cmd == "/add_channel":
        good, bad = [], []
        for raw in re.split(r"[\s,;]+", args.strip()):
            if not raw:
                continue
            name = normalize_channel(raw)
            (good.append(name) if name else bad.append(raw))
        if good:
            set_channels(channels() + good)
        reply = []
        if good:
            reply.append("Добавил: " + ", ".join(good))
        if bad:
            reply.append("Не понял (нужен публичный @канал или ссылка t.me): " + ", ".join(bad[:5]))
        send_text(chat_id, "\n".join(reply) or "Укажите канал: /add_channel @канал")
    elif cmd == "/del_channel":
        name = normalize_channel(args)
        if name and name in channels():
            set_channels([c for c in channels() if c != name])
            send_text(chat_id, f"Убрал t.me/{name}.")
        else:
            send_text(chat_id, "Такого канала нет в списке (/channels).")
    elif cmd == "/chats":
        try:
            import tg_userbot
            if not tg_userbot.session_ready():
                send_text(chat_id, "Сессия менеджер-аккаунта ещё не подключена — попросите "
                                   "разработчика выполнить подключение (нужен код из Telegram).")
            else:
                dialogs = tg_userbot.list_dialogs()
                kinds = {"channel": [], "group": [], "private": []}
                for d in dialogs:
                    kinds.get(d["type"], kinds["private"]).append(d)
                lines = [f"Сессия видит {len(dialogs)} диалогов: "
                         f"{len(kinds['channel'])} каналов, {len(kinds['group'])} групп, "
                         f"{len(kinds['private'])} личных чатов.", "", "Каналы:"]
                lines += [f"• {d['name']}" + (f" (t.me/{d['username']})" if d.get("username") else "")
                          for d in kinds["channel"][:60]]
                send_text(chat_id, "\n".join(lines))
        except Exception as exc:  # noqa: BLE001
            log.exception("chats command failed")
            send_text(chat_id, f"Не смог прочитать список чатов: {str(exc)[:150]}")
    elif cmd == "/digest":
        send_text(chat_id, "Собираю обзор каналов — пришлю сюда (обычно 2–5 минут)…")

        def _run():
            try:
                import tg_digest
                tg_digest.run_digest(notify_chat=chat_id)
            except Exception as exc:  # noqa: BLE001
                log.exception("manual digest failed")
                try:
                    send_text(chat_id, f"Обзор не получился: {str(exc)[:200]}")
                except Exception:  # noqa: BLE001
                    pass

        threading.Thread(target=_run, daemon=True).start()
    elif cmd == "/new":
        with _state_lock:
            state = load_state()
            (state.get("history") or {}).pop(str(chat_id), None)
            save_state(state)
        send_text(chat_id, "Начали заново — историю забыл.")
    else:
        return False
    return True


def handle_message(msg: dict) -> None:
    chat = msg.get("chat") or {}
    if chat.get("type") != "private":
        return  # phase 1: the bot works in private chats only
    chat_id = chat.get("id")
    sender = msg.get("from") or {}
    text = (msg.get("text") or "").strip()
    if not text:
        return
    if not is_owner(sender):
        send_text(chat_id, "Я — внутренний агент компании Albery и работаю только с владельцем. "
                           "Если вам нужен доступ — напишите Евгению.")
        return
    _remember_owner_chat(sender)
    if handle_command(chat_id, text):
        return
    try:
        api("sendChatAction", chat_id=chat_id, action="typing")
    except Exception:  # noqa: BLE001
        pass
    try:
        send_text(chat_id, owner_turn(chat_id, text))
    except Exception as exc:  # noqa: BLE001
        log.exception("owner turn failed")
        send_text(chat_id, f"Не получилось ответить (мозг сбоит): {str(exc)[:150]}. "
                           "Попробуйте ещё раз через минуту.")


def handle_business_connection(conn: dict) -> None:
    """Owner connected/disconnected the bot to his personal account (Telegram Business)."""
    with _state_lock:
        state = load_state()
        state.setdefault("business", {})[str(conn.get("id"))] = {
            "user_id": (conn.get("user") or {}).get("id"),
            "enabled": bool(conn.get("is_enabled", True)),
            "can_reply": bool((conn.get("rights") or {}).get("can_reply")
                              if isinstance(conn.get("rights"), dict) else conn.get("can_reply")),
            "at": datetime.now(timezone.utc).isoformat(),
        }
        save_state(state)
    for oid in delivery_targets():
        try:
            state_word = "подключён к вашему аккаунту" if conn.get("is_enabled", True) else "отключён"
            send_text(oid, f"🔗 Бизнес-режим: бот {state_word}. Я вижу личные чаты и веду журнал; "
                           "автоответы от вашего имени пока выключены (включим отдельно).")
        except Exception:  # noqa: BLE001
            pass


def handle_business_message(msg: dict) -> None:
    """Log an incoming message from the owner's personal chats (suppliers). Read-only in phase 1."""
    record = {
        "at": datetime.now(timezone.utc).isoformat(),
        "connection_id": msg.get("business_connection_id"),
        "chat_id": (msg.get("chat") or {}).get("id"),
        "chat_name": " ".join(x for x in ((msg.get("chat") or {}).get("first_name"),
                                          (msg.get("chat") or {}).get("last_name"),
                                          (msg.get("chat") or {}).get("title")) if x),
        "from_id": (msg.get("from") or {}).get("id"),
        "text": (msg.get("text") or msg.get("caption") or "")[:800],
    }
    try:
        with BUSINESS_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        log.exception("business log write failed")


def poll_forever() -> None:
    log.info("tg agent starting; owner ids=%s usernames=%s",
             sorted(owner_ids()), sorted(owner_usernames()))
    me = api("getMe")
    log.info("bot: @%s (id %s)", me.get("username"), me.get("id"))
    offset = int(load_state().get("offset") or 0)
    while True:
        try:
            updates = api("getUpdates", http_timeout=65, timeout=55, offset=offset,
                          allowed_updates=["message", "business_connection", "business_message"])
        except Exception as exc:  # noqa: BLE001
            log.warning("getUpdates failed: %s", str(exc)[:200])
            time.sleep(5)
            continue
        for upd in updates or []:
            offset = max(offset, int(upd.get("update_id", 0)) + 1)
            try:
                if upd.get("message"):
                    handle_message(upd["message"])
                elif upd.get("business_connection"):
                    handle_business_connection(upd["business_connection"])
                elif upd.get("business_message"):
                    handle_business_message(upd["business_message"])
            except Exception:  # noqa: BLE001
                log.exception("update handling failed")
        with _state_lock:
            state = load_state()
            state["offset"] = offset
            save_state(state)


if __name__ == "__main__":
    _load_env_file()
    if not bot_token():
        raise SystemExit("TG_AGENT_BOT_TOKEN is not configured")
    poll_forever()
