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

import hashlib
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
from typing import Any

import requests

import client_message      # единственная сборка сообщений, которые отправляет код
import decision_log        # трасса решений: что решили, по какому правилу и на каких фактах
import funnel_rules        # правила воронки как данные: факты → решение + его причина
import funnel_scenario     # настроенный владельцем сценарий воронки (кабинет)

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


def funnel_workspace_enabled() -> bool:
    """The custom inbox owns every customer send when enabled."""

    return str(os.getenv("FUNNEL_WORKSPACE_ENABLED", "0")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


# АГЕНТ = БОТ. @AlberyAIManager — не бот, а обычный аккаунт Telegram: он лишь подключил бота
# @Albery_AI2_Bot в «Telegram для бизнеса», и все ответы лидам физически шлёт бот, просто
# Telegram показывает их от лица аккаунта. Поэтому агент здесь один — бот, а два его источника
# диалогов различаются полем kind: bot_dm (пишут самому боту) и lead_chat (переписки аккаунта).
BOT_CHANNEL = "albery-ai-bot"
# Оставлено как псевдоним: бизнес-переписки ведёт тот же бот, отдельным агентом они не являются.
MANAGER_CHANNEL = BOT_CHANNEL


def customer_agent_slug() -> str:
    """Agent profile that answers customer conversations in the funnel workspace.

    The Telegram transport and the AI profile are deliberately different identities:
    ``BOT_CHANNEL`` names the physical gateway, while this slug selects the existing Albery
    agent's prompt, instructions and MCP boundary.  Changing the agent never creates a second
    Telegram consumer.
    """
    default_slug = "agent-po-rabote-s-iu"
    return (
        os.getenv("FUNNEL_WORKSPACE_AGENT_SLUG") or default_slug
    ).strip() or default_slug


def customer_toolset_slug() -> str:
    """Strict zero-tool connector used for untrusted customer text.

    The role/profile and the capability boundary are intentionally separate.  The existing
    ИУ agent supplies its role, while Telegram delivery, CRM writes and handoff are performed
    by deterministic workspace code.  Giving a customer prompt the ИУ agent's broad connector
    would expose owner-only mutation tools to prompt injection.
    """
    return (
        os.getenv("FUNNEL_WORKSPACE_CUSTOMER_TOOLSET_SLUG") or "iu-customer-runtime"
    ).strip() or "iu-customer-runtime"


def customer_toolsets() -> str:
    """Return only a manifest-capped, zero-tool connector or fail closed."""
    slug = customer_toolset_slug()
    try:
        from agent_knowledge import load_manifest

        manifest = load_manifest(slug)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("customer connector manifest is unavailable") from exc
    if "tools" not in manifest or manifest.get("tools"):
        raise RuntimeError(
            f"customer connector agent-{slug} is not capped to zero tools"
        )
    return f"agent-{slug}"


def owner_toolsets() -> str:
    """Trusted owner toolsets, structurally separate from the customer connector."""
    for value in (os.getenv("TG_AGENT_OWNER_TOOLSETS"), os.getenv("TG_AGENT_TOOLSETS")):
        if str(value or "").strip():
            return str(value).strip()
    return "albery,web"


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
    # Зеркалим сообщение в ленту сделки Битрикса — родная «переписка в карточке» (владелец,
    # 24.07.2026). Только реальные сообщения клиента и агента: служебные записи об эскалации
    # клиенту не отправлялись и в переписке не место. Best-effort — журнал важнее ленты.
    if kind == "lead_chat" and (text or "").strip() and status == "ok":
        m = meta or {}
        # escalated в оперативном meta — это bool True; в БД он станет строкой "true". Здесь
        # читаем dict, поэтому проверяем истинность, а не сравниваем со строкой.
        if m.get("deal_id") and not m.get("escalated"):
            _mirror_to_deal(m["deal_id"], direction, text)


_MIRROR_TO_DEAL = str(os.getenv("TG_MIRROR_TO_DEAL", "1")).strip().lower() in {"1", "true", "yes", "on"}


def _deal_comment_text(direction: str, text: str) -> str:
    """Одна реплика для ленты сделки: кто сказал + текст, с BB-разметкой Битрикса."""
    who = "Клиент" if direction == "in" else "Агент"
    return f"[B]{who}:[/B] {(text or '').strip()}"[:10000]


def _mirror_to_deal(deal_id, direction: str, text: str) -> None:
    """Отразить одно сообщение в ленту сделки. В фоне и best-effort: лента не должна ни
    тормозить ответ клиенту, ни ронять журнал, если CRM недоступна."""
    if not _MIRROR_TO_DEAL:
        return
    comment = _deal_comment_text(direction, text)

    def _post():
        try:
            mcp_call("add_deal_comment", {"deal_id": int(deal_id), "comment": comment})
        except Exception:  # noqa: BLE001 — лента сделки не критична для клиента
            log.warning("сообщение не отражено в ленте сделки %s", deal_id, exc_info=True)

    threading.Thread(target=_post, name="deal-mirror", daemon=True).start()


def backfill_deal_timeline(deal_id: int, force: bool = False) -> dict:
    """Один раз отразить всю уже накопленную переписку сделки в её ленту.

    Для существующих сделок, у которых переписка накопилась до включения зеркалирования.
    Идемпотентно: отметка в state.mirrored_deals, повтор не задваивает."""
    deal_id = int(deal_id)
    if not force and str(deal_id) in (load_state().get("mirrored_deals") or {}):
        return {"deal_id": deal_id, "posted": 0, "note": "лента уже заполнена"}
    try:
        with _db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT direction, text FROM telegram_bot_messages"
                    " WHERE bot = %s AND kind = 'lead_chat' AND status = 'ok'"
                    "   AND meta->>'deal_id' = %s"
                    "   AND COALESCE(meta->>'escalated', '') <> 'true'"
                    " ORDER BY id", (MANAGER_CHANNEL, str(deal_id)))
                rows = list(cur.fetchall())
    except Exception:  # noqa: BLE001
        log.warning("бэкфилл ленты: журнал недоступен (сделка %s)", deal_id, exc_info=True)
        return {"deal_id": deal_id, "posted": 0, "error": "журнал недоступен"}
    posted = 0
    for r in rows:
        if not (r["text"] or "").strip():
            continue
        try:
            mcp_call("add_deal_comment",
                     {"deal_id": deal_id, "comment": _deal_comment_text(r["direction"], r["text"])})
            posted += 1
        except Exception:  # noqa: BLE001 — CRM недоступна: не долбим, вернёмся в другой раз
            log.warning("бэкфилл ленты: запись не отражена (сделка %s)", deal_id, exc_info=True)
            return {"deal_id": deal_id, "posted": posted, "error": "CRM недоступна"}
    with _state_lock:
        st = load_state()
        st.setdefault("mirrored_deals", {})[str(deal_id)] = \
            datetime.now(timezone.utc).isoformat()
        save_state(st)
    return {"deal_id": deal_id, "posted": posted, "total": len(rows)}


def chat_history(bot: str, dialog_id, current_text: str | list = "", limit: int = 12) -> str:
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
    # Текущие сообщения (одно или пачка) уже могли попасть в журнал — в промпте они идут
    # отдельно, дублировать их в истории значит показать агенту, будто клиент написал дважды.
    current = ([current_text] if isinstance(current_text, str) else list(current_text or []))
    current_set = {t.strip() for t in current if t and t.strip()}
    while rows and rows[-1]["direction"] == "in" and (rows[-1]["text"] or "").strip() in current_set:
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
    для этого агента, — то же самое, что у субагентов Битрикса. Если connector не найден,
    возвращается ``None``; customer runner всё равно выбирает узкий fail-closed connector.

    This helper is customer-safe by construction: it never appends broad toolsets
    such as ``web``. Trusted owner turns use :func:`owner_toolsets` instead.
    """
    try:
        with _db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM agents WHERE slug = %s AND is_active", (channel,))
                if not cur.fetchone():
                    return None
    except Exception:  # noqa: BLE001
        return None
    return f"agent-{channel}"


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






def hermes_answer(prompt: str, session_prefix: str, toolsets: str | None = None,
                  timeout_s: int | None = None) -> str:
    customer_turn = _is_customer_session(session_prefix)
    if toolsets is None:
        # A database/connector lookup failure must not restore the broad default
        # for untrusted customer turns. The narrow connector may fail visibly,
        # but the customer can never inherit ``albery,web``.
        toolsets = (customer_toolsets() if customer_turn
                    else os.getenv("TG_AGENT_TOOLSETS", "albery,web"))
    timeout_s = timeout_s or int(os.getenv("TG_AGENT_HERMES_TIMEOUT", "420"))
    # Fresh session per run (hermes >=0.17 resumes --continue sessions; memory is prompt-injected)
    run_session = f"{session_prefix}-r{uuid.uuid4().hex[:8]}"
    cmd = ["hermes", "-z", prompt, "--continue", run_session, "-t", toolsets, "--yolo"]
    # Hermes v0.17 top-level oneshot (``-z``) does not accept ``--max-turns``.
    # Customer connectors have zero tools at P0, so a tool loop is structurally
    # impossible without passing an unsupported flag that would kill every turn.
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


def _is_customer_session(session_prefix: str) -> bool:
    return str(session_prefix).startswith(
        ("tg-new-", "tg-biz-", "tg-iu-", "answering-")
    )


def customer_hermes_answer(prompt: str, session_prefix: str, *,
                           timeout_s: int | None = None) -> str:
    """One untrusted customer turn through the zero-tool fail-closed connector."""
    kwargs = {"toolsets": customer_toolsets()}
    if timeout_s is not None:
        kwargs["timeout_s"] = timeout_s
    return hermes_answer(prompt, session_prefix, **kwargs)


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
    # Клиенты и владелец используют один Telegram-бот, но разные trust boundaries:
    # customer connector fail-closed, owner toolsets — доверенный внутренний контур.
    answer = hermes_answer("\n\n".join(parts), f"tg-owner-{chat_id}",
                           toolsets=owner_toolsets())
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


def _business_connection_id(preferred: str = "") -> tuple[str, str]:
    """Pick an enabled, reply-capable Business connection without exposing its id in logs.

    A conversation stores the exact connection that received the message.  Callers should pass
    it as ``preferred``; the fallback exists only for legacy proactive sends created before the
    workspace stored that relationship.
    """
    state = load_state()
    business = state.get("business") or {}
    wanted = str(preferred or "").strip()
    if wanted:
        info = business.get(wanted) or {}
        if not info:
            return "", "бизнес-подключение этого диалога больше не существует"
        if info.get("enabled") is False:
            return "", "бизнес-подключение этого диалога выключено"
        if info.get("can_reply") is False:
            return "", "у бизнес-подключения нет права отвечать"
        return wanted, ""
    for connection_id, info in business.items():
        info = info or {}
        if info.get("enabled") is False or info.get("can_reply") is False:
            continue
        return str(connection_id), ""
    return "", ("бизнес-подключение не настроено: подключите бота в Telegram → Настройки → "
                "Telegram для бизнеса → Чат-боты")


def send_as_account(user_id: int, text: str, parse_mode: str = "",
                    business_connection_id: str = "") -> tuple[bool, str]:
    """Написать человеку ОТ ЛИЦА аккаунта владельца (Telegram Business), а не от бота."""
    if funnel_workspace_enabled():
        return False, "прямая отправка отключена: используйте outbox рабочего пространства"
    connection_id, error = _business_connection_id(business_connection_id)
    if not connection_id:
        return False, error
    extra = {"parse_mode": parse_mode} if parse_mode else {}
    try:
        api("sendMessage", business_connection_id=connection_id, chat_id=int(user_id), text=text,
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
                             caption: str = "", business_connection_id: str = "") -> tuple[bool, str]:
    """Отправить файл человеку от лица аккаунта компании (договор, счёт).

    До 23.07.2026 агент умел только текст, поэтому договор клиенту отправить не мог —
    и вместо файла присылал обещание «направим»."""
    if funnel_workspace_enabled():
        return False, "прямая отправка отключена: используйте outbox рабочего пространства"
    connection_id, error = _business_connection_id(business_connection_id)
    if not connection_id:
        return False, error
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{bot_token()}/sendDocument",
            data={"business_connection_id": connection_id, "chat_id": int(user_id),
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


def _requisites_already_forwarded(dialog_id, deal_id) -> bool:
    """Уже говорили клиенту «передал менеджеру» по этой сделке? Второй раз нельзя."""
    try:
        with _db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM telegram_bot_messages WHERE bot = %s AND dialog_id = %s"
                    "   AND direction = 'out' AND meta->>'requisites_forwarded' = 'true'"
                    "   AND meta->>'deal_id' = %s LIMIT 1",
                    (MANAGER_CHANNEL, str(dialog_id), str(int(deal_id))))
                return cur.fetchone() is not None
    except Exception:  # noqa: BLE001 — журнал недоступен: лучше промолчать, чем задвоить
        log.warning("журнал недоступен (реквизиты, сделка %s)", deal_id, exc_info=True)
        return True


def _requisites_to_manager(cs, deal_id, telegram_id, raw: str, reason,
                           requisites_written: bool = False) -> dict:
    """Шаблона договора нет в базе — реквизиты уходят людям, клиент ждёт обратной связи.

    Владелец 24.07.2026 (шаблон временно удалён из базы знаний): реквизиты записать, клиенту —
    «передал информацию ответственному менеджеру, вернусь с обратной связью», в группу —
    «Человек отправил реквизиты, нужен следующий шаг». Без повторов при следующих ходах."""
    deal_id = int(deal_id)
    if _requisites_already_forwarded(telegram_id, deal_id):
        return {"sent": False, "forwarded": True,
                "note": ("Реквизиты уже переданы менеджеру, клиент предупреждён. Повторно об "
                         "этом НЕ пиши и договор собрать не пытайся — жди ответа людей в "
                         "группе «Работа с ИУ».")}
    msg = ("Реквизиты получил, спасибо. Передал информацию ответственному менеджеру — "
           "вернусь к вам с обратной связью.")
    ok, err = send_html(int(telegram_id), as_html(msg), msg)
    if ok:
        journal(MANAGER_CHANNEL, telegram_id, "out", msg, kind="lead_chat",
                meta={"deal_id": deal_id, "requisites_forwarded": True})
    else:
        log.warning("«передал менеджеру» не доставлено клиенту %s: %s", telegram_id, err[:150])
    who = next((e for e in contacts().values()
                if isinstance(e, dict) and to_int_safe(e.get("id")) == to_int_safe(telegram_id)),
               {})
    card = (f"[b]Человек отправил реквизиты, нужен следующий шаг[/b]\n"
            f"\n"
            f"[b]Клиент[/b]\n"
            f"{who.get('name') or 'без имени'}"
            + (f", @{who['username']}" if who.get("username") else "")
            + f", telegram id {telegram_id}, сделка №{deal_id}\n"
            f"\n"
            f"[b]Реквизиты[/b]\n{raw[:900]}\n"
            f"\n"
            f"Шаблона договора нет в базе знаний, договор не собирался. Клиенту сказано: "
            f"«передал менеджеру, вернусь с обратной связью».\n"
            f"\n"
            f"Скажите мне здесь: «{IU_AGENT_NAME}, ответь, что …» — и я передам ответ клиенту "
            f"в Telegram.")
    try:
        res = cs.TOOLS["notify_iu_group"]["handler"]({"text": card})
        if not res.get("sent"):
            raise RuntimeError(str(res)[:150])
    except Exception:  # noqa: BLE001 — карточка важна, но клиент уже предупреждён
        log.warning("карточка о реквизитах не дошла до группы (сделка %s)", deal_id,
                    exc_info=True)
    try:
        updates: dict = {"deal_id": deal_id,
                         "comments": "Реквизиты получены. Шаблона договора нет в базе знаний — "
                                     "передано менеджеру в группу «Работа с ИУ»."}
        if requisites_written and raw:
            updates["custom_fields"] = {CONTRACT_REQUISITES_FIELD: raw}
        cs.TOOLS["update_crm_deal"]["handler"](updates)
    except Exception:  # noqa: BLE001
        log.warning("сделка %s не обновлена после передачи реквизитов", deal_id, exc_info=True)
    log.info("реквизиты сделки %s переданы менеджеру (шаблона нет: %s)",
             deal_id, str(reason)[:120])
    return {"sent": False, "forwarded": True,
            "note": ("Шаблона договора нет в базе знаний. Реквизиты записаны и переданы "
                     "менеджеру в группу «Работа с ИУ»; клиенту уже сказано, что вернёмся с "
                     "обратной связью. Повторно об этом не пиши и договор не пытайся собрать "
                     "— жди людей.")}


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
    problems = contract_mod.validate_requisites(fields)
    if problems:
        # Клиент прислал «фигню» или опечатался: контрольные суммы ИНН/ОГРН это ловят
        # математикой (владелец, 24.07.2026). Договор с такими реквизитами юридически пуст.
        return {"sent": False, "invalid": problems,
                "note": ("Реквизиты не проходят проверку: " + "; ".join(problems)
                         + ". Скажи клиенту ПРЯМО, что именно не так, и попроси карточку "
                           "компании из банка или 1С. Договор не собран.")}

    # Шаблон читаем ДО номера и PDF: владелец может временно убрать его из базы знаний
    # (24.07.2026 — убрал). Тогда договор не собираем, а реквизиты передаём людям.
    try:
        template = contract_mod.load_template()
    except Exception as exc:  # noqa: BLE001 — нет шаблона/Drive недоступен: людям виднее
        return _requisites_to_manager(cs, deal_id, telegram_id, raw, exc,
                                      requisites_written=bool(requisites_text))

    number = (number or uf.get(CONTRACT_NUMBER_FIELD) or "").strip()
    if not number:
        number = cs.TOOLS["next_contract_number"]["handler"](
            {"category_id": int(deal.get("category_id") or 16)})["number"]
    now = datetime.now(timezone.utc) + timedelta(hours=3)
    human_date = f"«{now.day:02d}» {_MONTHS[now.month - 1]} {now.year} г."

    pdf = contract_mod.render_contract_pdf(number, human_date, fields, template=template)
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
        contract_mod.fill_template(template, fields, number, human_date))
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

# Все виды тире, в которые Google Docs «улучшает» дефисы: -, ‐, ‑, ‒, –, —, ―, − (минус).
_DASHES = "-‐‑‒–—―−"
_TERMS_MARKER_CORE = "ТЕКСТ КЛИЕНТУ"


def _is_terms_marker(line: str) -> bool:
    """Строка — это маркер начала клиентской части?

    Узнаём по СМЫСЛУ, а не по точному виду: Google Docs автозаменой превратил хвост «---» в
    длинное тире «—» («--- ТЕКСТ КЛИЕНТУ —»), точное сравнение сломалось, и агент говорил
    клиенту «техническая заминка по условиям» вместо самих условий (владелец, 24.07.2026).
    Теперь маркер — это строка, которая после снятия любых тире/кавычек/пробелов с краёв
    равна ровно «ТЕКСТ КЛИЕНТУ». Упоминание маркера внутри длинной фразы-инструкции в шапке
    так не свернётся и маркером не считается."""
    core = line.strip().strip("«»\"'").strip(_DASHES + " \t").strip()
    return re.sub(r"\s+", " ", core).casefold() == _TERMS_MARKER_CORE.casefold()


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
        if _is_terms_marker(line):
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


TELEGRAM_SAFE_TEXT_LIMIT = 3500


def _single_message_fits(body_html: str, plain: str) -> bool:
    """`send_html` не должен молча отрезать документ или CTA перед отметкой доставки."""
    return max(len(body_html), len(plain)) <= TELEGRAM_SAFE_TEXT_LIMIT


def send_terms(deal_id: int, telegram_id: int, *, offer_form: bool = False,
               resend_form: bool = False, ask_follow_up: bool = True) -> dict:
    """Отправить условия дословно и ровно один следующий шаг.

    Обычный путь заканчивается вопросом про условия. Если клиент в том же ходе явно решил
    подключаться, вопрос заменяется одной CTA анкеты — вопрос и форма рядом запрещены."""
    body = terms_text()
    form_status = _deal_has_form(deal_id) if offer_form else False
    invite_now = bool(
        offer_form and LEAD_FORM_URL
        and (resend_form or not _invite_already_sent(telegram_id))
        and form_status is False
    )
    # Документ остаётся слово в слово; вокруг — то, что сказал бы живой менеджер.
    message = client_message.compose(body, name=_name_for_uid(telegram_id),
                                     greet=_first_contact(telegram_id),
                                     lead_in=client_message.LEAD_IN_TERMS,
                                     follow_up="" if invite_now or not ask_follow_up
                                     else TERMS_QUESTION)
    if not client_message.verbatim_intact(message, body):
        raise RuntimeError("Условия не отправлены: текст документа изменился при сборке")
    invite_plain = FORM_TAIL_PLAIN.format(url=LEAD_FORM_URL) if invite_now else ""
    invite_html = FORM_TAIL.format(url=LEAD_FORM_URL) if invite_now else ""
    plain = message + invite_plain
    html = as_html(message) + invite_html
    # Если документ помещается, а CTA уже нет, отправляем её отдельным сообщением. Это по-прежнему
    # один следующий шаг, зато ссылка гарантированно дошла и только после этого будет отмечена.
    split_invite = bool(invite_now and not _single_message_fits(html, plain))
    if split_invite:
        plain = message
        html = as_html(message)
    if not _single_message_fits(html, plain):
        raise RuntimeError(
            "Условия не отправлены: документ превышает безопасный размер Telegram; "
            "обрезать утверждённый текст запрещено"
        )
    ok, err = send_html(int(telegram_id), html, plain)
    if not ok:
        raise RuntimeError(f"Условия не отправлены: {err}")
    journal(MANAGER_CHANNEL, telegram_id, "out", plain, kind="lead_chat",
            meta={"terms": True, "invited": bool(invite_now and not split_invite),
                  "deal_id": int(deal_id) if deal_id else None})
    # Документ у человека есть: второй раз его не дублируем, вопросы поверх условий идут людям.
    _mark_terms_sent(telegram_id)
    if invite_now and not split_invite:
        _mark_invited(telegram_id)
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
    if split_invite:
        form_plain = invite_plain.lstrip()
        form_html = invite_html.lstrip()
        if not _single_message_fits(form_html, form_plain):
            raise RuntimeError("Анкета не отправлена: CTA превышает безопасный размер Telegram")
        ok, err = send_html(int(telegram_id), form_html, form_plain)
        if not ok:
            raise RuntimeError(f"Анкета не отправлена после условий: {err}")
        journal(MANAGER_CHANNEL, telegram_id, "out", form_plain, kind="lead_chat",
                meta={"terms": False, "invited": True,
                      "deal_id": int(deal_id) if deal_id else None})
        _mark_invited(telegram_id)
    log.info("условия отправлены клиенту %s (сделка %s), %s символов",
             telegram_id, deal_id, len(body))
    return {"sent": True, "chars": len(body), "deal_id": deal_id, "invited": invite_now,
            "note": ("Условия ушли клиенту дословно. Следующий шаг — "
                     + ("анкета." if invite_now else
                        "вопросы клиента; после них не теряй переход к реквизитам."))}


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
# --- сверка анкеты: строго из живых полей воронки -----------------------------------------------
# Поля анкеты (значения приходят из CRM-формы «Индивидуальная настройка»). Telegram сюда не
# входит: это контакт, а не данные магазина, которые клиент должен подтвердить.
FORM_FIELDS = [c.strip() for c in os.getenv(
    "CRM_FORM_FIELDS",
    "UF_CRM_1784297026,UF_CRM_1784297137,UF_CRM_1784297181,UF_CRM_1784297221").split(",")
    if c.strip()]
_FIELD_LABELS_CACHE: dict[str, Any] = {"at": 0.0, "labels": {}}


def _deal_field_labels() -> dict[str, str]:
    """Живые названия полей сделки из CRM (кэш 10 мин).

    Владелец 24.07.2026: «если поля изменяются, нужно чтобы и сообщение автоматически
    изменилось». Поэтому названия НЕ зашиты в код: переименовали поле в воронке — сверка
    анкеты меняется сама, без деплоя."""
    now = time.time()
    if _FIELD_LABELS_CACHE["labels"] and now - float(_FIELD_LABELS_CACHE["at"]) < 600:
        return dict(_FIELD_LABELS_CACHE["labels"])
    try:
        from mcp import context_server as cs
        data = cs._crm_call("crm.deal.fields", {}).get("result") or {}
        labels = {}
        for code, meta in data.items():
            label = str((meta or {}).get("formLabel") or (meta or {}).get("listLabel") or "")
            if label:
                labels[code] = label
        if labels:
            _FIELD_LABELS_CACHE.update({"at": now, "labels": labels})
    except Exception:  # noqa: BLE001 — без названий покажем коды, но не упадём
        log.warning("названия полей сделки недоступны", exc_info=True)
    return dict(_FIELD_LABELS_CACHE["labels"])


def _fmt_form_value(value) -> str:
    """Числа анкеты — по-человечески: 5000000 → «5 млн», 1500000 → «1.5 млн»."""
    s = str(value).strip()
    try:
        n = float(s.replace(" ", "").replace(",", "."))
    except ValueError:
        return s
    if n >= 1_000_000:
        return f"{n / 1_000_000:g} млн"
    if n == int(n):
        return f"{int(n):,}".replace(",", " ")
    return s


def anketa_block(deal: dict) -> str:
    """Готовое сообщение сверки анкеты — данные из сделки, названия из воронки.

    Формат задан владельцем 24.07.2026: «Вижу анкету: • <поле> — <значение> … Всё верно?».
    Пустые поля не показываем. Текст собирает код, а не модель: сверка обязана быть
    дословной и одинаковой у сторожа анкеты и у обычного хода."""
    uf = deal.get("custom_fields") or {}
    labels = _deal_field_labels()
    lines = []
    for code in FORM_FIELDS:
        value = uf.get(code)
        if not _filled(value):
            continue
        lines.append(f"• {labels.get(code, code)} — {_fmt_form_value(value)}")
    if not lines:
        return ""
    return "Вижу анкету:\n\n" + "\n".join(lines) + "\n\nВсё верно?"


def _deal_terms_sent(deal: dict | None) -> bool:
    """CRM-доказательство уже отправленных условий, переживающее потерю local JSON."""
    uf = (deal or {}).get("custom_fields") or {}
    has_requisites = _filled(uf.get(CONTRACT_REQUISITES_FIELD))
    has_explicit_mark = bool(TERMS_SENT_FIELD and _filled(uf.get(TERMS_SENT_FIELD)))
    # Новое поле разворачивается не мгновенно: реквизиты в старой/уже идущей сделке остаются
    # более сильным доказательством, что этап условий давно пройден.
    return has_explicit_mark or has_requisites


# ответил — и забыл, что за ответом «давайте ЭДО» должна была идти задача на отправку. Любое
# число вопросов между шагами теперь ничего не ломает: шаг приходит в каждом сообщении.
def funnel_next_step(deal: dict, terms_sent_to_client: bool = False) -> dict:
    """Что агент обязан сделать на текущем шаге сделки.

    `terms_sent_to_client` — документ условий этому человеку уже уходил (отметка в состоянии
    агента). Условия часто отправляются ДО анкеты, ещё когда человек не был лидом, и в полях
    сделки этого следа нет: без флага агент предлагал бы их второй раз."""
    uf = deal.get("custom_fields") or {}
    stage = str(deal.get("stage_id") or deal.get("stage") or "")
    deal_id = deal.get("deal_id") or deal.get("id") or deal.get("ID")
    has_req = _filled(uf.get(CONTRACT_REQUISITES_FIELD))
    has_contract = _filled(uf.get(CONTRACT_NUMBER_FIELD))
    signing = _enum_label(SIGNING_FIELD, uf.get(SIGNING_FIELD))
    # Отметку об отправке условий держим в поле сделки, если оно заведено; пока поля нет —
    # признаком служат уже собранные реквизиты (значит, условия давно позади).
    terms_sent = _deal_terms_sent(deal) or terms_sent_to_client

    # ПОРЯДОК ЭТАПОВ (владелец 25.07.2026): условия → вопросы → анкета. Раньше первой была анкета,
    # и на вопрос «какие условия подключения к иу?» человек получал ссылку на форму вместо ответа —
    # так шли ВСЕ диалоги, где клиент писал первым (выгрузка telegram_bot_messages 24–25.07.2026,
    # диалоги 5195962532, 980579939, 764181402). Анкета переехала за условия: она нужна, чтобы
    # посчитать экономику, а не чтобы пропустить человека к разговору.
    # Признак шага — СОСТОЯНИЕ ДАННЫХ (есть ли анкета, отправлены ли условия), а не формулировка
    # клиента: фразу «вопросов нет» в живых переписках не пишет никто, ловить её нечем.
    if stage in (STAGE_NEW, STAGE_CONTACTED, STAGE_FORM_DONE):
        block = anketa_block(deal)
        if block:
            # Анкета уже заполнена — сверяем данные и уходим на согласование условий.
            return {"step": "Сверка анкеты",
                    "need": "подтверждение данных анкеты",
                    "action": (f"Если сверка ещё не отправлялась (посмотри историю) — отправь "
                               f"клиенту РОВНО это сообщение, слово в слово, ничего не добавляя:"
                               f"\n{block}\n\n"
                               f"Как только он подтвердил данные («всё верно», «да») — переведи "
                               f"сделку {deal_id} на стадию C16:S84294149 (update_crm_deal). "
                               + ("Условия клиент УЖЕ получил дословно из документа — заново их "
                                  "НЕ отправляй и не пересказывай."
                                  if terms_sent else
                                  f"Затем вызови send_terms(deal_id={deal_id}, "
                                  f"telegram_id=<id клиента>, offer_form=False)."))}
        if not terms_sent:
            return {"step": "Ответ и условия",
                    "need": "ничего — условия отправляешь ты",
                    "action": (f"Сначала ответь на то, о чём человек спросил. Затем вызови "
                               f"send_terms(deal_id={deal_id}, telegram_id=<id клиента>, "
                               f"offer_form=False): он отправит условия ДОСЛОВНО из документа и "
                               f"сам спросит, есть ли вопросы. Своими словами условия не "
                               f"пересказывай и цифр из головы не добавляй. Форму на этом шаге "
                               f"НЕ предлагай — человек ещё не прочитал, за что платит. "
                               f"Инструмент сказал, что в документе пометки [ЗАПОЛНИТЬ] — не "
                               f"отправляй ничего, сообщи владельцу через ТАКЖЕ_СПРОСИ_ЛЮДЕЙ.")}
        return {"step": "Вопросы по условиям",
                "need": "вопросы клиента по условиям — и его готовность идти дальше",
                "action": ("Условия клиент уже получил — второй раз их НЕ отправляй. Отвечай на "
                           "вопросы по базе знаний (search_company_knowledge), помня весь "
                           "разговор. Фактов в базе нет — унеси вопрос людям через "
                           "ТАКЖЕ_СПРОСИ_ЛЮДЕЙ, но клиенту всё равно скажи, что уточняешь и "
                           "вернёшься: не молчи, пауза без слов читается как игнор. Когда "
                           "вопросы закончились или человек спросил про подключение, сроки или "
                           "оплату — предложи заполнить анкету, чтобы посчитать экономику по "
                           "его магазину.")}
    if stage == "C16:S84294149" and not terms_sent and not has_req:
        return {"step": "Отправка условий",
                "need": "ничего — условия отправляешь ты",
                "action": (f"Вызови send_terms(deal_id={deal_id}, telegram_id=<id клиента>). "
                           f"Он отправит условия ДОСЛОВНО и спросит про вопросы. Своими словами "
                           f"условия не рассказывай и из головы ничего не добавляй. Инструмент "
                           f"сказал, что в документе пометки [ЗАПОЛНИТЬ] — не отправляй ничего, "
                           f"сообщи владельцу через ТАКЖЕ_СПРОСИ_ЛЮДЕЙ.")}
    # Вопросы по условиям с 25.07.2026 разбираются ДО анкеты (см. блок этапов сверки выше): на
    # согласование условий сделка приходит уже с прочитанными условиями и заполненной анкетой,
    # поэтому следующий шаг здесь — реквизиты. Вопросы, заданные по дороге, закрывает правило
    # «вопросы поверх отправленных условий» в funnel_rules — оно работает на любом этапе.
    if stage == "C16:S84294149" and not has_req:
        return {"step": "Сбор реквизитов",
                "need": "реквизиты организации (название, ИНН, КПП, ОГРН, адрес, р/с, банк, БИК, ФИО директора)",
                "action": (f"Остались вопросы по условиям — сначала ответь на них по базе знаний "
                           f"(search_company_knowledge). Как только реквизиты пришли — запиши их в поле "
                           f"{CONTRACT_REQUISITES_FIELD} сделки {deal_id} и СРАЗУ вызови "
                           f"send_contract(deal_id={deal_id}, telegram_id=<id клиента>). "
                           f"Не хватает части реквизитов — спроси именно недостающее. "
                           f"Если присланное НЕ похоже на реквизиты (случайные цифры, шутка, "
                           f"не тот документ) — не гадай и ничего не записывай в сделку: скажи "
                           f"прямо, что это не реквизиты, и попроси карточку компании. "
                           f"send_contract сам проверяет ИНН/ОГРН контрольными суммами и "
                           f"вернёт список проблем — передай их клиенту дословно.")}
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
        return {"step": "Оплата пришла — начинаем подключение",
                "need": "ничего от клиента: следующий шаг за нами",
                "action": (f"Деньги на счету. Скажи клиенту, что начинаем подключение, и переведи "
                           f"сделку {deal_id} на C16:S84294150 (этап «Подключение»). Сроки от себя "
                           f"не называй — если клиент про них спросит, ТАКЖЕ_СПРОСИ_ЛЮДЕЙ.")}
    # «Рассчёт экономики» — этап ЛЮДЕЙ: агент экономику не считает, инструмента у него нет.
    # Найдено прогоном пути 25.07.2026: этап был без шага, и агент работал по запасному сценарию.
    if stage == "C16:UC_YA6VN0":
        return {"step": "Расчёт экономики — за командой",
                "need": "расчёт от команды (не от клиента)",
                "action": ("Экономику считают люди, ты её НЕ считаешь и цифр от себя не даёшь. "
                           "Клиент спрашивает про расчёт — ТАКЖЕ_СПРОСИ_ЛЮДЕЙ и скажи, что "
                           "передал вопрос команде. Расчёт прислали в группе — передай клиенту "
                           "дословно, ничего не пересчитывая. Стадию сам не двигай.")}
    if stage == "C16:S84294150":
        return {"step": "Подключение кабинета",
                "need": "то, что нужно технически для подключения (доступы, данные кабинета)",
                "action": (f"Направь клиенту инструкции и запроси необходимое по базе знаний. "
                           f"Технических деталей от себя не придумывай: чего нет в базе — "
                           f"ТАКЖЕ_СПРОСИ_ЛЮДЕЙ. Когда подключение сделано — сделка {deal_id} на "
                           f"C16:CONNECTED.")}
    # Клиент подключён — продавать больше нечего, но бросать его нельзя: это уже сопровождение.
    # Этап был без шага, и агент работал по запасному сценарию (найдено страницей воронки в
    # кабинете 25.07.2026 — ровно то, для чего она и делалась).
    if stage == "C16:CONNECTED":
        return {"step": "Подключён — сопровождение",
                "need": "вопросы клиента по работе кабинета",
                "action": ("Клиент уже подключён. Отвечай на вопросы по работе кабинета по базе "
                           "знаний, ничего не продавай и новых обещаний не давай. Просит то, чего "
                           "в базе нет (правки в кабинете, выплаты, спорные ситуации) — "
                           "ТАКЖЕ_СПРОСИ_ЛЮДЕЙ: это сопровождение живыми людьми. Стадию не двигай.")}
    # Закрывающие этапы. Продавать тут нечего, но и молчать нельзя: человек пишет живому аккаунту.
    # Найдено прогоном пути 25.07.2026 — все четыре работали по запасному сценарию.
    if stage == "C16:WON":
        return {"step": "Сделка успешна — сопровождение",
                "need": "вопросы клиента по работе",
                "action": ("Сделка закрыта успешно. Отвечай по базе знаний, ничего не продавай и "
                           "новых обещаний не давай. Чего нет в базе — ТАКЖЕ_СПРОСИ_ЛЮДЕЙ. "
                           "Стадию не двигай.")}
    if stage in ("C16:NOT_FIT", "C16:LOSE"):
        return {"step": "Клиент не пошёл дальше — не дожимаем",
                "need": "ничего: инициатива за клиентом",
                "action": ("Клиент отказался или не подошёл. НЕ дожимай, не напоминай о себе и "
                           "ничего не предлагай сам. Написал сам — ответь вежливо и коротко по "
                           "базе знаний. Говорит, что снова интересно — не возвращай сделку в "
                           "работу сам: ТАКЖЕ_СПРОСИ_ЛЮДЕЙ, решают люди.")}
    if stage == "C16:APOLOGY":
        return {"step": "Разбор причины отказа — внутренний этап",
                "need": "ничего от клиента",
                "action": ("Это внутренний разбор, клиенту по своей инициативе не пишем. Написал "
                           "сам — ответь вежливо по базе знаний и передай вопрос людям через "
                           "ТАКЖЕ_СПРОСИ_ЛЮДЕЙ. Стадию не двигай.")}
    # Этап, для которого шага не написано. Так было с «Анкета заполнена» 24.07.2026: заглушка
    # «ждёшь: — / веди разговор по маршруту» не говорила агенту НИЧЕГО, и он вставал в тупик
    # посреди живого разговора. Молчать нельзя: даём осмысленное поведение менеджера и кричим
    # в журнал — новый этап в воронке обязан получить свой шаг в коде.
    log.warning("у этапа %s нет шага в воронке — агент работает по запасному сценарию", stage)
    return {"step": f"Стадия {stage} (шаг не описан)",
            "need": "вопросы клиента и его готовность идти дальше",
            "action": ("Шага для этой стадии в маршруте нет. Не молчи и не выдумывай новых "
                       "обещаний: ответь на то, что спросил клиент, спроси, остались ли у него "
                       "вопросы, и назови ближайший разумный следующий шаг по разговору. "
                       "Стадию сам не двигай. Если не понимаешь, что делать дальше — "
                       "ТАКЖЕ_СПРОСИ_ЛЮДЕЙ.")}


def funnel_step_block(deal_id: int, telegram_id=None) -> str:
    """Текущий шаг воронки текстом — уходит в промпт КАЖДОГО сообщения."""
    try:
        from mcp import context_server as cs
        deal = cs.TOOLS["get_crm_deal"]["handler"]({"deal_id": int(deal_id)})
        deal = deal.get("deal") or deal
    except Exception:  # noqa: BLE001 — без шага агент ответит хуже, но ответит
        log.warning("шаг воронки для сделки %s не определён", deal_id, exc_info=True)
        return ""
    st = funnel_next_step(deal, terms_sent_to_client=bool(
        telegram_id and _terms_already_sent(telegram_id)))
    # Владелец мог настроить текст шага в кабинете («Работа с воронками») — он главнее кода:
    # формулировки для своих клиентов он знает лучше. Условия правил при этом не меняются.
    stage = str(deal.get("stage_id") or deal.get("stage") or "")
    custom = funnel_scenario.step_override(_db, stage)
    if custom:
        st = {**st, **custom}
        log.debug("шаг этапа %s взят из настроек кабинета", stage)
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
    # 23.07.2026, ожидание задачи 2018: модель передала telegram_id=18 — Bitrix-id сотрудника,
    # а не Telegram-id клиента. Доставка билась в PEER_ID_INVALID каждые 20 секунд. Telegram-id
    # людей — большие числа; маленькое значение здесь всегда чужой идентификатор.
    if to_int_safe(telegram_id) is None or int(telegram_id) < 100000:
        raise ValueError(f"telegram_id={telegram_id} не похож на Telegram id клиента (это "
                         f"Bitrix-id?). Возьми числовой id из диалога или поля сделки.")
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
            if "PEER_ID_INVALID" in str(err):
                # Адрес недоставим в принципе (в ожидании чужой id, не Telegram) — повторять
                # каждые 20 секунд бессмысленно, а владелец получает бесконечный шум в логе.
                _cancel_watch(w["id"], f"адрес недоставим (PEER_ID_INVALID: "
                                       f"telegram_id={w['telegram_id']})")
                log.warning("ожидание задачи %s снято: telegram_id=%s недоставим",
                            w["bitrix_task_id"], w["telegram_id"])
                continue
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


def _name_for_uid(uid) -> str:
    """Имя человека из справочника контактов — чтобы обращаться по имени, а не «клиент»."""
    target = to_int_safe(uid)
    for entry in contacts().values():
        if isinstance(entry, dict) and to_int_safe(entry.get("id")) == target:
            return str(entry.get("name") or entry.get("first_name") or "")
    return ""


def _first_contact(uid) -> bool:
    """Агент ещё ни разу не писал этому человеку — значит надо поздороваться.

    Отметка живёт в общем журнале, поэтому видит и сообщения инструментов из другого процесса."""
    return _dialog_out_watermark(uid) == 0


def _username_for_uid(uid) -> str:
    """@username из справочника контактов по числовому id."""
    target = to_int_safe(uid)
    for entry in contacts().values():
        if isinstance(entry, dict) and to_int_safe(entry.get("id")) == target:
            return str(entry.get("username") or "")
    return ""


def _deals_for_username(uname: str) -> list[dict]:
    """Все сделки воронки с этим @username, от старой к новой.

    Список берём из list_crm_lead_contacts: он отдаёт ОТДЕЛЬНУЮ строку на каждую сделку, а
    list_crm_deals пользовательские поля не возвращает вовсе — из-за этого склейка сначала
    не находила дубль (проверено на проде 24.07.2026). Поля читаем точечно по каждой сделке."""
    target = _norm_username(uname)
    if not target:
        return []
    try:
        rows = mcp_call("list_crm_lead_contacts", {}).get("contacts") or []
    except Exception:  # noqa: BLE001
        log.warning("не удалось прочитать лидов воронки", exc_info=True)
        return []
    ids = sorted({int(r["deal_id"]) for r in rows if r.get("deal_id")
                  and _norm_username(str(r.get("username") or "")) == target})
    out: list[dict] = []
    for did in ids:
        try:
            deal = mcp_call("get_crm_deal", {"deal_id": did})
            deal = deal.get("deal") or deal
            deal["deal_id"] = did       # id несём сами: ответ инструмента его может не содержать
            out.append(deal)
        except Exception:  # noqa: BLE001
            log.warning("сделка %s недоступна при склейке", did, exc_info=True)
    return out


def merge_form_duplicate(uname: str) -> dict:
    """Склеить сделку от CRM-формы с той, что агент завёл при первом сообщении.

    Владелец 24.07.2026: анкета создаёт СВОЮ сделку, а у нас уже есть своя — «не надо плодить».
    Держим САМУЮ СТАРУЮ (её вёл агент с первого сообщения), переносим в неё заполненные поля
    анкеты и удаляем дубль."""
    deals = _deals_for_username(uname)
    if len(deals) < 2:
        return {"merged": False, "reason": "дублей нет"}
    keep, dup = deals[0], deals[-1]
    keep_id = int(keep.get("deal_id") or keep.get("id"))
    dup_id = int(dup.get("deal_id") or dup.get("id"))
    moved = {k: v for k, v in (dup.get("custom_fields") or {}).items()
             if _filled(v) and not _filled((keep.get("custom_fields") or {}).get(k))}
    try:
        mcp_call("update_crm_deal", {
            "deal_id": keep_id, "stage": STAGE_FORM_DONE,
            **({"custom_fields": moved} if moved else {}),
            "comments": f"Анкета заполнена — данные перенесены из сделки {dup_id}, дубль удалён."})
        mcp_call("delete_crm_deal", {"deal_id": dup_id, "confirm": True})
    except Exception as exc:  # noqa: BLE001
        log.warning("склейка сделок @%s не удалась: %s", uname, str(exc)[:200])
        return {"merged": False, "error": str(exc)[:200]}
    _LEADS_CACHE.setdefault("map", {})[_norm_username(uname)] = keep_id
    log.info("склеены сделки @%s: оставлена %s, удалён дубль %s, перенесено полей %d",
             uname, keep_id, dup_id, len(moved))
    return {"merged": True, "kept": keep_id, "deleted": dup_id, "fields": len(moved)}


def _deal_for_watch(deal_id: int) -> dict:
    """Сделка для сторожа анкеты (тем же инструментом, что и ходы агента)."""
    from mcp import context_server as cs
    deal = cs.TOOLS["get_crm_deal"]["handler"]({"deal_id": int(deal_id)})
    return deal.get("deal") or deal


def _facts_for_turn(author: dict, text: str, deal_id: int | None = None, *,
                    wants_terms: bool = False, deal: dict | None = None) -> funnel_rules.Facts:
    """Снимок состояния на один ход: собирается ОДИН раз, дальше решения читают только его.

    Раньше каждая проверка сама лазила в CRM, состояние и журнал — и разные части одного хода
    могли видеть разную картину."""
    uid = to_int_safe(author.get("id"))
    state = load_state()
    stage, anketa = "", ""
    deal_status_unknown = False
    if deal is None and deal_id:
        try:
            deal = _deal_for_watch(deal_id)
        except Exception:  # noqa: BLE001 — без сделки решение всё равно нужно принять
            log.warning("факты хода: сделка %s недоступна", deal_id, exc_info=True)
            deal = None
            deal_status_unknown = True
    if deal:
        stage = str(deal.get("stage_id") or deal.get("stage") or "")
        anketa = anketa_block(deal)
    fingerprint = _anketa_fingerprint(anketa) if anketa else ""
    return funnel_rules.Facts(
        uid=uid,
        name=(author.get("first_name") or _name_for_uid(uid) or "").strip(),
        username=str(author.get("username") or ""),
        text=text or "",
        deal_id=deal_id,
        stage=stage,
        anketa=anketa,
        anketa_fingerprint=fingerprint,
        anketa_seen=str((state.get("anketa_seen") or {}).get(str(uid)) or ""),
        legacy_surveyed=(str(uid) not in (state.get("anketa_seen") or {})
                         and str((state.get("form_surveyed") or {}).get(str(uid))) == str(deal_id)),
        terms_sent=bool(
            (state.get("terms_sent") or {}).get(str(uid))
            or _deal_terms_sent(deal)
        ),
        deal_status_unknown=deal_status_unknown,
        first_contact=_first_contact(uid),
        wants_terms=wants_terms,
    )


def _anketa_fingerprint(block: str) -> str:
    """Отпечаток содержимого анкеты — по нему видно, сверяли эти данные или ещё нет."""
    return hashlib.sha1(block.encode("utf-8")).hexdigest()[:12]


def _check_new_forms() -> None:
    """Сторож анкеты: клиент заполнил форму — агент НАЧИНАЕТ сверку сам, не ждёт сообщения.

    Владелец 24.07.2026: «агент должен не просто ждать пока человек напишет, а сам
    отслеживать, появилась ли анкета».

    Признак «анкета появилась» — САМИ ДАННЫЕ анкеты в сделке, а не факт её появления. Раньше
    сторож срабатывал на новую сделку, потому что сделку создавала только CRM-форма. С 24.07.2026
    сделку заводит агент с первого сообщения, и тот признак сломался: сделка уже есть, агент по
    ней говорит, склейка переносит анкету в неё же — клиент не получал сверку, пока не писал
    «Заполнил» сам. Теперь помним отпечаток сверенных данных (state.anketa_seen[uid]):
    перезаполнил анкету — отпечаток другой — сверяем заново."""
    state = load_state()
    invited = state.get("invited") or {}
    if not invited:
        return
    for uid_str in list(invited):
        username = _username_for_uid(uid_str)
        if not username:
            continue
        deal_id = lead_deal_for_username(username)
        if not deal_id:
            continue
        lock = dialog_lock(to_int_safe(uid_str))
        if not lock.acquire(blocking=False):
            continue        # идёт ход с этим человеком — он сам увидит шаг воронки
        try:
            # Анкета создала свою сделку, а агент уже вёл свою — склеиваем в одну и
            # ставим «Анкета заполнена» (владелец, 24.07.2026).
            merged = merge_form_duplicate(username)
            if merged.get("merged"):
                deal_id = merged["kept"]
            deal = _deal_for_watch(deal_id)
            author = {"id": to_int_safe(uid_str), "username": username}
            facts = _facts_for_turn(author, "", deal_id, deal=deal)
            decision = funnel_rules.decide(facts, slot="watch")
            block, fingerprint = facts.anketa, facts.anketa_fingerprint
            if decision.action != funnel_rules.SEND_SURVEY:
                log.debug("сторож анкеты %s: %s", uid_str, funnel_rules.explain(decision))
                decision_log.record(_db, decision, slot="watch", outcome="ничего не отправлено")
                if block:
                    # Данные есть, но сверять их не надо (уже сверяли / сделка ушла дальше) —
                    # запоминаем, чтобы не возвращаться к ним каждый проход.
                    _remember_anketa(uid_str, deal_id, fingerprint)
                continue
            stage = facts.stage
            # Анкета есть — этап обязан это показывать. Склейка ставит его сама, но когда дубля
            # не было (сделку создала только форма), этап так и оставался «Связались».
            if stage in (STAGE_NEW, STAGE_CONTACTED):
                _move_deal_stage(deal_id, STAGE_FORM_DONE, "Клиент заполнил анкету.")
            # Сверка — тоже сообщение живого человека: с приветствием при первом контакте и
            # подводкой. Раньше клиент получал голое «Вижу анкету:» (диалог 256942600).
            block = client_message.compose(block, name=_name_for_uid(uid_str),
                                           greet=_first_contact(uid_str),
                                           lead_in=client_message.LEAD_IN_ANKETA)
            ok, err = send_html(to_int_safe(uid_str), as_html(block), block)
            if not ok:
                log.warning("сверка анкеты не доставлена %s: %s", uid_str, err[:150])
                continue
            journal(MANAGER_CHANNEL, uid_str, "out", block, kind="lead_chat",
                    meta={"deal_id": deal_id, "anketa": True})
            _remember_anketa(uid_str, deal_id, fingerprint)
            decision_log.record(_db, decision, slot="watch", outcome="сверка анкеты отправлена")
            log.info("анкета сделки %s замечена — сверка отправлена клиенту %s",
                     deal_id, uid_str)
        except Exception:  # noqa: BLE001 — один человек не должен ронять весь проход
            log.warning("сторож анкеты: сбой на %s", uid_str, exc_info=True)
        finally:
            lock.release()


def _remember_anketa(uid_str: str, deal_id: int, fingerprint: str) -> None:
    """Запомнить сверенные данные анкеты этого человека."""
    with _state_lock:
        fresh = load_state()
        fresh.setdefault("anketa_seen", {})[uid_str] = fingerprint
        fresh.setdefault("form_surveyed", {})[uid_str] = deal_id   # для совместимости и отчётов
        save_state(fresh)


_FORM_WATCH_EVERY_TICKS = max(1, int(os.getenv("TG_FORM_WATCH_EVERY_TICKS", "3") or 3))


def _task_watch_loop() -> None:
    """Сторож ожиданий: сотрудник закрыл задачу — клиент узнаёт в пределах интервала.

    Живёт отдельным потоком в службе tg-агента: Битрикс не шлёт сюда событий о закрытии
    задач, а агент отвечает только на входящие — без сторожа механизм ожиданий не работал
    вовсе (23.07.2026 владелец закрыл задачу, клиенту не ушло ничего). Когда ожиданий нет,
    Битрикс не дёргается: проход обходится одним запросом к своей БД.

    Тем же потоком, раз в несколько тиков, работает сторож анкеты (_check_new_forms)."""
    tick = 0
    while True:
        try:
            res = check_finished_tasks()
            if res.get("notified") or res.get("failed"):
                log.info("сторож задач: %s", res)
        except Exception:  # noqa: BLE001 — сторож не имеет права умереть от одного сбоя
            log.warning("сторож задач: проход не удался", exc_info=True)
        tick += 1
        if tick % _FORM_WATCH_EVERY_TICKS == 0:
            try:
                _check_new_forms()
            except Exception:  # noqa: BLE001
                log.warning("сторож анкеты: проход не удался", exc_info=True)
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
    """Статус задачи голым HTTP к вебхуку Битрикса.

    Никаких импортов bitrix/context_server: в процессе tg-агента импорт bitrix циклится
    («partially initialized module» — поймано сторожем на проде 23.07.2026), а context_server
    запускает живые планировщики. На этом боксе BITRIX_WEBHOOK_BASE пуст, рабочий вебхук —
    B24_TESTBOT_WEBHOOK_BASE (тот же, которым ходит _b24_webhook_call в MCP)."""
    base = ""
    for env_name in ("BITRIX_WEBHOOK_BASE", "B24_TESTBOT_WEBHOOK_BASE"):
        base = (os.getenv(env_name) or "").strip().rstrip("/")
        if base:
            break
    if not base:
        raise RuntimeError("ни BITRIX_WEBHOOK_BASE, ни B24_TESTBOT_WEBHOOK_BASE не заданы")
    resp = requests.get(f"{base}/tasks.task.get.json", params={"taskId": int(task_id)},
                        timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"tasks.task.get: HTTP {resp.status_code} {resp.text[:200]}")
    data = resp.json() if (resp.text or "").strip() else {}
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(f"tasks.task.get: {data.get('error')} "
                           f"{data.get('error_description') or ''}".strip())
    task = (((data or {}).get("result") or {}).get("task")) or {}
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
    if funnel_workspace_enabled():
        return False
    return str(os.getenv("TG_BUSINESS_AUTOREPLY", "")).strip().lower() in {"1", "true", "yes"}


# --- белый список: отвечаем только лидам из воронки -------------------------------------------
# Аккаунт @AlberyAIManager живой: туда пишут не только лиды, но и поставщики, и знакомые.
# Автоответ разрешён ТОЛЬКО тем, чей Telegram указан в сделке воронки «Партнёрская программа
# WB — индивидуальные условия» (требование владельца 22.07.2026).
CRM_LEAD_CATEGORY_ID = int(os.getenv("CRM_LEAD_CATEGORY_ID", "16") or 16)
CRM_TELEGRAM_FIELD = os.getenv("CRM_TELEGRAM_FIELD", "UF_CRM_1784296997").strip()

# Первые этапы воронки (владелец, 24.07.2026): написал про ИУ → «Новый лид», ответили →
# «Связались», заполнил анкету → «Анкета заполнена». Дальше — согласование условий и т.д.
# Заданы в funnel_rules — там же, где правила, которые на них смотрят: список этапов сверки и
# сами константы однажды разошлись, и сторож анкеты замолчал.
STAGE_NEW = funnel_rules.STAGE_NEW
STAGE_CONTACTED = funnel_rules.STAGE_CONTACTED
STAGE_FORM_DONE = funnel_rules.STAGE_FORM_DONE


def _iu_intent(texts: list[str]) -> bool:
    """Человек интересуется подключением к ИУ, а не просто болтает?"""
    return funnel_rules.Facts(text=" ".join(texts or [])).iu_intent


def _open_lead_deal(username: str, telegram_id, name: str = "") -> int | None:
    """Завести сделку на первом этапе воронки и запомнить её за этим @username."""
    uname = _norm_username(username)
    if not uname:
        return None      # без username сделку не с чем связать — писать всё равно можем
    title = f"Лид Telegram @{uname}" + (f" — {name}" if name else "")
    try:
        res = mcp_call("create_crm_deal", {
            "title": title, "category_id": CRM_LEAD_CATEGORY_ID, "stage": STAGE_NEW,
            "custom_fields": {CRM_TELEGRAM_FIELD: uname},
            "comments": f"Написал в Telegram (id {telegram_id}). Сделка заведена агентом.",
        })
    except Exception:  # noqa: BLE001 — без сделки разговор всё равно продолжается
        log.warning("не удалось завести сделку для @%s", uname, exc_info=True)
        return None
    deal_id = to_int_safe(res.get("deal_id") or res.get("id") or res.get("ID"))
    if deal_id:
        # Кэш лидов должен сразу знать про новую сделку, иначе следующий ход снова сочтёт
        # человека незнакомцем и заведёт вторую.
        _LEADS_CACHE.setdefault("map", {})[uname] = int(deal_id)
        log.info("заведена сделка %s на этапе «Новый лид» для @%s", deal_id, uname)
    return deal_id


def _move_deal_stage(deal_id, stage: str, comment: str = "") -> None:
    """Передвинуть сделку по воронке. Тихо: движение стадии не должно ломать ответ клиенту."""
    try:
        mcp_call("update_crm_deal", {"deal_id": int(deal_id), "stage": stage,
                                     **({"comments": comment} if comment else {})})
    except Exception:  # noqa: BLE001
        log.warning("сделка %s не сдвинулась на %s", deal_id, stage, exc_info=True)
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


def _find_lead(uname: str, leads: dict[str, int]) -> int | None:
    if uname in leads:
        return leads[uname]
    squashed = _squash(uname)
    for known, deal_id in leads.items():
        if _squash(known) == squashed:
            return deal_id
    return None


def lead_deal_for_username(username: str) -> int | None:
    """Номер сделки, если этот человек — лид воронки. Иначе None (значит не отвечаем).

    Промах перепроверяется свежим чтением CRM: клиент заполняет анкету, сделка появляется
    сию секунду, а кэш живёт 5 минут — 23.07.2026 агент ещё несколько минут говорил с уже
    существующим лидом как с незнакомцем, и ходы прыгали между ветками (записи 211–221).
    Перечитываем не чаще раза в минуту, чтобы сообщения поставщиков не долбили CRM."""
    uname = _norm_username(username)
    if not uname:
        return None
    found = _find_lead(uname, crm_lead_usernames())
    if found is not None:
        return found
    if time.time() - float(_LEADS_CACHE.get("at") or 0) > 60:
        return _find_lead(uname, crm_lead_usernames(force=True))
    return None


# --- разговор с незнакомцем ---------------------------------------------------------------------
# Написал человек, которого нет в воронке. Он ведёт себя как живой человек: здоровается, о чём-то
# спрашивает — значит и отвечать надо как консультант, а не выдавать всем одну и ту же простыню.
# Анкета — отдельный разрешённый следующий шаг: её можно добавить только после явной готовности
# клиента, а не к «первому ответу» вообще. Чего в утверждённых данных нет — агент не придумывает.

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
    "Чтобы перейти к подключению, заполните короткую анкету — это займёт пару минут:\n"
    '<a href="{url}">Заполнить анкету</a>'
)
# Тот же хвост без разметки — на случай, когда Telegram отверг HTML: адрес должен остаться
# видимым, иначе «Заполнить анкету» превратится в слова без ссылки.
FORM_TAIL_PLAIN = FORM_TAIL.replace('<a href="{url}">Заполнить анкету</a>', "{url}")


def _without_model_form_link(answer: str) -> str:
    """Убрать форму из модельного текста: ссылкой управляет только policy/assembler.

    Иначе модель могла вставить анкету на приветствие, а код уже не имел возможности это
    запретить. При разрешённом намерении assembler добавит одну каноническую ссылку обратно."""
    value = str(answer or "")
    base = LEAD_FORM_URL.rstrip("/")
    if not base:
        return value.strip()
    target = re.escape(base) + r"/?"
    value = re.sub(r"\[[^\]\n]{1,100}\]\(\s*" + target + r"\s*\)", "", value,
                   flags=re.I)
    value = re.sub(target, "", value, flags=re.I)
    value = re.sub(r"[ \t]+([,.;:!?])", r"\1", value)
    value = re.sub(r"[ \t]{2,}", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip(" \n—–-,:;")


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
# Третий маркер: человек спросил про условия. Пересказ модели запрещён — условия уходят
# ДОСЛОВНО из документа владельца (24.07.2026: «дублировать условия из файла один в один»).
# До этого модель сочиняла их сама, и цифры гуляли от диалога к диалогу.
TERMS_REQUEST_MARKER = "ПОКАЖИ_УСЛОВИЯ"

# Документ условий уходит клиенту ОДИН раз. 24.07.2026 (диалог 764181402) клиент, уже получивший
# условия, спросил «какой дрр нужно держать и как происходит управление?» — слов «ДРР»,
# «управление», «реклама» в документе нет, — и агент второй раз выслал весь документ: на вопрос
# не ответил и людям его не передал. Решение владельца: документ один раз; вопрос, ответа на
# который в документе нет, уносим людям И пишем клиенту одну строку, чтобы он не сидел в тишине
# (тот же случай, что был с Георгием: молчание оставляет клиента без понимания, ответят ли ему).
TERMS_ASK_HUMAN_REPLY = "Уточню это у команды и вернусь с ответом."
# Часть вопросов агент ответил сам, часть ушла людям — клиент должен об этом знать честно.
TERMS_PENDING_NOTE = "По остальному уточню у команды и вернусь с ответом."
# Разбор текста (вопрос ли это, просьба выслать заново, интерес к ИУ) живёт в funnel_rules —
# там же, где правила, которые на него смотрят.


def _looks_like_question(text: str) -> bool:
    """Клиент о чём-то спрашивает, а не просто подтверждает?"""
    return bool(funnel_rules.QUESTION_RE.search(text or ""))


def _terms_already_sent(user_id: int) -> bool:
    """Уходил ли этому человеку документ условий (в любой из ветвей — лида или незнакомца)."""
    return bool((load_state().get("terms_sent") or {}).get(str(user_id)))


def _mark_terms_sent(user_id: int) -> None:
    """Отметить фактически доставленный документ, чтобы второй раз его не дублировать."""
    with _state_lock:
        state = load_state()
        state.setdefault("terms_sent", {})[str(user_id)] = datetime.now(timezone.utc).isoformat()
        save_state(state)


def _wants_terms_again(text: str) -> bool:
    """Человек просит именно ПРИСЛАТЬ условия заново, а не спрашивает что-то поверх них."""
    return bool(funnel_rules.RESEND_RE.search(text or ""))




def _ask_model(prompt: str) -> str:
    """Один вопрос модели без инструментов — для разбора ответов по источникам."""
    return hermes_answer(prompt, f"answering-{uuid.uuid4().hex[:8]}")


# Пауза перед повтором после сбоя провайдера модели: 500/503 обычно живут секунды.
_MODEL_RETRY_PAUSE_S = float(os.getenv("TG_MODEL_RETRY_PAUSE_S", "8") or 8)






def _terms_question_to_humans(author: dict, client_text: str,
                              texts_to_journal: list[str] | None = None,
                              meta: dict | None = None) -> None:
    """Условия уже высылали, человек спрашивает дальше — вопрос людям, клиенту одна строка."""
    uid = author.get("id")
    escalate_to_human(author,
                      f"условия клиент уже получил, в документе ответа на это нет; ему написано "
                      f"«{TERMS_ASK_HUMAN_REPLY}». Нужен ваш ответ на: {client_text[:150]}",
                      client_text, answered=True)
    reply = client_message.compose(TERMS_ASK_HUMAN_REPLY, name=_name_for_uid(uid),
                                   greet=_first_contact(uid))
    ok, err = send_html(uid, as_html(reply), reply)
    for t in texts_to_journal or []:
        journal(MANAGER_CHANNEL, uid, "in", t, kind="lead_chat", user=author)
    journal(MANAGER_CHANNEL, uid, "out",
            reply if ok else f"{reply}\n\n[не доставлено: {err}]",
            kind="lead_chat", user=author, status="ok" if ok else "error",
            meta={"escalated": True, **(meta or {})})
    log.info("вопрос поверх уже отправленных условий от %s унесён людям", uid)

# Правила для разговора с тем, кого ещё нет в воронке. У лида шаг задаёт сделка, а у
# незнакомца сценария не было вовсе — отсюда импровизация и обещания, которых агент не
# выполнит (клиент с оборотом 200 млн, диалог 764181402: «посмотрим экономику по артикулу»).
STRANGER_RULES = (
    "Человека ещё нет в воронке. Твоя цель — понять запрос и помочь, а не любой ценой отправить "
    "анкету.\n"
    "- Сначала ответь по существу на всё, что человек спросил. Приветствие, off-topic и простой "
    "вопрос об условиях сами по себе НЕ разрешают анкету.\n"
    f"- Если ТЕКУЩИЙ вопрос действительно про условия ИУ, цены ИУ, комиссию ИУ или тариф ИУ — верни "
    f"РОВНО одну строку: {TERMS_REQUEST_MARKER}. Больше НИЧЕГО не пиши и условия своими "
    f"словами не рассказывай: система проверит намерение и отправит утверждённый текст. Слова "
    "«цена», «условия» или «доставка» в постороннем вопросе не являются запросом условий ИУ.\n"
    "- Если человек явно решил подключаться, прямо попросил анкету или подтвердил твоё предложение "
    "её заполнить, система сама добавит одну ссылку. В таком ходе не задавай встречный вопрос и "
    "не добавляй другой следующий шаг.\n"
    "- НЕ обещай того, чего не сделаешь: не предлагай посчитать экономику, не проси артикул "
    "или ссылку на товар, не называй сроки и суммы от себя. Ты не считаешь экономику и не "
    "анализируешь товары — такого инструмента у тебя нет.\n"
    "- Данные о магазине человек указывает в анкете — не выспрашивай их по одному в чате."
)

# Правила живого тона. Родились из разбора диалога 23.07.2026: агент начинал КАЖДОЕ сообщение
# с «Александр, …» и переспрашивал уже отвеченное — выглядело как автоответчик.
STYLE_RULES = (
    "Стиль (обязательно):\n"
    "- Ответ клиента важнее этапа CRM: сначала прямо ответь на его вопрос или признай, что нужна "
    "помощь человека, и только затем переходи к процессу. Стадия — контекст, а не реплика скрипта.\n"
    "- В одном сообщении максимум один следующий шаг: ИЛИ один вопрос, ИЛИ один CTA. Если система "
    "добавит анкету, не задавай рядом вопрос и не предлагай ещё одно действие.\n"
    "- НЕ начинай сообщение с имени клиента и не вставляй имя в каждый ответ. По имени можно "
    "обратиться изредка, в важный момент; в остальном просто разговаривай, как живой менеджер.\n"
    "- Если клиент прислал несколько сообщений подряд — прочитай все и ответь на всё ОДНИМ "
    "сообщением, ничего не пропуская.\n"
    "- Смотри историю: не задавай вопрос, на который клиент уже ответил, и не повторяй свой "
    "прошлый вопрос другими словами. Данные из анкеты, уже присланные данные и документы не "
    "запрашивай повторно.\n"
    "- Не принимай данные на веру: если присланное не похоже на то, что ты просил (случайные "
    "цифры, обрывок, не тот документ) — скажи об этом прямо и попроси нормальный вариант. "
    "Не додумывай за клиента.\n"
    "- Ссылку на анкету и предложение её заполнить НЕ вставляй сам: policy добавляет одну CTA "
    "только когда клиент явно готов подключаться или прямо попросил анкету.\n"
    "- Спрашивают про условия, цены, комиссию, тарифы, стоимость, «что за договор» — верни "
    "РОВНО одну строку: ПОКАЖИ_УСЛОВИЯ. Больше НИЧЕГО не пиши и условия своими словами НЕ "
    "пересказывай, даже кратко: система отправит их дословно из документа владельца. Своя "
    "выжимка условий — грубая ошибка (случай 24.07.2026).\n"
    "- Не спрашивай в чате то, что человек указывает в анкете (магазин, категория товара, "
    "обороты). Голое «ок»/«хорошо» не считай согласием на анкету без предыдущего явного вопроса "
    "о ней.\n"
    "- Коротко и тепло, без канцелярита."
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


IU_CLARIFY_REPLY = (
    "Подскажите, ваш вопрос относится к индивидуальным условиям для продавцов Wildberries?"
)


def _deal_has_form(deal_id: int | None) -> bool | None:
    """`True`/`False` при известном CRM-состоянии, `None` при недоступной проверке.

    Для CTA `None` трактуется fail-closed: лучше сохранить ответ без формы, чем повторно
    прислать её человеку, который уже заполнил данные."""
    if not deal_id:
        return False
    try:
        return bool(anketa_block(_deal_for_watch(int(deal_id))))
    except Exception:  # noqa: BLE001 — недоступность CRM не должна ломать обычный ответ
        log.warning("не удалось проверить анкету сделки %s перед CTA", deal_id, exc_info=True)
        return None






IU_AGENT_NAME = os.getenv("IU_AGENT_NAME", "Агент по работе с ИУ").strip()


def escalate_to_human(author: dict, question: str, client_text: str,
                      answered: bool = False) -> None:
    """Принести вопрос лида живым людям в группу Битрикса «Работа с ИУ».

    Ответ сотрудника в той же группе агент передаёт клиенту сам — поэтому в карточке есть
    telegram id: без него передать ответ будет некому.

    Карточка КОРОТКАЯ (владелец, 24.07.2026): суть вопроса, клиент, чего нет в базе — и всё.
    Простыня «О чём говорили в чате» убрана: она превращала уведомления во флуд, а переписку
    агент группы и так достаёт инструментом get_telegram_dialog."""
    uid = author.get("id")
    uname = author.get("username") or ""
    name = " ".join(x for x in (author.get("first_name"), author.get("last_name")) if x).strip()
    # Оформление — по стандарту компании: блоки через пустую строку, заголовки [b]…[/b].
    # Клиент в этот момент СИДИТ БЕЗ ОТВЕТА, поэтому карточка начинается со срочности: сотрудник
    # должен понять это с первой строки, а не вычитать из середины.
    card = (("[b]Клиенту отвечено по существу, но нужна конкретика от вас[/b]\n"
             if answered else
             "[b]⚠️ Клиент ждёт ответа — ему пока НИЧЕГО не отвечено[/b]\n")
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


def reply_to_stranger(author: dict, texts: list[str] | str) -> bool:
    """Ответ человеку, которого ещё нет в воронке — через общий конвейер ИУ.

    Сделка заводится ПОСЛЕ хода и только по факту: если разговор действительно оказался про наш
    продукт (нашлась карточка знаний или агент выслал условия/анкету). Раньше решение принимал
    реестр regex-правил по тексту сообщения, и «сколько стоит доставка?» заводило сделку."""
    if not lead_invite_enabled():
        return False
    texts = [texts] if isinstance(texts, str) else [t for t in texts if (t or "").strip()]
    if not texts:
        return False

    import iu_runtime

    author_id = author.get("id")
    ok, outcome = iu_runtime.run_turn(author, texts, deal_id=None)
    if not iu_runtime.about_our_product(outcome):
        return ok

    new_deal = _open_lead_deal(author.get("username") or "", author_id,
                               (author.get("first_name") or "").strip())
    if new_deal:
        log.info("незнакомец %s: разговор про ИУ — заведена сделка %s", author_id, new_deal)
        move = iu_runtime.stage_after_turn(new_deal, author_id, outcome)
        if move:
            _move_deal_stage(new_deal, move, "Этап подтверждён фактами переписки.")
    return ok


def _business_owner_id(business_connection_id: str = "") -> int | None:
    """Числовой id владельца конкретного Telegram Business подключения.

    Без аргумента сохраняется legacy-поведение для внутренних уведомлений.
    Входящие workspace-сообщения всегда передают точный connection id, чтобы
    исходящее одного владельца не стало входящим клиента другого владельца.
    """

    business = load_state().get("business") or {}
    preferred = str(business_connection_id or "").strip()
    if preferred:
        info = business.get(preferred) or {}
        try:
            return int(info["user_id"]) if info.get("user_id") else None
        except (TypeError, ValueError):
            return None
    for info in business.values():
        if info.get("user_id"):
            return int(info["user_id"])
    return None


_inbox_lock = threading.Lock()
_inbox: dict[Any, list[dict]] = {}
_inbox_last: dict[Any, float] = {}      # когда человек написал в последний раз (monotonic)
_REPLY_DEBOUNCE_S = float(os.getenv("TG_REPLY_DEBOUNCE_S", "15") or 15)




def _wait_for_quiet(uid) -> None:
    """Подождать, пока человек выговорится: окно тишины отсчитывается от ПОСЛЕДНЕГО сообщения.

    30 секунд владелец счёл слишком долгими (24.07.2026) — теперь 15."""
    while True:
        with _inbox_lock:
            last = _inbox_last.get(uid) or 0.0
        wait = _REPLY_DEBOUNCE_S - (time.monotonic() - last)
        if wait <= 0:
            return
        time.sleep(wait)


def maybe_autoreply(msg: dict) -> None:
    """Ответить лиду в личке ОТ ЛИЦА аккаунта компании.

    Отвечаем ТОЛЬКО на входящие от живых людей. Свои же исходящие тоже приходят этим
    апдейтом, и без фильтра агент отвечал бы сам себе бесконечно.

    Люди пишут мысль несколькими сообщениями подряд. Раньше каждое уходило в отдельный ход,
    ходы не видели ответов друг друга — агент спрашивал «Давайте начнём?» и тут же «Условия
    вам подходят?», дважды просил реквизиты (диалог 23.07.2026, записи 218–225). Теперь
    сообщения человека копятся в буфере, ход забирает всё накопившееся и отвечает одним
    сообщением на всё сразу.

    Разговоры разных людей идут параллельно, ходы одного — строго по очереди."""
    uid = (msg.get("from") or {}).get("id")
    if uid is None:
        return
    with _inbox_lock:
        _inbox.setdefault(uid, []).append(msg)
        _inbox_last[uid] = time.monotonic()
    with dialog_lock(uid):
        with _inbox_lock:
            if not _inbox.get(uid):
                return      # сообщение уже забрал предыдущий ход
        # Пауза-добор СКОЛЬЗЯЩАЯ: отсчёт от ПОСЛЕДНЕГО сообщения (владелец, 24.07.2026).
        # Человек пишет 7 сообщений подряд — агент молчит, пока тот не выговорится, и отвечает
        # на всё разом, как живой менеджер. Каждое новое сообщение сдвигает окно.
        _wait_for_quiet(uid)
        with _inbox_lock:
            batch = _inbox.pop(uid, [])
        if batch:
            _autoreply_turn(batch)


# Автоответы Открытой линии (Wazzup) прилетают в тот же чат и попадали к нам КАК СООБЩЕНИЯ
# КЛИЕНТА: 24.07.2026 в диалоге 980579939 агент «услышал» от клиента «Добро пожаловать в
# Открытую линию компании». Это не человек — это робот соседнего канала; в разговор такое
# брать нельзя.
_OPENLINE_NOISE = (
    "добро пожаловать в открытую линию",
    "вам ответит первый освободившийся оператор",
    "спасибо, что написали. мы скоро ответим",
)


def _is_openline_noise(text: str) -> bool:
    low = " ".join((text or "").lower().split())
    return any(marker in low for marker in _OPENLINE_NOISE)




def _autoreply_turn(msgs: list[dict] | dict) -> None:
    msgs = [msgs] if isinstance(msgs, dict) else list(msgs)
    msgs = [m for m in msgs
            if not _is_openline_noise((m.get("text") or m.get("caption") or ""))]
    texts = [t for m in msgs if (t := (m.get("text") or m.get("caption") or "").strip())]
    if not texts:
        return
    last = msgs[-1]
    author = last.get("from") or {}
    chat = last.get("chat") or {}
    author_id = author.get("id")
    if not author_id or author.get("is_bot"):
        return
    owner_id = _business_owner_id()
    if owner_id and author_id == owner_id:
        return  # это исходящее самого владельца, а не входящее от клиента
    if str(chat.get("type") or "private") != "private":
        return  # phase 2: только личные переписки
    conn_id = last.get("business_connection_id") or ""
    if not conn_id:
        return

    # Выключатель воронки из кабинета («Работа с воронками»): владелец должен иметь возможность
    # остановить агента сам, не дожидаясь инженера. Выключен — молчим совсем, разговор ведут люди.
    if not funnel_scenario.agent_enabled(_db, CRM_LEAD_CATEGORY_ID):
        log.info("агент на воронке %s выключен в кабинете — %s отвечают люди",
                 CRM_LEAD_CATEGORY_ID, author_id)
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
        reply_to_stranger(author, texts)
        return

    # В журнал попадают только переписки, где участвует агент: у лида воронки он ведёт разговор.
    # Каждое сообщение пачки — своей записью: журнал должен отражать переписку как она была.
    for m in msgs:
        t = (m.get("text") or m.get("caption") or "").strip()
        if t:
            journal(MANAGER_CHANNEL, author_id, "in", t, kind="lead_chat", user=author,
                    tg_message_id=m.get("message_id"), meta={"deal_id": deal_id})
    react(author_id, last.get("message_id"), "👀", conn_id)

    import iu_runtime

    ok, _ = iu_runtime.run_turn(author, texts, deal_id=deal_id)
    log.info("автоответ лиду %s: %s", author_id, "отправлен" if ok else "не отправлен")

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


def _capture_failure_delay(consecutive_failures: int) -> float:
    """Bound polling pressure while PostgreSQL cannot durably accept updates."""

    failures = max(1, int(consecutive_failures or 1))
    return float(min(30, 2 ** min(failures - 1, 5)))


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
    workspace_mode = funnel_workspace_enabled()
    if workspace_mode:
        import funnel_telegram_gateway

        funnel_telegram_gateway.start_workers()
        log.info("custom funnel workspace owns Telegram Business traffic")
    else:
        # Сторож ожиданий: без него «задача закрыта → сообщение клиенту» не срабатывало никогда —
        # check_finished_tasks существовал, но его никто не вызывал (владелец, 23.07.2026).
        start_task_watchdog()
    me = api("getMe")
    log.info("bot: @%s (id %s)", me.get("username"), me.get("id"))
    offset = int(load_state().get("offset") or 0)
    capture_failures = 0
    while True:
        try:
            updates = api(
                "getUpdates",
                http_timeout=65,
                timeout=55,
                offset=offset,
                allowed_updates=[
                    "message",
                    "business_connection",
                    "business_message",
                    "edited_business_message",
                    "deleted_business_messages",
                ],
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("getUpdates failed: %s", str(exc)[:200])
            time.sleep(5)
            continue
        capture_failed = False
        for upd in updates or []:
            next_offset = int(upd.get("update_id", 0)) + 1
            if workspace_mode:
                try:
                    # Commit first, acknowledge to Telegram second. A DB outage therefore causes
                    # a replay, not a silently lost customer message.
                    funnel_telegram_gateway.capture_poll_update(upd)
                except Exception as exc:  # noqa: BLE001
                    capture_failures += 1
                    capture_failed = True
                    log.warning(
                        "workspace did not durably capture Telegram update; retrying after backoff: %s",
                        str(exc)[:200],
                    )
                    break
                capture_failures = 0
                offset = max(offset, next_offset)
            else:
                offset = max(offset, next_offset)
                # Обработка уходит в пул: ход мозга занимает десятки секунд, и раньше цикл стоял
                # на нём целиком — десятый написавший ждал бы минуты. Порядок сообщений ОДНОГО
                # человека держит dialog_lock, число одновременных ходов — _hermes_slots.
                _workers.submit(_handle_update_safely, upd)
        with _state_lock:
            state = load_state()
            state["offset"] = offset
            save_state(state)
        if capture_failed:
            time.sleep(_capture_failure_delay(capture_failures))


if __name__ == "__main__":
    _load_env_file()
    if not bot_token():
        raise SystemExit("TG_AGENT_BOT_TOKEN is not configured")
    poll_forever()
