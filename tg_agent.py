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


# АГЕНТ = БОТ. @AlberyAIManager — не бот, а обычный аккаунт Telegram: он лишь подключил бота
# @Albery_AI2_Bot в «Telegram для бизнеса», и все ответы лидам физически шлёт бот, просто
# Telegram показывает их от лица аккаунта. Поэтому агент здесь один — бот, а два его источника
# диалогов различаются полем kind: bot_dm (пишут самому боту) и lead_chat (переписки аккаунта).
BOT_CHANNEL = "albery-ai-bot"
# Оставлено как псевдоним: бизнес-переписки ведёт тот же бот, отдельным агентом они не являются.
MANAGER_CHANNEL = BOT_CHANNEL


def owner_usernames() -> set[str]:
    """Кому разрешено писать агенту в личку бота.

    Список живёт в БД (telegram_bot_access) и правится в кабинете; .env остаётся запасным
    источником на случай, когда база недоступна — иначе сбой БД молча закрыл бы агента для всех."""
    from_db = access_usernames(BOT_CHANNEL)
    if from_db:
        return from_db
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


# --- журнал переписок и доступ (PostgreSQL) -----------------------------------------------------
# Битрикс-диалоги живут в bitrix_bot_messages с 052 и на них построен кабинет; Telegram писался
# только в файл рядом со службой, поэтому вкладка Telegram была заглушкой. Пишем сюда же — в БД.
# Импортировать app/b24bot в этот процесс нельзя (их импорт стартует живые планировщики), а
# shared.db — чистый слой без Flask, поэтому берём соединение оттуда.
_ACCESS_CACHE: dict[str, Any] = {"at": 0.0, "by_bot": {}}
_ACCESS_TTL_S = float(os.getenv("TG_ACCESS_TTL_S", "60") or 60)


def _db():
    from shared.db import connect
    return connect()


def journal(bot: str, dialog_id, direction: str, text: str, *, kind: str = "bot_dm",
            user: dict | None = None, tg_message_id=None, status: str = "ok",
            meta: dict | None = None) -> None:
    """Записать сообщение в журнал переписок. Никогда не мешает работе агента.

    Логируем только те чаты, где участвовал агент (решение владельца 22.07.2026): бизнес-режим
    видит и личные переписки аккаунта с поставщиками и знакомыми, им не место в кабинете."""
    try:
        user = user or {}
        uname = str(user.get("username") or "").lstrip("@").lower() or None
        name = " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x).strip()
        with _db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO telegram_bot_messages (bot, dialog_id, tg_user_id, username,"
                    " display_name, direction, kind, text, tg_message_id, status, meta)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (bot, str(dialog_id), to_int_safe(user.get("id")), uname, name or None,
                     direction, kind, (text or "")[:20000], to_int_safe(tg_message_id), status,
                     json.dumps(meta, ensure_ascii=False) if meta else None),
                )
    except Exception:  # noqa: BLE001
        log.warning("журнал Telegram недоступен", exc_info=True)


def access_usernames(bot: str) -> set[str]:
    """Кому разрешено писать этому агенту. Пустое множество = список не задан/БД недоступна."""
    now = time.time()
    cached = (_ACCESS_CACHE["by_bot"] or {}).get(bot)
    if cached is not None and now - float(_ACCESS_CACHE["at"]) < _ACCESS_TTL_S:
        return set(cached)
    try:
        with _db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT username FROM telegram_bot_access"
                            " WHERE bot = %s AND is_active", (bot,))
                names = {str(r["username"]).lstrip("@").lower() for r in cur.fetchall()}
    except Exception:  # noqa: BLE001
        log.warning("список доступа Telegram недоступен", exc_info=True)
        return set(cached or ())
    _ACCESS_CACHE["by_bot"][bot] = names
    _ACCESS_CACHE["at"] = now
    return set(names)


def remember_access_user_id(bot: str, user: dict) -> None:
    """Дописать числовой id к записи доступа: по @username Telegram искать людей не умеет,
    id становится известен только когда человек написал сам."""
    uname = str((user or {}).get("username") or "").lstrip("@").lower()
    uid = to_int_safe((user or {}).get("id"))
    if not uname or not uid:
        return
    try:
        with _db() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE telegram_bot_access SET tg_user_id = %s"
                            " WHERE bot = %s AND username = %s AND tg_user_id IS DISTINCT FROM %s",
                            (uid, bot, uname, uid))
    except Exception:  # noqa: BLE001
        log.warning("не удалось запомнить id для доступа", exc_info=True)


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


def react(chat_id, message_id, emoji: str, business_connection_id: str = "") -> None:
    """Поставить реакцию на сообщение собеседника — как агент в Битриксе.

    Там это 👀 «прочитал, думаю» → 👍 «ответил», и человек видит, что его не игнорируют.
    Реакция косметическая: любая ошибка гасится, ответ клиенту важнее."""
    if not message_id:
        return
    params = {"chat_id": chat_id, "message_id": int(message_id),
              "reaction": [{"type": "emoji", "emoji": emoji}] if emoji else []}
    if business_connection_id:
        params["business_connection_id"] = business_connection_id
    try:
        api("setMessageReaction", **params)
    except Exception as exc:  # noqa: BLE001
        log.debug("реакция %s не поставлена: %s", emoji, str(exc)[:120])


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


def channel_toolsets(channel: str) -> str | None:
    """Личный коннектор канала agent-<slug>, если он настроен в кабинете.

    Через него применяются набор MCP-инструментов, инструкции и знания, выбранные владельцем
    для этого агента, — то же самое, что у субагентов Битрикса. Пока агента нет (или он
    выключен), работает прежний общий набор из .env, чтобы бот не остался без инструментов."""
    try:
        with _db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM agents WHERE slug = %s AND is_active", (channel,))
                if not cur.fetchone():
                    return None
    except Exception:  # noqa: BLE001
        return None
    extra = os.getenv("TG_AGENT_EXTRA_TOOLSETS", "web").strip().strip(",")
    return f"agent-{channel},{extra}" if extra else f"agent-{channel}"


def channel_role_prompt(channel: str) -> str:
    """Роль агента из его карточки в кабинете.

    Промпт живёт в карточке, а не в коде: владелец правит поведение агента сам, без деплоя.
    Пусто или база недоступна — работает встроенный текст ниже, чтобы агент не остался немым."""
    try:
        with _db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT role_prompt FROM agents WHERE slug = %s AND is_active",
                            (channel,))
                row = cur.fetchone()
                return (row["role_prompt"] or "").strip() if row else ""
    except Exception:  # noqa: BLE001
        return ""


_INSTR_CACHE: dict[str, object] = {"at": 0.0, "text": ""}
_INSTR_CAP = int(os.getenv("TG_AGENT_INSTR_CAP", "12000") or "12000")
_INSTR_DOC_CAP = int(os.getenv("TG_AGENT_INSTR_DOC_CAP", "6000") or "6000")


def channel_instructions(channel: str) -> str:
    """Инструкции, подключённые владельцем ИМЕННО этому агенту (его манифест в кабинете).

    Доставляем их прямо в промпт, а не надеемся, что модель сама позовёт start_here: у неё
    один ход на ответ клиенту, и «забыла спросить» означает неоформленное сообщение.
    Универсальные инструкции сюда НЕ идут намеренно: они написаны под отчёты в Битриксе и
    несут BB-коды, а в Telegram те доходят до клиента мусором (жалоба владельца 14.07.2026)."""
    now = time.time()
    if now - float(_INSTR_CACHE["at"] or 0) < 120 and _INSTR_CACHE["text"]:
        return _INSTR_CACHE["text"]
    try:
        from agent_knowledge import load_instructions, load_manifest
        connected = set(load_manifest(channel)["instructions"])
        if not connected:
            return ""
        items = [i for i in (load_instructions() or []) if i["path"] in connected]
        # Правила оформления идут ПЕРВЫМИ и целиком: к агенту подключены и объёмные
        # инструкции по работе в системе (десятки килобайт), и без явного порядка они
        # съедали бы лимит, а оформление обрезалось бы на середине.
        items.sort(key=lambda i: (not i["path"].startswith("Формат ответа"), i["path"]))
        picked = [f"# {i['name']}\n{i['content'].strip()}"[:_INSTR_DOC_CAP] for i in items]
    except Exception:  # noqa: BLE001 — без оформления агент ответит хуже, но ответит
        log.warning("инструкции агента %s не загрузились", channel, exc_info=True)
        return _INSTR_CACHE["text"] or ""
    text = "\n\n".join(picked)[:_INSTR_CAP]
    _INSTR_CACHE.update({"at": now, "text": text})
    return text


def _with_instructions(prompt: str, channel: str) -> str:
    instr = channel_instructions(channel)
    if not instr:
        return prompt
    return (f"{prompt}\n\n"
            f"ОБЯЗАТЕЛЬНЫЕ ПРАВИЛА ОФОРМЛЕНИЯ — подключены владельцем, следуй им буквально:\n"
            f"{instr}")


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
    # Инструменты, инструкции и знания — из карточки этого агента в кабинете.
    answer = hermes_answer("\n\n".join(parts), f"tg-{chat_id}",
                           toolsets=channel_toolsets(BOT_CHANNEL))
    _remember(chat_id, user_text, answer)
    return answer


# --- update handling ------------------------------------------------------------------------

HELP_TEXT = (
    "Я — ИИ-агент Албери в Telegram.\n\n"
    "Команды:\n"
    "/channels — список каналов еженедельного обзора\n"
    "/add_channel <@канал или ссылка, можно несколько> — следить только за этими\n"
    "/del_channel <канал> — убрать из списка\n"
    "/id — добавить человека в справочник (кнопка выбора контакта → его числовой id)\n"
    "/contacts — известные контакты и их id\n"
    "/write @username текст — написать человеку ОТ ЛИЦА вашего аккаунта\n"
    "/chats — что видит подключённая сессия аккаунта (каналы/группы/чаты)\n"
    "/digest — собрать обзор прямо сейчас\n"
    "/new — начать новую сессию (забыть историю)\n\n"
    "Любое другое сообщение — вопрос к агенту (инструменты компании + веб).\n\n"
    "Обзор каналов: если подключена сессия менеджер-аккаунта, я читаю ВСЕ каналы, на которые "
    "подписан аккаунт (список /add_channel тогда работает как фильтр; пустой список = все). "
    "Без сессии — только публичные каналы из списка."
)


# --- справочник контактов: username -> числовой id --------------------------------------------
# Bot API НЕ умеет находить человека по @username: sendMessage принимает только числовой id,
# а getChat на чужой username отвечает «chat not found» — и это не лечится правами.
# Штатный способ получить id — кнопка выбора контакта (KeyboardButtonRequestUsers): владелец
# тыкает человека в своём списке, Telegram сам возвращает его user_id. Дальше писать этому
# человеку от лица аккаунта можно когда угодно (проверено 21.07.2026: доставка вне окна 24 ч).


def contacts() -> dict:
    return (load_state().get("contacts") or {}) if True else {}


def remember_contact(user: dict) -> dict:
    """Сохранить человека в справочник. Ключ — username в нижнем регистре, плюс id."""
    uid = user.get("user_id") or user.get("id")
    if not uid:
        return {}
    entry = {
        "id": int(uid),
        "username": (user.get("username") or "").lstrip("@"),
        "name": " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x).strip(),
        "at": datetime.now(timezone.utc).isoformat(),
    }
    with _state_lock:
        state = load_state()
        book = state.setdefault("contacts", {})
        if entry["username"]:
            book[entry["username"].lower()] = entry
        book[str(entry["id"])] = entry
        save_state(state)
    return entry


def find_contact(who: str) -> dict | None:
    """Найти в справочнике по @username или по числовому id."""
    key = (who or "").strip().lstrip("@").lower()
    if not key:
        return None
    return contacts().get(key)


def send_as_account(user_id: int, text: str, parse_mode: str = "") -> tuple[bool, str]:
    """Написать человеку ОТ ЛИЦА аккаунта владельца (Telegram Business), а не от бота."""
    state = load_state()
    conn_ids = list((state.get("business") or {}).keys())
    if not conn_ids:
        return False, "бизнес-подключение не настроено: подключите бота в Telegram → Настройки → Telegram для бизнеса → Чат-боты"
    extra = {"parse_mode": parse_mode} if parse_mode else {}
    try:
        api("sendMessage", business_connection_id=conn_ids[0], chat_id=int(user_id), text=text,
            link_preview_options={"is_disabled": True}, **extra)
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:200]


def _request_contact_keyboard() -> dict:
    """Кнопка «выбрать человека»: Telegram вернёт его числовой id в users_shared."""
    return {
        "keyboard": [[{
            "text": "👤 Выбрать человека",
            "request_users": {"request_id": 1, "user_is_bot": False, "max_quantity": 1,
                              "request_username": True, "request_name": True},
        }]],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }


# --- рабочие функции для агента (вызываются MCP-инструментами) --------------------------------

def telegram_send_as_account(who: str, text: str) -> dict:
    """Написать человеку от лица аккаунта владельца. who = @username или числовой id.

    Telegram не даёт боту искать людей по @username, поэтому пишем только тем, чей числовой id
    уже известен: он попадает в справочник сам, как только человек написал на аккаунт."""
    text = (text or "").strip()
    if not text:
        raise ValueError("Пустой текст сообщения.")
    key = (who or "").strip()
    if not key:
        raise ValueError("Не указан получатель.")
    entry = find_contact(key)
    target = entry["id"] if entry else (int(key) if key.lstrip("-").isdigit() else None)
    if target is None:
        raise ValueError(
            f"«{key}» нет в справочнике, а Telegram не позволяет боту найти человека по "
            "@username — нужен его числовой id. Он появится сам, как только человек напишет "
            "на аккаунт (например, по ссылке t.me/AlberyAIManager). Список известных — "
            "list_telegram_contacts.")
    ok, err = send_as_account(target, text)
    if not ok:
        raise RuntimeError(f"Telegram отказал: {err}")
    return {"sent": True, "to_id": target,
            "to": ("@" + entry["username"]) if (entry and entry.get("username")) else str(target),
            "from": "аккаунт владельца (Telegram Business)", "chars": len(text)}


def telegram_contacts_list() -> dict:
    """Кому агент может писать прямо сейчас (справочник числовых id)."""
    uniq = {v["id"]: v for v in contacts().values() if isinstance(v, dict) and v.get("id")}
    people = sorted(uniq.values(), key=lambda x: (x.get("username") or x.get("name") or ""))
    return {
        "contacts": people,
        "total": len(people),
        "note": ("Писать можно только этим людям: Telegram не даёт боту искать по @username. "
                 "Новые контакты попадают сюда автоматически, когда человек пишет на аккаунт."),
    }


def handle_forward(chat_id, msg: dict) -> bool:
    """Достать числовой id автора ПЕРЕСЛАННОГО сообщения. True — сообщение обработано.

    Так работают публичные «боты для получения id»: волшебного поиска по @username в Bot API
    нет, зато у пересланного сообщения есть автор. Если человек закрыл пересылку в настройках
    приватности, Telegram отдаёт только имя без id — тогда честно говорим об этом."""
    origin = msg.get("forward_origin") or {}
    user = origin.get("sender_user") or msg.get("forward_from") or {}
    if user.get("id"):
        entry = remember_contact({"user_id": user["id"], "username": user.get("username"),
                                  "first_name": user.get("first_name"),
                                  "last_name": user.get("last_name")})
        who = ("@" + entry["username"]) if entry["username"] else (entry["name"] or "контакт")
        send_text(chat_id, f"Записал: {who} — id {entry['id']}.\n\n"
                           f"Написать ему от лица вашего аккаунта:\n"
                           f"/write {('@' + entry['username']) if entry['username'] else entry['id']} текст")
        return True
    hidden = origin.get("sender_user_name") or msg.get("forward_sender_name")
    if hidden:
        send_text(chat_id, f"«{hidden}» закрыл пересылку в настройках приватности — "
                           "Telegram не отдаёт его id при пересылке.\n"
                           "Добавьте его кнопкой: /id — там выбор из ваших контактов.")
        return True
    return False


def handle_users_shared(chat_id, shared: dict) -> None:
    """Владелец выбрал человека кнопкой — Telegram прислал его настоящий числовой id."""
    people = shared.get("users") or shared.get("user_ids") or []
    saved = []
    for u in people:
        entry = remember_contact(u if isinstance(u, dict) else {"user_id": u})
        if entry:
            saved.append(entry)
    if not saved:
        send_text(chat_id, "Не удалось разобрать выбранного человека, попробуйте ещё раз.")
        return
    lines = ["Записал в справочник:"]
    for e in saved:
        who = ("@" + e["username"]) if e["username"] else (e["name"] or "без имени")
        lines.append(f"• {who} — id {e['id']}")
    lines.append("")
    lines.append("Теперь можно писать от лица вашего аккаунта:")
    lines.append(f"/write {('@' + saved[0]['username']) if saved[0]['username'] else saved[0]['id']} текст сообщения")
    send_text(chat_id, "\n".join(lines))


def handle_command(chat_id, text: str) -> bool:
    """True when the message was a command and is fully handled."""
    cmd, _, args = text.strip().partition(" ")
    cmd = cmd.lower().split("@", 1)[0]
    if cmd in ("/start", "/help"):
        send_text(chat_id, HELP_TEXT)
    elif cmd in ("/id", "/contact", "/контакт"):
        try:
            api("sendMessage", chat_id=chat_id,
                text="Нажмите кнопку и выберите человека — я запомню его числовой id, "
                     "и потом смогу писать ему от лица вашего аккаунта.",
                reply_markup=_request_contact_keyboard())
        except Exception as exc:  # noqa: BLE001
            send_text(chat_id, f"Не получилось показать кнопку: {str(exc)[:150]}")
    elif cmd in ("/contacts", "/контакты"):
        book = {v["id"]: v for v in contacts().values()}
        if not book:
            send_text(chat_id, "Справочник пуст. Добавьте человека: /id")
        else:
            lines = ["Известные контакты:"]
            for e in sorted(book.values(), key=lambda x: x.get("name") or ""):
                who = ("@" + e["username"]) if e.get("username") else (e.get("name") or "без имени")
                lines.append(f"• {who} — id {e['id']}")
            send_text(chat_id, "\n".join(lines))
    elif cmd in ("/write", "/напиши"):
        who, _, body = args.strip().partition(" ")
        body = body.strip()
        if not who or not body:
            send_text(chat_id, "Формат: /write @username текст сообщения\n"
                               "Человек должен быть в справочнике — добавьте через /id.")
        else:
            entry = find_contact(who)
            target_id = entry["id"] if entry else (int(who) if who.lstrip("-").isdigit() else None)
            if target_id is None:
                send_text(chat_id, f"«{who}» нет в справочнике. Добавьте его кнопкой: /id\n"
                                   "Telegram не позволяет боту искать людей по @username — "
                                   "нужен либо выбор контакта, либо его числовой id.")
            else:
                ok, err = send_as_account(target_id, body)
                send_text(chat_id, "Отправлено от лица вашего аккаунта." if ok
                          else f"Не отправилось: {err}")
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
    # Выбор контакта приходит БЕЗ текста — разбираем до проверки на пустоту, иначе id потеряется.
    shared = msg.get("users_shared") or msg.get("user_shared")
    if shared:
        if is_owner(sender):
            _remember_owner_chat(sender)
            handle_users_shared(chat_id, shared)
        return
    # Пересланное сообщение — второй штатный способ узнать числовой id человека: Telegram
    # кладёт автора оригинала в forward_origin/forward_from. Работает, только если человек не
    # закрыл пересылку в настройках приватности.
    if is_owner(sender) and handle_forward(chat_id, msg):
        return
    # Присланный контакт из адресной книги тоже несёт user_id.
    contact = msg.get("contact") or {}
    if contact.get("user_id") and is_owner(sender):
        entry = remember_contact({"user_id": contact["user_id"],
                                  "first_name": contact.get("first_name"),
                                  "last_name": contact.get("last_name")})
        send_text(chat_id, f"Записал: {entry['name'] or 'контакт'} — id {entry['id']}.\n"
                           f"Написать от лица аккаунта: /write {entry['id']} текст")
        return
    if not text:
        return
    journal(BOT_CHANNEL, chat_id, "in", text, kind="bot_dm", user=sender,
            tg_message_id=msg.get("message_id"))
    if not is_owner(sender):
        refusal = ("Я — внутренний агент компании Albery и работаю только с владельцем. "
                   "Если вам нужен доступ — напишите Евгению.")
        send_text(chat_id, refusal)
        journal(BOT_CHANNEL, chat_id, "out", refusal, kind="bot_dm", user=sender,
                meta={"denied": True})
        return
    remember_access_user_id(BOT_CHANNEL, sender)
    _remember_owner_chat(sender)
    react(chat_id, msg.get("message_id"), "👀")      # прочитал, думаю — как агент в Битриксе
    if handle_command(chat_id, text):
        return
    try:
        api("sendChatAction", chat_id=chat_id, action="typing")
    except Exception:  # noqa: BLE001
        pass
    try:
        answer = owner_turn(chat_id, text)
        send_text(chat_id, answer)
        react(chat_id, msg.get("message_id"), "👍")   # ответил
        journal(BOT_CHANNEL, chat_id, "out", answer, kind="bot_dm", user=sender)
    except Exception as exc:  # noqa: BLE001
        log.exception("owner turn failed")
        failure = (f"Не получилось ответить (мозг сбоит): {str(exc)[:150]}. "
                   "Попробуйте ещё раз через минуту.")
        send_text(chat_id, failure)
        # status=error: в кабинете такие ходы видно как сбойные, а не как обычный ответ.
        journal(BOT_CHANNEL, chat_id, "out", failure, kind="bot_dm", user=sender, status="error")


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
    """Log an incoming message from the owner's personal chats (suppliers). Read-only in phase 1.

    Заодно САМ пополняет справочник контактов: во входящем есть и числовой id, и @username.
    Это и делает рассылку лидам автоматической — как только человек написал на аккаунт хоть
    раз, агент может писать ему сам, без участия владельца."""
    author = msg.get("from") or {}
    if author.get("id") and not author.get("is_bot"):
        try:
            remember_contact({"user_id": author["id"], "username": author.get("username"),
                              "first_name": author.get("first_name"),
                              "last_name": author.get("last_name")})
        except Exception:  # noqa: BLE001
            log.warning("не удалось записать контакт из входящего", exc_info=True)
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
    # Разговор владельца аккаунта с самим агентом — это отдельная подвкладка кабинета
    # («в боте»), она не должна смешиваться с перепиской по лидам.
    owner_id = _business_owner_id()
    text_in = (msg.get("text") or msg.get("caption") or "").strip()
    if owner_id and text_in and to_int_safe(author.get("id")) == owner_id:
        journal(MANAGER_CHANNEL, record["chat_id"], "in", text_in, kind="bot_dm", user=author,
                tg_message_id=msg.get("message_id"))
    if business_autoreply_enabled():
        try:
            maybe_autoreply(msg)
        except Exception:  # noqa: BLE001
            log.exception("автоответ в личке не удался")


def business_autoreply_enabled() -> bool:
    """Отвечать ли самому на входящие в личных чатах аккаунта (TG_BUSINESS_AUTOREPLY=1)."""
    return str(os.getenv("TG_BUSINESS_AUTOREPLY", "")).strip().lower() in {"1", "true", "yes"}


# --- белый список: отвечаем только лидам из воронки -------------------------------------------
# Аккаунт @AlberyAIManager живой: туда пишут не только лиды, но и поставщики, и знакомые.
# Автоответ разрешён ТОЛЬКО тем, чей Telegram указан в сделке воронки «Партнёрская программа
# WB — индивидуальные условия» (требование владельца 22.07.2026).
CRM_LEAD_CATEGORY_ID = int(os.getenv("CRM_LEAD_CATEGORY_ID", "16") or 16)
CRM_TELEGRAM_FIELD = os.getenv("CRM_TELEGRAM_FIELD", "UF_CRM_1784296997").strip()
_LEADS_CACHE: dict[str, Any] = {"at": 0.0, "map": {}, "ok": False}
_LEADS_TTL_S = float(os.getenv("CRM_LEADS_TTL_S", "300") or 300)


def _norm_username(value: str) -> str:
    """@Griaznov.D -> griaznov.d. Пустая строка, если это не похоже на username."""
    s = str(value or "").strip().lower()
    s = re.sub(r"^(https?://)?(t\.me/|telegram\.me/)", "", s)
    s = s.lstrip("@").strip()
    return s if re.fullmatch(r"[a-z0-9._-]{3,64}", s or "") else ""


def _squash(value: str) -> str:
    """griaznov.d и griaznov_d — почти наверняка один человек: в анкете точки ставят по ошибке,
    в самом Telegram точек в username не бывает."""
    return re.sub(r"[._-]", "", value or "")


def mcp_call(tool: str, arguments: dict) -> dict:
    """Вызвать инструмент Albery через локальный MCP приложения.

    Вебхук Bitrix не имеет прав на CRM (insufficient_scope), а MCP работает по OAuth приложения —
    тем же путём, что и все остальные инструменты системы. Импортировать app/b24bot в этот
    процесс нельзя: их импорт запускает живые планировщики."""
    secret = (os.getenv("MCP_SHARED_SECRET") or "").strip()
    if not secret:
        raise RuntimeError("MCP_SHARED_SECRET не задан")
    url = os.getenv("ALBERY_MCP_URL", "http://127.0.0.1:5002/mcp").strip()
    resp = requests.post(url, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }, headers={"Authorization": "Bearer " + secret,
                "Accept": "application/json, text/event-stream"}, timeout=45)
    raw = resp.text or ""
    if "data:" in raw[:200]:      # ответ может прийти потоком SSE
        raw = "\n".join(l[5:].strip() for l in raw.splitlines() if l.startswith("data:"))
    payload = json.loads(raw) if raw.strip() else {}
    if payload.get("error"):
        raise RuntimeError(str(payload["error"])[:300])
    result = payload.get("result") or {}
    content = result.get("structuredContent") or result
    if isinstance(content.get("content"), list):      # текстовая обёртка MCP
        for part in content["content"]:
            if part.get("type") == "text":
                try:
                    return json.loads(part.get("text") or "{}")
                except Exception:  # noqa: BLE001
                    pass
    return content


def crm_lead_usernames(force: bool = False) -> dict[str, int]:
    """{username: deal_id} по сделкам воронки лидов. Пустой словарь при недоступности CRM.

    Пустой ответ двусмыслен (воронка пуста ИЛИ CRM недоступна), поэтому успех запроса
    отмечается отдельно в _LEADS_CACHE["ok"] — см. crm_leads_reachable()."""
    now = time.time()
    if not force and _LEADS_CACHE["map"] and now - float(_LEADS_CACHE["at"]) < _LEADS_TTL_S:
        return dict(_LEADS_CACHE["map"])
    # Идём через локальный MCP приложения: вебхук Bitrix не имеет прав на CRM
    # (insufficient_scope), а MCP работает по OAuth приложения — тем же путём, что и все
    # остальные CRM-инструменты.
    out: dict[str, int] = {}
    try:
        content = mcp_call("list_crm_lead_contacts", {})
        for row in (content.get("contacts") or []):
            uname = _norm_username(row.get("username") or "")
            if uname:
                out[uname] = int(row.get("deal_id") or 0)
    except Exception:  # noqa: BLE001
        log.warning("не удалось прочитать лидов из CRM", exc_info=True)
        _LEADS_CACHE["ok"] = False
        return dict(_LEADS_CACHE["map"])
    _LEADS_CACHE.update({"at": now, "map": out, "ok": True})
    return dict(out)


def crm_leads_reachable() -> bool:
    """Удалось ли прочитать воронку. Незнакомцу пишут приглашение, а лиду — ответ; если CRM
    молчит, отличить одного от другого нельзя, и тогда безопаснее не писать вообще."""
    return bool(_LEADS_CACHE.get("ok"))


def lead_deal_for_username(username: str) -> int | None:
    """Номер сделки, если этот человек — лид воронки. Иначе None (значит не отвечаем)."""
    uname = _norm_username(username)
    if not uname:
        return None
    leads = crm_lead_usernames()
    if uname in leads:
        return leads[uname]
    squashed = _squash(uname)
    for known, deal_id in leads.items():
        if _squash(known) == squashed:
            return deal_id
    return None


# --- разговор с незнакомцем ---------------------------------------------------------------------
# Написал человек, которого нет в воронке. Он ведёт себя как живой человек: здоровается, о чём-то
# спрашивает — значит и отвечать надо как живой менеджер, а не выдавать всем одну и ту же простыню.
# Поэтому ответ сочиняет мозг, опираясь на базу знаний воронки в Google Drive («База знаний —
# Партнёрская программа WB»), а ссылка на анкету добавляется к первому ответу хвостом.
# Чего в базе нет — агент НЕ придумывает: он передаёт вопрос живому менеджеру (эскалация).

# ПУБЛИЧНАЯ ссылка формы (/pub/form/...). Адрес вида /crm/form/detail/... — это карточка формы
# внутри портала: клиента он уводит на страницу входа в Битрикс, а не на анкету.
LEAD_FORM_URL = os.getenv(
    "CRM_LEAD_FORM_URL", "https://b24-0xrp3s.bitrix24.ru/pub/form/2_nvunq5/").strip()
_INVITE_COOLDOWN_S = float(os.getenv("TG_INVITE_COOLDOWN_DAYS", "30") or 30) * 86400

# Хвост с анкетой. Обычный текст без разметки: ответ мозга подставляется рядом, а любой <, > или &
# из его ответа сломал бы HTML-режим и Telegram отклонил бы сообщение целиком.
# Ссылка, по которой лид приходит в чат ПОСЛЕ формы: текст подставляется в поле ввода, ему
# остаётся нажать «отправить». Так агент сразу понимает контекст, а не выспрашивает заново.
LEAD_CHAT_URL = os.getenv(
    "LEAD_CHAT_URL", "https://t.me/AlberyAIManager?text=%D0%97%D0%B4%D1%80%D0%B0%D0%B2%D1%81%D1%82%D0%B2%D1%83%D0%B9%D1%82%D0%B5%21%20%D0%A4%D0%BE%D1%80%D0%BC%D1%83%20%D0%BE%D1%82%D0%BF%D1%80%D0%B0%D0%B2%D0%B8%D0%BB%2C%20%D0%BA%D0%B0%D0%BA%D0%B8%D0%B5%20%D0%BC%D0%BE%D0%B8%20%D0%B4%D0%B0%D0%BB%D1%8C%D0%BD%D0%B5%D0%B9%D1%88%D0%B8%D0%B5%20%D0%B4%D0%B5%D0%B9%D1%81%D1%82%D0%B2%D0%B8%D1%8F%3F").strip()

FORM_TAIL = (
    "\n\n———\n"
    "📝 Чтобы подобрать условия под ваш магазин, заполните короткую анкету — пара минут:\n"
    "{url}\n"
    "Как заполните, вернитесь сюда и напишите — продолжим уже по вашим цифрам. 🤝"
)

# Мозг отвечает этим маркером, когда ответа в базе знаний нет. Тогда вопрос уходит живым людям
# в группу «Работа с ИУ», а клиенту агент НЕ пишет ничего (владелец, 22.07.2026): отписка
# «уточню у коллег и вернусь» обещает ответ, который агент сам дать не может, и клиент считает
# минуты. Пауза без обещания честнее — а сотрудник тем временем отвечает по-настоящему.
ESCALATION_MARKER = "НУЖЕН_ЧЕЛОВЕК"

STRANGER_PROMPT = (
    "Ты — менеджер компании Albery, отвечаешь в Telegram от лица аккаунта компании. Пишет "
    "человек, которого ещё нет в CRM. Веди себя как живой человек, а не как автоответчик.\n"
    "\n"
    "Как отвечать:\n"
    "- по-русски, обычным текстом без разметки, 1-3 предложения, тепло и по-деловому;\n"
    "- поздоровался — поздоровайся в ответ и спроси, чем можешь помочь;\n"
    "- задал вопрос — СНАЧАЛА поищи ответ в базе знаний компании инструментом "
    "search_company_knowledge (раздел «База знаний — Партнёрская программа WB»), затем отвечай "
    "по найденному;\n"
    "- никогда не представляйся ботом или ИИ и не упоминай, что смотришь какие-то документы.\n"
    "\n"
    "ЗАПРЕЩЕНО придумывать факты о компании: условия, цены, комиссии, сроки, гарантии. Если в базе "
    "знаний ответа нет или он неполный — не сочиняй и не отвечай общими словами. Вместо этого "
    f"верни РОВНО одну строку в формате: {ESCALATION_MARKER}: <суть вопроса клиента в 10 словах>. "
    "Ничего больше в таком случае не пиши.\n"
    "\n"
    "Собеседник: {name}\n"
    "Его сообщение:\n{text}"
)


def lead_invite_enabled() -> bool:
    return (os.getenv("TG_LEAD_INVITE") or "").strip().lower() in ("1", "true", "yes", "on")


def _invite_already_sent(user_id: int) -> bool:
    """Приглашали ли этого человека недавно. Второй раз одно и то же слать нельзя."""
    sent_at = (load_state().get("invited") or {}).get(str(user_id))
    if not sent_at:
        return False
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(sent_at)).total_seconds()
    except Exception:  # noqa: BLE001
        return True      # дата битая — считаем, что уже писали, чтобы точно не задваивать
    return age < _INVITE_COOLDOWN_S


def _mark_invited(user_id: int) -> None:
    with _state_lock:
        state = load_state()
        state.setdefault("invited", {})[str(user_id)] = datetime.now(timezone.utc).isoformat()
        save_state(state)


IU_AGENT_NAME = os.getenv("IU_AGENT_NAME", "Агент по работе с ИУ").strip()


def escalate_to_human(author: dict, question: str, client_text: str) -> None:
    """Принести вопрос лида живым людям в группу Битрикса «Работа с ИУ».

    Ответ сотрудника в той же группе агент передаёт клиенту сам — поэтому в карточке есть
    telegram id: без него передать ответ будет некому."""
    uid = author.get("id")
    uname = author.get("username") or ""
    name = " ".join(x for x in (author.get("first_name"), author.get("last_name")) if x).strip()
    # Оформление — по стандарту компании: блоки через пустую строку, заголовки [b]…[/b].
    # Клиент в этот момент СИДИТ БЕЗ ОТВЕТА, поэтому карточка начинается со срочности: сотрудник
    # должен понять это с первой строки, а не вычитать из середины.
    card = (f"[b]⚠️ Клиент ждёт ответа — ему пока НИЧЕГО не отвечено[/b]\n"
            f"\n"
            f"Пользователь задал вопрос: «{client_text[:600]}»\n"
            f"Что мне на него ответить?\n"
            f"\n"
            f"[b]Клиент[/b]\n"
            f"{name or 'без имени'}" + (f", @{uname}" if uname else "")
            + f", telegram id {uid}\n"
            f"\n"
            f"[b]В базе знаний не нашлось[/b]\n"
            f"{question}\n"
            f"\n"
            f"———\n"
            f"\n"
            f"Скажите мне здесь: «{IU_AGENT_NAME}, ответь, что …» — и я передам ответ клиенту "
            f"в Telegram.")
    try:
        res = mcp_call("notify_iu_group", {"text": card})
        if not res.get("sent"):
            raise RuntimeError(str(res)[:200])
        log.info("вопрос лида %s принесён в группу «Работа с ИУ» (сообщение %s)",
                 uid, res.get("message_id"))
        return
    except Exception as exc:  # noqa: BLE001
        log.warning("группа недоступна (%s) — дублирую вопрос в Telegram", str(exc)[:200])
    # Запасной канал: вопрос клиента не должен потеряться из-за сбоя Битрикса.
    chat_id = (os.getenv("TG_ESCALATION_CHAT_ID") or "").strip() or _business_owner_id()
    if not chat_id:
        log.warning("эскалация некуда: группа недоступна и TG_ESCALATION_CHAT_ID не задан")
        return
    try:
        # BB-коды живут только в Битриксе; в Telegram они дошли бы до человека как мусор.
        api("sendMessage", chat_id=chat_id, text=_strip_markup(card))
        log.info("эскалация по %s ушла в Telegram (запасной канал)", uid)
    except Exception as exc:  # noqa: BLE001
        log.warning("эскалация не доставлена вообще: %s", str(exc)[:200])


def escalated(author: dict, answer: str, client_text: str) -> bool:
    """Вопрос без ответа в базе: унести людям в группу и промолчать в чате.

    Одна точка на обе ветки — лида и незнакомца. Раньше маркер обрабатывался только у
    незнакомцев, и лид воронки получал служебную строку «НУЖЕН_ЧЕЛОВЕК: …» прямо в чат."""
    if ESCALATION_MARKER not in answer:
        return False
    question = answer.split(":", 1)[-1].strip() if ":" in answer else client_text
    escalate_to_human(author, question[:200], client_text)
    return True


def reply_to_stranger(author: dict, text: str) -> bool:
    """Живой ответ незнакомцу: по базе знаний, с анкетой при первом контакте.

    Вопросы без ответа в базе уходят человеку — выдуманный ответ клиенту хуже паузы."""
    if not lead_invite_enabled():
        return False
    author_id = author.get("id")
    name = (author.get("first_name") or "").strip()
    # Роль из карточки ЗАМЕНЯЕТ встроенный сценарий, а не дополняет его. Склейка давала
    # противоречивый промпт: роль велит сперва проверить воронку, а встроенный текст — сразу
    # слать анкету. Агент шёл по встроенному, и владелец получал «после обработки анкеты
    # продолжим» вместо разговора (22.07.2026).
    role = channel_role_prompt(MANAGER_CHANNEL)
    uname = author.get("username") or "без username"
    base = (f"{role}\n\nСобеседник: {name or 'клиент'} (@{uname})\n"
            f"Его сообщение:\n{text}") if role else STRANGER_PROMPT.format(
                name=name or "клиент", text=text)
    try:
        answer = hermes_answer(_with_instructions(base, MANAGER_CHANNEL), f"tg-new-{author_id}",
                               toolsets=channel_toolsets(MANAGER_CHANNEL))
    except Exception as exc:  # noqa: BLE001
        log.warning("мозг не ответил незнакомцу %s: %s", author_id, str(exc)[:200])
        return False
    answer = _strip_markup((answer or "").strip())
    if not answer:
        return False

    if escalated(author, answer, text):
        # В чате — тишина: обещать ответ, которого у агента нет, хуже паузы. Но переписку в
        # журнал пишем, иначе в кабинете вопрос клиента исчезнет вместе с ответом.
        journal(MANAGER_CHANNEL, author_id, "in", text, kind="lead_chat", user=author)
        journal(MANAGER_CHANNEL, author_id, "out", "вопрос без ответа в базе — унесён людям "
                "в группу «Работа с ИУ», клиенту не отвечено", kind="lead_chat", user=author,
                status="ok", meta={"stranger": True, "escalated": True})
        return False

    # Анкета — хвостом к первому ответу, один раз: это приглашение, а не подпись под каждым словом.
    invite_now = LEAD_FORM_URL and not _invite_already_sent(author_id)
    if invite_now:
        answer = answer + FORM_TAIL.format(url=LEAD_FORM_URL)
    ok, err = send_as_account(author_id, answer[:3500])
    if ok and invite_now:
        _mark_invited(author_id)    # отмечаем только фактически доставленное
    # Незнакомец попадает в журнал только теперь: агент с ним заговорил. Пока он молчал, это была
    # обычная личная переписка аккаунта, которой в кабинете не место.
    journal(MANAGER_CHANNEL, author_id, "in", text, kind="lead_chat", user=author)
    journal(MANAGER_CHANNEL, author_id, "out", answer if ok else f"{answer}\n\n[не доставлено: {err}]",
            kind="lead_chat", user=author, status="ok" if ok else "error",
            meta={"stranger": True, "invited": bool(invite_now and ok)})
    log.info("ответ незнакомцу %s: %s%s", author_id, "отправлен" if ok else f"не ушёл ({err})",
             " (+анкета)" if invite_now and ok else "")
    return ok


def _business_owner_id() -> int | None:
    """Числовой id владельца аккаунта, к которому подключён бот."""
    for info in (load_state().get("business") or {}).values():
        if info.get("user_id"):
            return int(info["user_id"])
    return None


def maybe_autoreply(msg: dict) -> None:
    """Ответить лиду в личке ОТ ЛИЦА аккаунта компании.

    Отвечаем ТОЛЬКО на входящие от живых людей. Свои же исходящие тоже приходят этим
    апдейтом, и без фильтра агент отвечал бы сам себе бесконечно."""
    author = msg.get("from") or {}
    chat = msg.get("chat") or {}
    author_id = author.get("id")
    text = (msg.get("text") or msg.get("caption") or "").strip()
    if not author_id or not text:
        return
    if author.get("is_bot"):
        return
    owner_id = _business_owner_id()
    if owner_id and author_id == owner_id:
        return  # это исходящее самого владельца, а не входящее от клиента
    if str(chat.get("type") or "private") != "private":
        return  # phase 2: только личные переписки
    conn_id = msg.get("business_connection_id") or ""
    if not conn_id:
        return

    # Отвечаем ТОЛЬКО лидам воронки. Аккаунт живой: поставщикам и знакомым агент писать не
    # должен. Если CRM недоступна, список пуст — и мы молчим, а не отвечаем всем подряд.
    username = author.get("username") or ""
    deal_id = lead_deal_for_username(username)
    if deal_id is None:
        if not crm_leads_reachable():
            log.warning("CRM недоступна — не пишем %s: лида не отличить от незнакомца", author_id)
            return
        # Человека в воронке нет: разговариваем как менеджер и даём анкету, чтобы он стал лидом.
        reply_to_stranger(author, text)
        return

    # В журнал попадают только переписки, где участвует агент: у лида воронки он ведёт разговор.
    journal(MANAGER_CHANNEL, author_id, "in", text, kind="lead_chat", user=author,
            tg_message_id=msg.get("message_id"), meta={"deal_id": deal_id})
    conn_id_react = msg.get("business_connection_id") or ""
    react(author_id, msg.get("message_id"), "👀", conn_id_react)

    name = (author.get("first_name") or "").strip()
    role = channel_role_prompt(MANAGER_CHANNEL)
    prompt = (
        f"{role}\n\n"
        f"Сделка в CRM: №{deal_id} (воронка «Партнёрская программа WB — индивидуальные условия»)\n"
        f"Собеседник: {name or 'клиент'} (@{username or 'без username'})\n"
        f"Его сообщение:\n{text}"
    ) if role else (
        "Ты ведёшь переписку в Telegram ОТ ЛИЦА компании Albery (аккаунт менеджера). "
        "Пишет ЛИД по партнёрской программе Wildberries — он оставил заявку на индивидуальные "
        "условия. Отвечай по-русски, коротко и по-человечески, обычным текстом без разметки, "
        "как менеджер в мессенджере — 1-3 предложения. Не представляйся ботом и не пиши, что ты "
        "ИИ. Если для ответа не хватает данных, задай один уточняющий вопрос. Если вопрос вне "
        "твоей компетенции или требует решения человека — скажи, что уточнишь у коллег и "
        "вернёшься с ответом.\n\n"
        f"Сделка в CRM: №{deal_id} (воронка «Партнёрская программа WB — индивидуальные условия»)\n"
        f"Собеседник: {name or 'клиент'}\n"
        f"Его сообщение:\n{text}"
    )
    try:
        answer = hermes_answer(_with_instructions(prompt, MANAGER_CHANNEL), f"tg-biz-{author_id}",
                               toolsets=channel_toolsets(MANAGER_CHANNEL))
    except Exception as exc:  # noqa: BLE001
        log.warning("мозг не ответил лиду %s: %s", author_id, str(exc)[:200])
        journal(MANAGER_CHANNEL, author_id, "out", f"мозг не ответил: {str(exc)[:200]}",
                kind="lead_chat", user=author, status="error", meta={"deal_id": deal_id})
        return
    answer = _strip_markup((answer or "").strip())
    if not answer:
        return
    if escalated(author, answer, text):
        journal(MANAGER_CHANNEL, author_id, "out", "вопрос без ответа в базе — унесён людям "
                "в группу «Работа с ИУ», клиенту не отвечено", kind="lead_chat", user=author,
                status="ok", meta={"deal_id": deal_id, "escalated": True})
        return
    ok, err = send_as_account(author_id, answer[:3500])
    journal(MANAGER_CHANNEL, author_id, "out", answer if ok else f"{answer}\n\n[не доставлено: {err}]",
            kind="lead_chat", user=author, status="ok" if ok else "error",
            meta={"deal_id": deal_id})
    log.info("автоответ лиду %s: %s", author_id, "отправлен" if ok else f"не отправлен ({err})")


def poll_forever() -> None:
    log.info("tg agent starting; owner ids=%s usernames=%s",
             sorted(owner_ids()), sorted(owner_usernames()))
    # Агенты, заведённые владельцем в кабинете, работают рядом — каждый своим потоком и своим
    # токеном. Сбой там не должен мешать основному боту: он несёт бизнес-режим и лидов.
    try:
        import tg_multi
        tg_multi.start_all()
    except Exception:  # noqa: BLE001
        log.exception("не удалось запустить дополнительных Telegram-агентов")
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
