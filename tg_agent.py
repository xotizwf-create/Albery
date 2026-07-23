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

import html
import json
import logging
import os
import re
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(),
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tg_agent")

APP_ROOT = Path(__file__).resolve().parent
STATE_PATH = APP_ROOT / ".tg_agent_state.json"
BUSINESS_LOG_PATH = APP_ROOT / ".tg_business_log.jsonl"
_state_lock = threading.Lock()
# Сколько ходов мозга идёт одновременно. Не «сколько влезет»: на боксе 2 ГБ, каждый ход — это
# отдельный процесс hermes, и без предела поток лидов положил бы службу целиком.
_HERMES_PARALLEL = max(1, int(os.getenv("TG_AGENT_PARALLEL_TURNS", "3") or 3))
_hermes_slots = threading.BoundedSemaphore(_HERMES_PARALLEL)
# Сообщения ОДНОГО человека обрабатываются строго по очереди: иначе два его сообщения подряд
# уходят в два параллельных хода, и второй не видит, что ответил первый.
_dialog_locks: dict[str, threading.Lock] = {}
_dialog_locks_guard = threading.Lock()
# Пул обработчиков апдейтов. Больше слотов мозга: пока один разговор ждёт очереди на ход,
# остальные потоки успевают сделать лёгкую работу (журнал, справочник контактов, реакции).
_workers = ThreadPoolExecutor(max_workers=_HERMES_PARALLEL * 4,
                              thread_name_prefix="tg-update")


def dialog_lock(dialog_id) -> threading.Lock:
    key = str(dialog_id)
    with _dialog_locks_guard:
        lock = _dialog_locks.get(key)
        if lock is None:
            lock = _dialog_locks[key] = threading.Lock()
        return lock


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


def chat_history(bot: str, dialog_id, current_text: str = "", limit: int = 12) -> str:
    """Последние сообщения этого диалога — чтобы агент помнил, о чём уже говорили.

    Без истории каждый ход был чистым листом: клиент здоровался, агент отвечал «Здравствуйте!»,
    клиент спрашивал по делу — и агент здоровался ВТОРОЙ раз, будто видит человека впервые
    (жалоба владельца 22.07.2026, переписка с @AlberyAIManager 23:06-23:08).

    Служебные записи об эскалации в историю не идут: клиенту они не отправлялись, и агент
    не должен считать, что уже что-то ответил."""
    try:
        with _db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT direction, text FROM telegram_bot_messages"
                    " WHERE bot = %s AND dialog_id = %s AND status = 'ok'"
                    "   AND COALESCE(meta->>'escalated', '') <> 'true'"
                    " ORDER BY id DESC LIMIT %s",
                    (bot, str(dialog_id), limit),
                )
                rows = list(cur.fetchall())[::-1]
    except Exception:  # noqa: BLE001 — без истории агент ответит хуже, но ответит
        log.warning("история диалога %s недоступна", dialog_id, exc_info=True)
        return ""
    # Текущее сообщение уже могло попасть в журнал — в промпте оно идёт отдельно, дублировать
    # его в истории значит показать агенту, будто клиент написал это дважды.
    while rows and rows[-1]["direction"] == "in" and (rows[-1]["text"] or "").strip() == (current_text or "").strip():
        rows.pop()
    if not rows:
        return ""
    lines = [f"{'Клиент' if r['direction'] == 'in' else 'Ты'}: {(r['text'] or '').strip()[:400]}"
             for r in rows if (r["text"] or "").strip()]
    return "\n".join(lines)


def _dialog_out_watermark(dialog_id) -> int:
    """Наибольший id исходящего в этом диалоге — отметка «до хода мозга».

    Инструменты, которые сами пишут клиенту (send_terms, send_contract), выполняются в ДРУГОМ
    процессе — MCP приложения, а не в службе tg-агента. Поэтому факт их отправки виден отсюда
    только через общий журнал. По этой отметке после хода видно, отправил ли инструмент
    сообщение клиенту сам."""
    try:
        with _db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COALESCE(MAX(id), 0) AS m FROM telegram_bot_messages"
                    " WHERE bot = %s AND dialog_id = %s AND direction = 'out'",
                    (MANAGER_CHANNEL, str(dialog_id)))
                return int((cur.fetchone() or {}).get("m") or 0)
    except Exception:  # noqa: BLE001 — журнал недоступен: отметки нет, гасить нечем
        log.warning("отметка журнала для %s недоступна", dialog_id, exc_info=True)
        return -1


def _out_messages_after(dialog_id, since_id: int) -> int:
    """Сколько сообщений КЛИЕНТУ реально ушло в этом диалоге после отметки.

    0 — законная отметка «до хода исходящих не было»: значит любой исходящий id>0 сделан этим
    ходом. Отрицательная отметка — отметку снять не удалось, тогда судить нельзя и не гасим.
    Служебная запись об эскалации (meta.escalated) клиенту не отправлялась — её не считаем."""
    if since_id < 0:       # отметку снять не удалось — судить не можем, не гасим
        return 0
    try:
        with _db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) AS n FROM telegram_bot_messages"
                    " WHERE bot = %s AND dialog_id = %s AND direction = 'out'"
                    "   AND status = 'ok' AND id > %s"
                    "   AND COALESCE(meta->>'escalated', '') <> 'true'",
                    (MANAGER_CHANNEL, str(dialog_id), int(since_id)))
                return int((cur.fetchone() or {}).get("n") or 0)
    except Exception:  # noqa: BLE001
        log.warning("проверка журнала на дубль для %s недоступна", dialog_id, exc_info=True)
        return 0


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
# Разделы инструкций в порядке важности для разговора с клиентом; остальные идут после.
_INSTR_PRIORITY = {"Работа с клиентами": 0, "Формат ответа": 1}


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
        # То, что определяет РАЗГОВОР с клиентом, идёт первым и целиком: к агенту подключены
        # и объёмные инструкции по работе в системе (десятки килобайт), и без явного порядка
        # они съедали бы лимит, а правила общения обрезались бы на середине.
        items.sort(key=lambda i: (_INSTR_PRIORITY.get(i["path"].split(" / ")[0], 9), i["path"]))
        picked = [f"# {i['name']}\n{i['content'].strip()}"[:_INSTR_DOC_CAP] for i in items]
    except Exception:  # noqa: BLE001 — без оформления агент ответит хуже, но ответит
        log.warning("инструкции агента %s не загрузились", channel, exc_info=True)
        return _INSTR_CACHE["text"] or ""
    text = "\n\n".join(picked)[:_INSTR_CAP]
    _INSTR_CACHE.update({"at": now, "text": text})
    return text


def _with_history(prompt: str, dialog_id, current_text: str) -> str:
    history = chat_history(MANAGER_CHANNEL, dialog_id, current_text)
    if not history:
        return prompt
    return f"{prompt}\n\nО чём вы уже говорили в этом чате:\n{history}"


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
    # Раньше здесь был ОДИН замок на всю службу: пока агент думал над одним клиентом (а ход
    # занимает десятки секунд), все остальные стояли в очереди. При потоке лидов десятый ждал
    # бы минуты. Теперь параллельно идут несколько ходов; предел держим осознанно — на боксе
    # 2 ГБ памяти, и неограниченный параллелизм убил бы службу вместе с ответами всем.
    waited = time.monotonic()
    with _hermes_slots:
        queued = time.monotonic() - waited
        if queued > 5:
            log.info("ход ждал очереди %.0f c (занято %s из %s слотов)",
                     queued, _HERMES_PARALLEL - _hermes_slots._value, _HERMES_PARALLEL)
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
    # Тот же вид, что и у собственных ответов агента: ссылки приходят кликабельной подписью,
    # а не голым адресом. Разметка косметическая — при отказе уходит обычный текст.
    ok, err = send_html(target, as_html(text), text)
    if not ok:
        raise RuntimeError(f"Telegram отказал: {err}")
    # В журнал — обязательно: это сообщение клиенту, и в кабинете переписка должна быть целой.
    # Без записи ответы, отправленные сотрудником через группу Битрикса, пропадали из истории,
    # и агент в следующем ходе не знал, что клиенту уже ответили (22.07.2026).
    journal(MANAGER_CHANNEL, target, "out", text, kind="lead_chat",
            user={"id": target, "username": (entry or {}).get("username"),
                  "first_name": (entry or {}).get("name")},
            meta={"relay": True, "via": "bitrix"})
    return {"sent": True, "to_id": target,
            "to": ("@" + entry["username"]) if (entry and entry.get("username")) else str(target),
            "from": "аккаунт владельца (Telegram Business)", "chars": len(text)}


def send_document_as_account(user_id: int, data: bytes, filename: str,
                             caption: str = "") -> tuple[bool, str]:
    """Отправить файл человеку от лица аккаунта компании (договор, счёт).

    До 23.07.2026 агент умел только текст, поэтому договор клиенту отправить не мог —
    и вместо файла присылал обещание «направим»."""
    state = load_state()
    conn_ids = list((state.get("business") or {}).keys())
    if not conn_ids:
        return False, "бизнес-подключение не настроено"
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{bot_token()}/sendDocument",
            data={"business_connection_id": conn_ids[0], "chat_id": int(user_id),
                  "caption": caption[:1000]},
            files={"document": (filename, data)},
            timeout=120,
        )
        body = r.json()
        if not body.get("ok"):
            return False, str(body.get("description") or body)[:200]
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:200]


CONTRACT_REQUISITES_FIELD = os.getenv("CRM_REQUISITES_FIELD", "UF_CRM_F84751394").strip()
CONTRACT_NUMBER_FIELD = os.getenv("CRM_CONTRACT_NUMBER_FIELD", "UF_CRM_F84792019").strip()
CONTRACT_FILE_FIELD = os.getenv("CRM_CONTRACT_FILE_FIELD", "UF_CRM_F84792018").strip()
CONTRACT_DATE_FIELD = os.getenv("CRM_CONTRACT_DATE_FIELD", "UF_CRM_F84792022").strip()
# Договор отправлен — человек уже не на «Согласовании условий», а на подписании.
CONTRACT_STAGE = os.getenv("CRM_CONTRACT_STAGE", "C16:NDA").strip()
_MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
           "сентября", "октября", "ноября", "декабря")


def contract_send(deal_id: int, telegram_id: int | str, requisites_text: str = "",
                  number: str = "") -> dict:
    """Собрать договор по реквизитам сделки и отправить клиенту PDF на согласование.

    Один вызов вместо цепочки «поставь задачу человеку → человек соберёт → человек отправит».
    Владелец 23.07.2026: агент должен заполнять шаблон сам, от клиента к клиенту меняются
    только реквизиты."""
    import contract as contract_mod

    from mcp import context_server as cs

    deal = cs.TOOLS["get_crm_deal"]["handler"]({"deal_id": int(deal_id)})
    deal = deal.get("deal") or deal
    uf = deal.get("custom_fields") or {}
    raw = (requisites_text or uf.get(CONTRACT_REQUISITES_FIELD) or "").strip()
    if not raw:
        raise ValueError(f"В сделке {deal_id} нет реквизитов — попроси их у клиента и запиши "
                         f"в поле {CONTRACT_REQUISITES_FIELD}.")
    fields = contract_mod.parse_requisites(raw)
    gaps = contract_mod.missing_fields(fields)
    if gaps:
        # Договор с дырами в реквизитах подписывать нельзя: сторона не определена.
        return {"sent": False, "missing": gaps,
                "note": ("Не хватает реквизитов: " + ", ".join(gaps)
                         + ". Спроси у клиента ИМЕННО их, не проси прислать всё заново.")}

    number = (number or uf.get(CONTRACT_NUMBER_FIELD) or "").strip()
    if not number:
        number = cs.TOOLS["next_contract_number"]["handler"](
            {"category_id": int(deal.get("category_id") or 16)})["number"]
    now = datetime.now(timezone.utc) + timedelta(hours=3)
    human_date = f"«{now.day:02d}» {_MONTHS[now.month - 1]} {now.year} г."

    pdf = contract_mod.render_contract_pdf(number, human_date, fields)
    filename = f"Договор {number}.pdf"
    ok, err = send_document_as_account(
        int(telegram_id), pdf, filename,
        caption=f"Договор № {number} на согласование. Посмотрите, всё ли верно")
    if not ok:
        raise RuntimeError(f"Договор собран, но не отправлен: {err}")

    # Договор кладём В САМУ СДЕЛКУ и двигаем стадию: иначе файл живёт только в Telegram, а
    # воронка показывает, что человек всё ещё на согласовании условий (владелец, 23.07.2026).
    import base64
    updates: dict = {
        "deal_id": int(deal_id),
        "stage": CONTRACT_STAGE,
        "custom_fields": {
            CONTRACT_NUMBER_FIELD: number,
            CONTRACT_DATE_FIELD: now.strftime("%Y-%m-%d"),
            CONTRACT_FILE_FIELD: {"fileData": [filename,
                                               base64.b64encode(pdf).decode("ascii")]},
        },
        "comments": f"Договор № {number} собран по шаблону и отправлен клиенту в Telegram "
                    f"на согласование. Файл приложен к сделке.",
    }
    try:
        cs.TOOLS["update_crm_deal"]["handler"](updates)
    except Exception as exc:  # noqa: BLE001
        # Файл клиенту уже ушёл — молча терять этот факт нельзя, но и падать поздно.
        log.warning("договор %s отправлен, но сделка %s не обновлена: %s",
                    number, deal_id, str(exc)[:200])
        cs.TOOLS["update_crm_deal"]["handler"]({
            "deal_id": int(deal_id), "stage": CONTRACT_STAGE,
            "custom_fields": {CONTRACT_NUMBER_FIELD: number},
            "comments": f"Договор № {number} отправлен клиенту. Файл приложить не удалось: "
                        f"{str(exc)[:200]}"})
    journal(MANAGER_CHANNEL, telegram_id, "out", f"[файл] {filename} — на согласование",
            kind="lead_chat", meta={"contract": number, "deal_id": int(deal_id)})
    gaps = contract_mod.unfilled_placeholders(
        contract_mod.fill_template(contract_mod.load_template(), fields, number, human_date))
    return {"sent": True, "number": number, "file": filename, "size_bytes": len(pdf),
            "client_name": fields.get("name"), "stage": CONTRACT_STAGE,
            "attached_to_deal": True, "unfilled_in_template": gaps,
            "note": ("PDF ушёл клиенту, приложен к сделке, стадия сдвинута на подписание. "
                     "Дождись подтверждения, что всё верно, и только потом ставь задачу на "
                     "подписание.")
                    + (f" ВНИМАНИЕ: в шаблоне не заполнено {len(gaps)} мест — скажи об этом "
                       f"владельцу." if gaps else "")}


SIGNING_FIELD = os.getenv("CRM_SIGNING_FIELD", "UF_CRM_F84751395").strip()
TERMS_SENT_FIELD = os.getenv("CRM_TERMS_SENT_FIELD", "").strip()
TERMS_DOC_NAME = os.getenv("TERMS_DOC_NAME", "Условия ИУ — текст для клиента").strip()
TERMS_MARKER = "--- ТЕКСТ КЛИЕНТУ ---"
TERMS_QUESTION = "Есть вопросы по условиям?"


def _after_terms_marker(raw: str) -> str:
    """Взять из документа ТОЛЬКО текст клиенту — часть после строки-маркера.

    Маркер ищем как ОТДЕЛЬНУЮ СТРОКУ, а не подстроку: в шапке документа он упомянут в самой
    инструкции («всё, что ниже строки "--- ТЕКСТ КЛИЕНТУ ---", отправляется дословно»), и
    разрез по первому вхождению отдавал клиенту остаток инструкции вместе с примером пометки
    [ЗАПОЛНИТЬ] — из-за этого агент отказывался слать уже заполненные условия (23.07.2026).

    Маркера нет вовсе — возвращаем пустоту: отправить документ целиком, вместе с инструкцией
    для владельца, хуже, чем не отправить ничего."""
    lines = raw.replace("\r\n", "\n").split("\n")
    marker_at = -1
    for i, line in enumerate(lines):
        if line.strip().strip("«»\"'").strip() == TERMS_MARKER:
            marker_at = i          # берём ПОСЛЕДНЮЮ такую строку
    if marker_at < 0:
        return ""
    return "\n".join(lines[marker_at + 1:]).strip()


def terms_text() -> str:
    """Условия для клиента — ДОСЛОВНО из документа владельца в базе знаний.

    Владелец 23.07.2026: агент должен слать условия слово в слово, а не пересказывать. Поэтому
    текст не идёт через модель: читаем документ и отправляем как есть."""
    from mcp import context_server as cs

    files = cs.TOOLS["list_company_files"]["handler"]({"limit": 300})
    wanted = TERMS_DOC_NAME.casefold()
    match = next((f for f in (files.get("files") or files.get("items") or [])
                  if wanted in str(f.get("name") or "").casefold() and f.get("google_file_id")),
                 None)
    if not match:
        raise ValueError(f"В базе знаний нет документа «{TERMS_DOC_NAME}» — отправлять нечего.")
    res = cs.TOOLS["get_company_file"]["handler"]({"google_file_id": match["google_file_id"]})
    raw = str(res.get("content") or res.get("text") or "")
    body = _after_terms_marker(raw)
    # Служебную шапку базы знаний клиенту показывать нельзя.
    body = re.sub(r"^(?:Источник|Обновлено в Google Drive|Тип):.*$", "", body,
                  flags=re.MULTILINE).strip()
    if not body:
        raise ValueError(
            f"В документе «{TERMS_DOC_NAME}» нет строки «{TERMS_MARKER}» или под ней пусто — "
            f"отправлять нечего. Скажи владельцу.")
    if "[ЗАПОЛНИТЬ]" in body:
        # Неполные условия у клиента хуже паузы: молча слать заготовку нельзя.
        raise ValueError(
            f"В документе «{TERMS_DOC_NAME}» остались пометки [ЗАПОЛНИТЬ] — условия клиенту не "
            f"отправлены. Скажи об этом владельцу и попроси дозаполнить документ.")
    return body


def send_terms(deal_id: int, telegram_id: int) -> dict:
    """Отправить клиенту условия дословно и спросить, есть ли вопросы."""
    body = terms_text()
    message = f"{body}\n\n{TERMS_QUESTION}"
    ok, err = send_html(int(telegram_id), as_html(message), message)
    if not ok:
        raise RuntimeError(f"Условия не отправлены: {err}")
    journal(MANAGER_CHANNEL, telegram_id, "out", message, kind="lead_chat",
            meta={"terms": True, "deal_id": int(deal_id) if deal_id else None})
    if deal_id:
        fields = {TERMS_SENT_FIELD: datetime.now(timezone.utc).strftime("%Y-%m-%d")} \
            if TERMS_SENT_FIELD else {}
        try:
            from mcp import context_server as cs
            cs.TOOLS["update_crm_deal"]["handler"](
                {"deal_id": int(deal_id), **({"custom_fields": fields} if fields else {}),
                 "comments": "Условия отправлены клиенту дословно из документа базы знаний."})
        except Exception:  # noqa: BLE001 — условия клиенту важнее отметки в CRM
            log.warning("отметка об отправке условий не записана (сделка %s)", deal_id,
                        exc_info=True)
    log.info("условия отправлены клиенту %s (сделка %s), %s символов",
             telegram_id, deal_id, len(body))
    return {"sent": True, "chars": len(body), "deal_id": deal_id,
            "note": ("Условия ушли клиенту дословно, вопрос про вопросы добавлен. Дальше отвечай "
                     "на его вопросы по базе знаний и не теряй следующий шаг — реквизиты.")}


def _enum_label(field: str, value) -> str:
    """Название варианта вместо его id.

    В сделке поле-список хранит id («84»), и агент сказал бы клиенту «способ подписания 84»."""
    if not _filled(value):
        return ""
    try:
        from mcp import context_server as cs
        items = cs._crm_enum_items().get(field.upper()) or {}
        for label, item_id in items.items():
            if str(item_id) == str(value).strip():
                return label.upper() if len(label) <= 4 else label.capitalize()
    except Exception:  # noqa: BLE001 — без словаря покажем как есть
        pass
    return str(value).strip()


def _filled(value) -> bool:
    """Заполнено ли поле сделки.

    Незаполненный список Битрикса приходит НУЛЁМ, а не пустотой: строка «0» правдива, и агент
    счёл бы способ подписания выбранным, хотя клиент его не называл (23.07.2026)."""
    text = str(value if value is not None else "").strip()
    return bool(text) and text not in {"0", "None", "[]", "{}"}

# Маршрут воронки: стадия → что уже должно быть сделано и что делать дальше. Считается по
# ФАКТАМ сделки, а не по памяти агента: 23.07.2026 клиент спросил «а что такое ЭДО?», агент
# ответил — и забыл, что за ответом «давайте ЭДО» должна была идти задача на отправку. Любое
# число вопросов между шагами теперь ничего не ломает: шаг приходит в каждом сообщении.
def funnel_next_step(deal: dict) -> dict:
    """Что агент обязан сделать на текущем шаге сделки."""
    uf = deal.get("custom_fields") or {}
    stage = str(deal.get("stage_id") or deal.get("stage") or "")
    deal_id = deal.get("deal_id") or deal.get("id") or deal.get("ID")
    has_req = _filled(uf.get(CONTRACT_REQUISITES_FIELD))
    has_contract = _filled(uf.get(CONTRACT_NUMBER_FIELD))
    signing = _enum_label(SIGNING_FIELD, uf.get(SIGNING_FIELD))
    # Отметку об отправке условий держим в поле сделки, если оно заведено; пока поля нет —
    # признаком служат уже собранные реквизиты (значит, условия давно позади).
    terms_sent = _filled(uf.get(TERMS_SENT_FIELD)) if TERMS_SENT_FIELD else has_req

    if stage in ("C16:NEW", "C16:CONTACTED"):
        return {"step": "Сверка анкеты",
                "need": "подтверждение данных анкеты",
                "action": (f"Как только клиент подтвердил данные — переведи сделку {deal_id} на "
                           f"стадию C16:S84294149 (update_crm_deal) и СРАЗУ вызови "
                           f"send_terms(deal_id={deal_id}, telegram_id=<id клиента>). Условия "
                           f"НЕ пересказывай своими словами: инструмент отправит их дословно из "
                           f"документа и сам спросит, есть ли вопросы.")}
    if stage == "C16:S84294149" and not terms_sent and not has_req:
        return {"step": "Отправка условий",
                "need": "ничего — условия отправляешь ты",
                "action": (f"Вызови send_terms(deal_id={deal_id}, telegram_id=<id клиента>). "
                           f"Он отправит условия ДОСЛОВНО и спросит про вопросы. Своими словами "
                           f"условия не рассказывай и из головы ничего не добавляй. Инструмент "
                           f"сказал, что в документе пометки [ЗАПОЛНИТЬ] — не отправляй ничего, "
                           f"сообщи владельцу через ТАКЖЕ_СПРОСИ_ЛЮДЕЙ.")}
    if stage == "C16:S84294149" and terms_sent and not has_req:
        return {"step": "Вопросы по условиям",
                "need": "вопросы клиента по условиям — или его согласие идти дальше",
                "action": (f"Условия клиент уже получил. Отвечай на его вопросы по базе знаний "
                           f"(search_company_knowledge), помня весь разговор. Нет фактов — "
                           f"ТАКЖЕ_СПРОСИ_ЛЮДЕЙ, но разговор продолжай. Когда вопросы "
                           f"закончились — попроси реквизиты организации для договора; как "
                           f"придут, запиши в {CONTRACT_REQUISITES_FIELD} сделки {deal_id} и "
                           f"вызови send_contract.")}
    if stage == "C16:S84294149" and not has_req:
        return {"step": "Сбор реквизитов",
                "need": "реквизиты организации (название, ИНН, КПП, ОГРН, адрес, р/с, банк, БИК, ФИО директора)",
                "action": (f"Как только реквизиты пришли — запиши их в поле "
                           f"{CONTRACT_REQUISITES_FIELD} сделки {deal_id} и СРАЗУ вызови "
                           f"send_contract(deal_id={deal_id}, telegram_id=<id клиента>). "
                           f"Не хватает части реквизитов — спроси именно недостающее.")}
    if stage == "C16:S84294149" or (stage == "C16:NDA" and not has_contract):
        return {"step": "Отправка договора",
                "need": "ничего — договор отправляешь ты",
                "action": (f"Реквизиты уже есть. Вызови send_contract(deal_id={deal_id}, "
                           f"telegram_id=<id клиента>) и попроси посмотреть, всё ли верно.")}
    if stage == "C16:NDA" and not signing:
        return {"step": "Выбор способа подписания",
                "need": "ответ клиента: ЭДО или бумага",
                "action": (f"ЭТО ГЛАВНОЕ, ЧТО СЕЙЧАС НУЖНО. Клиент может по дороге задать любые "
                           f"вопросы — ответь и ВЕРНИСЬ к этому. Как только он назвал способ: "
                           f"1) запиши его в поле {SIGNING_FIELD} сделки {deal_id}; "
                           f"2) create_bitrix_task ответственному (ИИ Агент, id 22) «Направить "
                           f"договор на подписание (<способ>)» со сроком 1 час, в описании — "
                           f"номер договора и реквизиты; "
                           f"3) СРАЗУ notify_client_when_task_done(задача, telegram_id клиента, "
                           f"текст «договор отправили, посмотрите и подпишите»); "
                           f"4) скажи клиенту, что направляешь, и что напишешь, когда уйдёт.")}
    if stage == "C16:NDA" and signing:
        return {"step": "Договор на подписании",
                "need": "подтверждение клиента, что подписал",
                "action": (f"Способ подписания уже выбран ({signing}). Если задача на отправку "
                           f"ещё не поставлена — поставь и повесь на неё "
                           f"notify_client_when_task_done. Клиент сказал, что подписал → "
                           f"переведи сделку {deal_id} на C16:UC_SGZRVS.")}
    if stage == "C16:UC_SGZRVS":
        return {"step": "Счёт на оплату",
                "need": "ничего — счёт готовит бухгалтер",
                "action": (f"Поставь задачу бухгалтеру (ИИ Агент, id 22) «Выставить счёт по "
                           f"договору» со сроком 1 час, приложи реквизиты, и повесь "
                           f"notify_client_when_task_done с текстом про счёт. Переведи сделку "
                           f"{deal_id} на C16:PREPAYMENT_INVOIC.")}
    if stage == "C16:PREPAYMENT_INVOIC":
        return {"step": "Ожидание оплаты",
                "need": "подтверждение ОТ БУХГАЛТЕРА, что деньги пришли",
                "action": (f"Слова клиента «я оплатил» — не деньги на счету: стадию по ним не "
                           f"двигай. Подтвердил бухгалтер → сделка {deal_id} на C16:EXECUTING.")}
    if stage == "C16:EXECUTING":
        return {"step": "Подключение",
                "need": "то, что нужно технически для подключения кабинета",
                "action": (f"Направь клиенту инструкции и ссылки, запроси необходимое. Когда "
                           f"подключение сделано — сделка {deal_id} на C16:CONNECTED.")}
    return {"step": f"Стадия {stage}", "need": "—",
            "action": "Веди разговор по маршруту воронки; стадию не двигай без факта."}


def funnel_step_block(deal_id: int) -> str:
    """Текущий шаг воронки текстом — уходит в промпт КАЖДОГО сообщения."""
    try:
        from mcp import context_server as cs
        deal = cs.TOOLS["get_crm_deal"]["handler"]({"deal_id": int(deal_id)})
        deal = deal.get("deal") or deal
    except Exception:  # noqa: BLE001 — без шага агент ответит хуже, но ответит
        log.warning("шаг воронки для сделки %s не определён", deal_id, exc_info=True)
        return ""
    st = funnel_next_step(deal)
    return ("ТЕКУЩИЙ ШАГ ВОРОНКИ (считан из сделки прямо сейчас — это важнее твоей памяти о "
            f"разговоре):\n"
            f"- этап: {st['step']}\n"
            f"- ждёшь от клиента: {st['need']}\n"
            f"- что сделать: {st['action']}\n"
            "Клиент может задать сколько угодно вопросов по дороге — отвечай на них и "
            "возвращайся к этому шагу. Пока шаг не выполнен, он остаётся твоей задачей.")


def watch_task_for_client(bitrix_task_id: int, telegram_id: int, client_message: str,
                          deal_id: int | None = None, kind: str = "other",
                          next_stage: str = "") -> dict:
    """Поставить ожидание: задача закроется — клиенту уйдёт сообщение.

    Агент отвечает только на входящие, поэтому событие «сотрудник выполнил задачу» до него не
    доходило вовсе: 23.07.2026 договор ушёл в ЭДО, а клиент об этом не узнал."""
    if not str(client_message or "").strip():
        raise ValueError("Нужен текст, который получит клиент после закрытия задачи.")
    with _db() as conn:
        with conn.cursor() as cur:
            # Новое ожидание того же смысла ЗАМЕНЯЕТ старое: если по сделке пересоздали задачу
            # шага (23.07.2026 по сделке 92 висели задачи 1996 и 2006 с одним текстом), клиент
            # при закрытии обеих получил бы одно и то же дважды. kind='other' не трогаем: там
            # смысл определяется текстом, и один шаг не заменяет другой.
            if kind != "other":
                if deal_id:
                    cur.execute(
                        "UPDATE funnel_task_watch SET cancelled_at = now(),"
                        " note = %s WHERE notified_at IS NULL AND cancelled_at IS NULL"
                        " AND bitrix_task_id <> %s AND kind = %s AND deal_id = %s",
                        (f"заменено новой задачей {int(bitrix_task_id)}",
                         int(bitrix_task_id), kind, int(deal_id)))
                else:
                    cur.execute(
                        "UPDATE funnel_task_watch SET cancelled_at = now(),"
                        " note = %s WHERE notified_at IS NULL AND cancelled_at IS NULL"
                        " AND bitrix_task_id <> %s AND kind = %s AND deal_id IS NULL"
                        " AND telegram_id = %s",
                        (f"заменено новой задачей {int(bitrix_task_id)}",
                         int(bitrix_task_id), kind, int(telegram_id)))
            cur.execute(
                "INSERT INTO funnel_task_watch (bitrix_task_id, deal_id, telegram_id, kind,"
                " client_message, next_stage) VALUES (%s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (bitrix_task_id) WHERE notified_at IS NULL AND cancelled_at IS NULL"
                " DO UPDATE SET client_message = EXCLUDED.client_message,"
                "               next_stage = EXCLUDED.next_stage"
                " RETURNING id",
                (int(bitrix_task_id), int(deal_id) if deal_id else None, int(telegram_id),
                 kind, client_message.strip(), next_stage or None))
            row = cur.fetchone()
    log.info("ожидание закрытия задачи %s для клиента %s поставлено", bitrix_task_id, telegram_id)
    return {"watch_id": int(row["id"]), "bitrix_task_id": int(bitrix_task_id),
            "note": "Как только задачу закроют, клиент получит сообщение автоматически."}


def _cancel_watch(watch_id: int, note: str) -> None:
    """Снять ожидание с пометкой, почему оно больше не нужно."""
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE funnel_task_watch SET cancelled_at = now(), note = %s"
                        " WHERE id = %s", (note[:300], int(watch_id)))


def _watch_key(w: dict) -> tuple:
    """По какому признаку два ожидания — «одно и то же» для клиента.

    Обычно это сделка + вид шага (edo и т.п.). Для kind='other' смысл задаёт сам текст:
    два разных сообщения по одной сделке — это два разных события, их не склеиваем."""
    who = ("deal", w["deal_id"]) if w.get("deal_id") else ("tg", w["telegram_id"])
    kind = str(w.get("kind") or "other")
    if kind == "other":
        return (who, kind, (w.get("client_message") or "").strip())
    return (who, kind)


def check_finished_tasks(limit: int = 50) -> dict:
    """Пройтись по ожиданиям: закрытые задачи → сообщение клиенту. Крутится сторожем
    _task_watch_loop в службе tg-агента.

    Идемпотентно: отметка notified_at ставится сразу после доставки, поэтому повторный проход
    не отправит клиенту то же самое второй раз. context_server сюда НЕ импортируется: в
    процессе tg-агента его импорт запускает живые планировщики (та же причина, по которой
    существует mcp_call) — статус задачи берём прямым REST, сделку двигаем через MCP по HTTP."""
    sent, still_open, failed = [], 0, []
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, bitrix_task_id, deal_id, telegram_id, kind, client_message, next_stage"
                " FROM funnel_task_watch WHERE notified_at IS NULL AND cancelled_at IS NULL"
                " ORDER BY id LIMIT %s", (int(limit),))
            watches = list(cur.fetchall())
    served: set[tuple] = set()      # кому уже отправлено в этом проходе (по смыслу)
    for w in watches:
        key = _watch_key(w)
        if key in served:
            # Второе ожидание того же смысла (сделка 92, задачи 1996 и 2006, 23.07.2026):
            # клиент уже получил это сообщение в этом же проходе — второй раз слать нельзя.
            _cancel_watch(w["id"], f"дубль: клиенту уже сообщено в этом проходе "
                                   f"(задача {w['bitrix_task_id']})")
            continue
        try:
            status = str((_task_status(w["bitrix_task_id"]) or {}).get("status") or "")
        except Exception as exc:  # noqa: BLE001 — одна недоступная задача не должна ронять проход
            if "not found" in str(exc).lower() or "не найден" in str(exc).lower():
                _cancel_watch(w["id"], "задача удалена из Битрикса")
                continue
            failed.append({"task": w["bitrix_task_id"], "error": str(exc)[:150]})
            continue
        if not status:
            # Портал отвечает 200 без задачи — её удалили. Ждать её закрытия бессмысленно,
            # иначе ожидание висит вечно и каждый проход тратится на мёртвый запрос.
            _cancel_watch(w["id"], "задача удалена из Битрикса")
            continue
        if status not in _TASK_DONE_STATUSES:
            still_open += 1
            continue
        ok, err = send_html(w["telegram_id"], as_html(w["client_message"]), w["client_message"])
        if not ok:
            failed.append({"task": w["bitrix_task_id"], "error": f"не доставлено: {err}"})
            continue
        journal(MANAGER_CHANNEL, w["telegram_id"], "out", w["client_message"], kind="lead_chat",
                meta={"task_closed": w["bitrix_task_id"], "deal_id": w["deal_id"]})
        with _db() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE funnel_task_watch SET notified_at = now() WHERE id = %s",
                            (w["id"],))
        served.add(key)
        if w["deal_id"] and w["next_stage"]:
            try:
                # Через MCP по HTTP: у вебхука нет прав на CRM, а импортировать context_server
                # в процесс tg-агента нельзя.
                mcp_call("update_crm_deal",
                         {"deal_id": int(w["deal_id"]), "stage": w["next_stage"],
                          "comments": f"Задача {w['bitrix_task_id']} выполнена, клиенту сообщено."})
            except Exception:  # noqa: BLE001 — сообщение клиенту важнее записи в CRM
                log.warning("стадия сделки %s не сдвинулась", w["deal_id"], exc_info=True)
        sent.append({"task": w["bitrix_task_id"], "client": w["telegram_id"]})
        log.info("задача %s закрыта — клиенту %s отправлено уведомление",
                 w["bitrix_task_id"], w["telegram_id"])
    return {"checked": len(watches), "notified": len(sent), "still_open": still_open,
            "failed": failed}


_TASK_WATCH_INTERVAL_S = float(os.getenv("TG_TASK_WATCH_INTERVAL_S", "20") or 20)


def _task_watch_loop() -> None:
    """Сторож ожиданий: сотрудник закрыл задачу — клиент узнаёт в пределах интервала.

    Живёт отдельным потоком в службе tg-агента: Битрикс не шлёт сюда событий о закрытии
    задач, а агент отвечает только на входящие — без сторожа механизм ожиданий не работал
    вовсе (23.07.2026 владелец закрыл задачу, клиенту не ушло ничего). Когда ожиданий нет,
    Битрикс не дёргается: проход обходится одним запросом к своей БД."""
    while True:
        try:
            res = check_finished_tasks()
            if res.get("notified") or res.get("failed"):
                log.info("сторож задач: %s", res)
        except Exception:  # noqa: BLE001 — сторож не имеет права умереть от одного сбоя
            log.warning("сторож задач: проход не удался", exc_info=True)
        time.sleep(_TASK_WATCH_INTERVAL_S)


def start_task_watchdog() -> threading.Thread:
    """Запустить сторожа ожиданий фоновым потоком службы."""
    t = threading.Thread(target=_task_watch_loop, name="task-watch", daemon=True)
    t.start()
    log.info("сторож задач запущен (интервал %s c)", _TASK_WATCH_INTERVAL_S)
    return t


# Статусы Битрикса, при которых работа считается выполненной: 4 — «завершена», 5 — «закрыта».
_TASK_DONE_STATUSES = {"4", "5"}


def _task_status(task_id: int) -> dict:
    """Статус задачи напрямую через REST — на случай, если инструмента чтения задач нет."""
    from bitrix import BitrixClient
    cli = BitrixClient(os.getenv("BITRIX_WEBHOOK_BASE", "").strip())
    res = cli.call("tasks.task.get", {"taskId": int(task_id), "select": ["ID", "STATUS"]})
    task = (((res or {}).get("result") or {}).get("task")) or {}
    return {"status": str(task.get("status") or "")}


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

# Ссылка для клиентов — сайт компании (владелец, 22.07.2026). Прежний адрес /pub/form/… —
# внутренний адрес формы на портале; клиентам его не показываем.
LEAD_FORM_URL = os.getenv(
    "CRM_LEAD_FORM_URL", "https://b24-9qcm4m.bitrix24site.ru/").strip()
_INVITE_COOLDOWN_S = float(os.getenv("TG_INVITE_COOLDOWN_DAYS", "30") or 30) * 86400

# Хвост с анкетой. Обычный текст без разметки: ответ мозга подставляется рядом, а любой <, > или &
# из его ответа сломал бы HTML-режим и Telegram отклонил бы сообщение целиком.
# Ссылка, по которой лид приходит в чат ПОСЛЕ формы: текст подставляется в поле ввода, ему
# остаётся нажать «отправить». Так агент сразу понимает контекст, а не выспрашивает заново.
LEAD_CHAT_URL = os.getenv(
    "LEAD_CHAT_URL", "https://t.me/AlberyAIManager?text=%D0%97%D0%B4%D1%80%D0%B0%D0%B2%D1%81%D1%82%D0%B2%D1%83%D0%B9%D1%82%D0%B5%21%20%D0%A4%D0%BE%D1%80%D0%BC%D1%83%20%D0%BE%D1%82%D0%BF%D1%80%D0%B0%D0%B2%D0%B8%D0%BB%2C%20%D0%BA%D0%B0%D0%BA%D0%B8%D0%B5%20%D0%BC%D0%BE%D0%B8%20%D0%B4%D0%B0%D0%BB%D1%8C%D0%BD%D0%B5%D0%B9%D1%88%D0%B8%D0%B5%20%D0%B4%D0%B5%D0%B9%D1%81%D1%82%D0%B2%D0%B8%D1%8F%3F").strip()

# Хвост с анкетой — уже в HTML: ссылка приходит клиенту кликабельной подписью, как у агентов
# в Битриксе ([URL=…]…[/URL]), а не голым адресом.
FORM_TAIL = (
    "\n\n———\n"
    "Чтобы подобрать условия под ваш магазин, нужна короткая анкета — пара минут:\n"
    '<a href="{url}">Заполнить анкету</a>\n'
    "Как заполните, возвращайтесь сюда — продолжим уже по вашим цифрам 🤝"
)
# Тот же хвост без разметки — на случай, когда Telegram отверг HTML: адрес должен остаться
# видимым, иначе «Заполнить анкету» превратится в слова без ссылки.
FORM_TAIL_PLAIN = FORM_TAIL.replace('<a href="{url}">Заполнить анкету</a>', "{url}")


# Модель пишет ссылки по-человечески — [Заполнить анкету](https://…). Превращаем их в
# кликабельные подписи. Экранируем ВСЁ до этого: любой <, > или & из ответа мозга иначе
# сломал бы HTML-режим, и Telegram отклонил бы сообщение целиком.
_MD_LINK_RE = re.compile(r"\[([^\]\n]{1,80})\]\((https?://[^\s)]+)\)")


def as_html(text: str) -> str:
    safe = html.escape(text or "", quote=False)
    return _MD_LINK_RE.sub(lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', safe)


def send_html(user_id: int, body_html: str, plain: str) -> tuple[bool, str]:
    """Отправить размеченное сообщение, с откатом на обычный текст.

    Разметка косметическая: если Telegram придерётся к ней, клиент всё равно должен получить
    ответ — молчание из-за неудачного символа хуже сообщения без кликабельной ссылки."""
    ok, err = send_as_account(user_id, body_html[:3500], parse_mode="HTML")
    if ok:
        return True, ""
    log.warning("HTML-режим отклонён (%s) — шлём обычным текстом", err[:120])
    return send_as_account(user_id, plain[:3500])

# Мозг отвечает этим маркером, когда ответа в базе знаний нет. Тогда вопрос уходит живым людям
# в группу «Работа с ИУ», а клиенту агент НЕ пишет ничего (владелец, 22.07.2026): отписка
# «уточню у коллег и вернусь» обещает ответ, который агент сам дать не может, и клиент считает
# минуты. Пауза без обещания честнее — а сотрудник тем временем отвечает по-настоящему.
ESCALATION_MARKER = "НУЖЕН_ЧЕЛОВЕК"
# Второй случай: агенту ЕСТЬ что ответить по существу (порядок работы, уточняющий вопрос), но
# конкретики — цифр, сроков, гарантий — в базе нет. Молчать здесь неправильно: новый лид
# остался бы совсем без ответа. Тогда агент отвечает клиенту И отдельной строкой просит людей
# дать недостающее. Строка клиенту не уходит.
SIDE_ESCALATION_MARKER = "ТАКЖЕ_СПРОСИ_ЛЮДЕЙ"

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


def _card_conversation(uid, client_text: str, limit: int = 8) -> str:
    """Кусок переписки в карточку для группы.

    Без него сотрудник (и агент группы) видят голый вопрос и переспрашивают то, что клиент уже
    написал: 23.07.2026 клиент прислал реквизиты в Telegram, а агент группы ответил «реквизитов
    в истории диалога нет» — они были, просто в другом чате."""
    history = chat_history(MANAGER_CHANNEL, uid, client_text, limit=limit)
    if not history:
        return ""
    return f"[b]О чём говорили в чате с клиентом[/b]\n{history}\n\n"


def escalate_to_human(author: dict, question: str, client_text: str,
                      answered: bool = False) -> None:
    """Принести вопрос лида живым людям в группу Битрикса «Работа с ИУ».

    Ответ сотрудника в той же группе агент передаёт клиенту сам — поэтому в карточке есть
    telegram id: без него передать ответ будет некому."""
    uid = author.get("id")
    uname = author.get("username") or ""
    name = " ".join(x for x in (author.get("first_name"), author.get("last_name")) if x).strip()
    # Оформление — по стандарту компании: блоки через пустую строку, заголовки [b]…[/b].
    # Клиент в этот момент СИДИТ БЕЗ ОТВЕТА, поэтому карточка начинается со срочности: сотрудник
    # должен понять это с первой строки, а не вычитать из середины.
    card = ((f"[b]Клиенту отвечено по существу, но нужна конкретика от вас[/b]\n"
             if answered else
             f"[b]⚠️ Клиент ждёт ответа — ему пока НИЧЕГО не отвечено[/b]\n")
            + f"\n"
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
            + _card_conversation(uid, client_text)
            + f"———\n"
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


def split_side_question(author: dict, answer: str, client_text: str) -> str:
    """Вынуть из ответа строку «ТАКЖЕ_СПРОСИ_ЛЮДЕЙ: …», унести вопрос людям, вернуть чистый текст.

    Клиент получает ответ по существу, а недостающую конкретику сотрудники досылают следом."""
    if SIDE_ESCALATION_MARKER not in answer:
        return answer
    kept, question = [], ""
    for line in answer.splitlines():
        if line.strip().startswith(SIDE_ESCALATION_MARKER):
            question = line.split(":", 1)[-1].strip()
        else:
            kept.append(line)
    escalate_to_human(author, (question or client_text)[:200], client_text, answered=True)
    return "\n".join(kept).strip()


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
            f"\nЕго сообщение:\n{text}") if role else STRANGER_PROMPT.format(
                name=name or "клиент", text=text)
    # История добавляется к ЛЮБОМУ промпту, в том числе к запасному: помнить разговор агент
    # обязан и тогда, когда карточка недоступна.
    base = _with_history(base, author_id, text)
    # Отметка ДО хода: см. _autoreply_turn — если инструмент сам напишет клиенту, реплику
    # модели не дублируем.
    out_watermark = _dialog_out_watermark(author_id)
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

    answer = split_side_question(author, answer, text)
    if not answer:
        return False

    # Инструмент уже написал клиенту сам в этом ходе — реплику модели не дублируем.
    if _out_messages_after(author_id, out_watermark):
        log.info("незнакомец %s: инструмент уже ответил клиенту — реплику модели не дублируем",
                 author_id)
        return False

    # Анкета — хвостом к первому ответу, один раз: это приглашение, а не подпись под каждым словом.
    invite_now = LEAD_FORM_URL and not _invite_already_sent(author_id)
    body = as_html(answer)
    plain = answer
    if invite_now:
        body += FORM_TAIL.format(url=LEAD_FORM_URL)
        plain += FORM_TAIL_PLAIN.format(url=LEAD_FORM_URL)
    ok, err = send_html(author_id, body, plain)
    answer = plain
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
    апдейтом, и без фильтра агент отвечал бы сам себе бесконечно.

    Разговоры разных людей идут параллельно, сообщения одного — строго по очереди."""
    uid = (msg.get("from") or {}).get("id")
    if uid is None:
        return
    with dialog_lock(uid):
        _autoreply_turn(msg)


def _autoreply_turn(msg: dict) -> None:
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
        f"\n{funnel_step_block(deal_id)}\n"
        f"\nЕго сообщение:\n{text}"
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
    # Отметка ДО хода: по ней потом видно, отправил ли инструмент (условия/договор) сообщение
    # клиенту сам — тогда финальную реплику модели слать нельзя, это был бы дубль того же посыла.
    out_watermark = _dialog_out_watermark(author_id)
    try:
        answer = hermes_answer(_with_instructions(_with_history(prompt, author_id, text),
                                                  MANAGER_CHANNEL), f"tg-biz-{author_id}",
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
    answer = split_side_question(author, answer, text)
    if not answer:
        return
    # Инструмент (условия/договор) уже написал клиенту в этом ходе — его сообщение полное и с
    # вопросом внутри. Реплика модели «условия отправили вам сюда»/«договор направил вам сюда»
    # повторила бы тот же посыл ВТОРЫМ сообщением (жалоба владельца 23.07.2026). Гасим её.
    if _out_messages_after(author_id, out_watermark):
        log.info("лид %s: инструмент уже ответил клиенту в этом ходе — реплику модели не дублируем",
                 author_id)
        return
    ok, err = send_html(author_id, as_html(answer), answer)
    journal(MANAGER_CHANNEL, author_id, "out", answer if ok else f"{answer}\n\n[не доставлено: {err}]",
            kind="lead_chat", user=author, status="ok" if ok else "error",
            meta={"deal_id": deal_id})
    log.info("автоответ лиду %s: %s", author_id, "отправлен" if ok else f"не отправлен ({err})")


def _handle_update_safely(upd: dict) -> None:
    """Один апдейт в отдельном потоке. Сбой на одном клиенте не должен ронять остальных."""
    try:
        if upd.get("message"):
            handle_message(upd["message"])
        elif upd.get("business_connection"):
            handle_business_connection(upd["business_connection"])
        elif upd.get("business_message"):
            handle_business_message(upd["business_message"])
    except Exception:  # noqa: BLE001
        log.exception("update handling failed")


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
    # Сторож ожиданий: без него «задача закрыта → сообщение клиенту» не срабатывало никогда —
    # check_finished_tasks существовал, но его никто не вызывал (владелец, 23.07.2026).
    start_task_watchdog()
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
            # Обработка уходит в пул: ход мозга занимает десятки секунд, и раньше цикл стоял
            # на нём целиком — десятый написавший ждал бы минуты. Порядок сообщений ОДНОГО
            # человека держит dialog_lock, число одновременных ходов — _hermes_slots.
            _workers.submit(_handle_update_safely, upd)
        with _state_lock:
            state = load_state()
            state["offset"] = offset
            save_state(state)


if __name__ == "__main__":
    _load_env_file()
    if not bot_token():
        raise SystemExit("TG_AGENT_BOT_TOKEN is not configured")
    poll_forever()
