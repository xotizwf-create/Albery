"""Agent Center read-only API (/api/agent-center/*) — backs the "Центр Агента" SPA section.

Serves data that already lives in PostgreSQL and on this box: employee↔bot dialogs
(bitrix_bot_interactions), the main agent profile with usage stats (agent_access +
interactions), the instruction library (ai_instruction_folders) and the Hermes
skill library (/root/.hermes/skills/**/SKILL.md — the agent's gateway loads them
from there; custom ones are versioned in this repo under scripts/hermes_skills).

Registers routes on the shared Flask `app` at import time (same pattern as b24bot);
app.py imports this module at the bottom. Every endpoint is GET/read-only and sits
behind the site's admin session login + /api gate (require_admin_auth in app.py).
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time

import requests

from datetime import datetime
from datetime import timedelta
from datetime import timezone
from flask import jsonify
from flask import request
from pathlib import Path
from typing import Any

from app import (
    MSK_TZ,
    app,
    msk_now,
    pg_connect,
)
from shared.db import load_env_value as shared_load_env_value
from utils import to_int

_DIALOGS_LIMIT_DEFAULT = 100
_MESSAGES_LIMIT_DEFAULT = 200


# Direct URLs for the Центр Агента pages (the SPA routes itself by pathname). The
# <path:sub> variants let a specific agent/dialog be deep-linked (e.g. /agent/main,
# /agent-dialogs/main/22) and survive a refresh — all serve the same SPA index.html.
@app.get("/agent")
@app.get("/agent/<path:sub>")
@app.get("/agent-dialogs")
@app.get("/agent-dialogs/<path:sub>")
@app.get("/agent-knowledge")
@app.get("/agent-monitoring")
@app.get("/agent-usage")
def agent_center_spa(sub: str = ""):
    from app import index
    return index()

# The main Bitrix24 bot is the default agent. Legacy tier columns remain in the
# DB as creation presets / labels, but a personal agent's real capability boundary
# is its enabled tool whitelist.
_MAIN_AGENT_META = {
    "id": "main",
    "name": "Основной агент",
    "kind": "Bitrix24-бот • Мозг Гермеса",
    "icon": "zap",
    "icon_bg": "bg-orange-100 text-orange-500",
}

_B24_MARKUP_RE = re.compile(r"\[/?(?:b|i|u|s|code|quote|url(?:=[^\]]*)?|user(?:=[^\]]*)?)\]", re.IGNORECASE)


def _strip_b24_markup(text: str) -> str:
    return _B24_MARKUP_RE.sub("", text or "").strip()


def _limit_arg(default: int, ceiling: int) -> int:
    try:
        value = int(request.args.get("limit") or default)
    except (TypeError, ValueError):
        value = default
    return min(max(value, 1), ceiling)


def _when_label(dt: Any) -> str:
    """15:05 for today, «вчера», DD.MM otherwise — matches the dialog-list design.
    Accepts a datetime or an ISO-8601 string (registry frontmatter timestamps)."""
    if not dt:
        return ""
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except ValueError:
            return ""
    local = dt.astimezone(MSK_TZ)
    today = msk_now().date()
    day = local.date()
    if day == today:
        return local.strftime("%H:%M")
    if (today - day).days == 1:
        return "вчера"
    return local.strftime("%d.%m")


def _user_names() -> dict[int, dict[str, str]]:
    """{bitrix_user_id: {name, position}} — portal directory first, access rows as fallback."""
    out: dict[int, dict[str, str]] = {}
    try:
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT bitrix_user_id, display_name FROM agent_access WHERE display_name IS NOT NULL")
                for r in cur.fetchall():
                    out[int(r["bitrix_user_id"])] = {"name": r["display_name"], "position": ""}
    except Exception:  # noqa: BLE001
        logging.exception("agent_center: access-name fallback load failed")
    try:
        # Lazy import: b24bot may still be mid-import when a script imports it first
        # (b24bot → app → agent_center), so never touch it at module level here.
        from b24bot import _b24_portal_user_directory
        for uid, info in _b24_portal_user_directory().items():
            out[uid] = {"name": info.get("name") or f"#{uid}", "position": info.get("position") or ""}
    except Exception:  # noqa: BLE001
        logging.exception("agent_center: portal directory load failed")
    return out


# --- Telegram side of the agent centre ------------------------------------------------------
# Один бот-токен (@Albery_AI2_Bot) обслуживает два канала, и для владельца это разные агенты:
# личка самого бота и бизнес-переписки аккаунта компании @AlberyAIManager. Журнал пишет
# tg_agent (служба albery-tg) в telegram_bot_messages — миграция 060.
TG_BOT_CHANNEL = "albery-ai-bot"
TG_MANAGER_CHANNEL = "albery-ai-manager"
# Запасные подписи на случай, если Telegram недоступен. Настоящие имя и @username всегда
# берутся из самого Telegram (_tg_identities): в кабинете агент должен называться ровно так же,
# как в мессенджере — иначе владелец ищет в интерфейсе имя, которого там нет.
TG_CHANNELS = {
    TG_BOT_CHANNEL: {"name": "Telegram-бот", "handle": "", "subtitle": "личные сообщения боту"},
    TG_MANAGER_CHANNEL: {"name": "Аккаунт компании", "handle": "",
                         "subtitle": "аккаунт компании • лиды"},
}
_TG_IDENTITY_CACHE: dict[str, Any] = {"at": 0.0, "data": {}}


def _tg_api(method: str, **params) -> dict[str, Any]:
    token = (shared_load_env_value("TG_AGENT_BOT_TOKEN") or "").strip()
    if not token:
        return {}
    try:
        resp = requests.post(f"https://api.telegram.org/bot{token}/{method}",
                             json=params, timeout=15)
        data = resp.json() if resp.content else {}
        return (data.get("result") or {}) if data.get("ok") else {}
    except Exception:  # noqa: BLE001
        logging.warning("telegram %s failed", method, exc_info=True)
        return {}


def _tg_identities() -> dict[str, dict[str, str]]:
    """Имя и @username каждого Telegram-канала — как их видит сам Telegram."""
    now = time.time()
    if _TG_IDENTITY_CACHE["data"] and now - float(_TG_IDENTITY_CACHE["at"]) < 600:
        return dict(_TG_IDENTITY_CACHE["data"])
    out: dict[str, dict[str, str]] = {}
    me = _tg_api("getMe")
    if me:
        out[TG_BOT_CHANNEL] = {"name": str(me.get("first_name") or "").strip(),
                               "handle": "@" + str(me.get("username") or "")}
    # Аккаунт компании — владелец бизнес-подключения; его id знает служба albery-tg.
    owner_id = None
    try:
        state = json.loads((Path("/var/www/albery") / ".tg_agent_state.json").read_text(encoding="utf-8"))
        for info in (state.get("business") or {}).values():
            owner_id = info.get("user_id") or owner_id
    except Exception:  # noqa: BLE001
        owner_id = None
    if owner_id:
        chat = _tg_api("getChat", chat_id=owner_id)
        if chat:
            name = " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x).strip()
            out[TG_MANAGER_CHANNEL] = {"name": name, "handle": "@" + str(chat.get("username") or "")}
    if out:
        _TG_IDENTITY_CACHE.update({"at": now, "data": out})
    return out


def _tg_channel_meta() -> dict[str, dict[str, str]]:
    """Все Telegram-каналы: два штатных + заведённые владельцем.

    Имена берутся из Telegram, если он доступен, иначе запасные."""
    live = _tg_identities()
    meta = {}
    for slug, base in TG_CHANNELS.items():
        got = live.get(slug) or {}
        meta[slug] = {**base,
                      "name": got.get("name") or base["name"],
                      "handle": got.get("handle") or base["handle"]}
    for a in telegram_agents_list():
        if not a.get("is_active"):
            continue
        meta[a["slug"]] = {"name": a.get("name") or a["slug"],
                           "handle": "@" + str(a.get("username") or ""),
                           "subtitle": "агент владельца"}
    return meta
# Подвкладки менеджера: разговор с самим агентом и переписки с людьми — разные потоки.
TG_KINDS = {"bot_dm": "В боте", "lead_chat": "Диалоги с пользователями"}


def _telegram_dialogs(q: str, agent: str, kind: str, limit: int) -> list[dict[str, Any]]:
    """Список Telegram-переписок: последнее сообщение, счётчики, сбои — как во вкладке Bitrix."""
    where = ["dialog_id IS NOT NULL"]
    scope: list[Any] = []               # параметры одного блока условий
    if agent and agent != "all":
        where.append("bot = %s")
        scope.append(agent)
    if kind and kind != "all":
        where.append("kind = %s")
        scope.append(kind)
    cond = " AND ".join(where)
    sql = f"""
        WITH last AS (
            SELECT DISTINCT ON (bot, dialog_id)
                   bot, dialog_id, tg_user_id, username, display_name, direction, kind,
                   text, created_at
            FROM telegram_bot_messages WHERE {cond}
            ORDER BY bot, dialog_id, id DESC
        ),
        agg AS (
            SELECT bot, dialog_id, COUNT(*) AS turns, MAX(created_at) AS last_at,
                   COUNT(*) FILTER (WHERE status <> 'ok') AS errors,
                   MAX(display_name) AS any_name, MAX(username) AS any_username
            FROM telegram_bot_messages WHERE {cond}
            GROUP BY bot, dialog_id
        )
        SELECT l.*, a.turns, a.last_at, a.errors,
               COALESCE(l.display_name, a.any_name) AS name2,
               COALESCE(l.username, a.any_username) AS username2
        FROM last l JOIN agg a ON a.bot = l.bot AND a.dialog_id = l.dialog_id
    """
    params: list[Any] = [*scope, *scope]        # блок last + блок agg
    if q:
        sql += (f" WHERE l.dialog_id IN (SELECT DISTINCT dialog_id FROM telegram_bot_messages"
                f" WHERE text ILIKE %s AND {cond})")
        params += [f"%{q}%", *scope]            # поиск остаётся внутри выбранного агента
    sql += " ORDER BY a.last_at DESC LIMIT %s"
    params.append(limit)
    out: list[dict[str, Any]] = []
    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    for r in rows:
        preview = (r["text"] or "").strip()
        if r["direction"] == "out":
            preview = "Агент: " + preview
        if len(preview) > 100:
            preview = preview[:100].rstrip() + "…"
        # В Telegram человека узнают по @username — его и ставим заголовком переписки. Имя
        # профиля идёт подписью: у половины собеседников оно одинаковое или вовсе «Albery».
        uname = r["username2"] or ""
        title = f"@{uname}" if uname else (r["name2"] or f"id {r['dialog_id']}")
        subtitle = (r["name2"] or "") if uname else ""
        out.append({
            "dialog_id": str(r["dialog_id"]),
            "task_id": None,
            "bitrix_user_id": None,
            "user_name": title,
            "user_position": subtitle,
            "tier": "ops",
            "agent_slug": r["bot"],
            "kind": r["kind"],
            "last_message": preview,
            "last_status": "error" if int(r["errors"] or 0) else "ok",
            "turns": int(r["turns"] or 0),
            "errors": int(r["errors"] or 0),
            "time": _when_label(r["last_at"]),
        })
    return out


def _telegram_dialog_messages(dialog_id: str, agent: str):
    """Одна Telegram-переписка целиком — тем же форматом, что и вкладка Bitrix."""
    where = ["dialog_id = %s"]
    params: list[Any] = [dialog_id]
    if agent and agent != "all":
        where.append("bot = %s")
        params.append(agent)
    limit = _limit_arg(_MESSAGES_LIMIT_DEFAULT, 1000)
    turns = []
    try:
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, created_at, direction, kind, text, status, display_name, username"
                    f" FROM telegram_bot_messages WHERE {' AND '.join(where)}"
                    " ORDER BY id DESC LIMIT %s", [*params, limit])
                rows = cur.fetchall()
    except Exception:  # noqa: BLE001
        logging.exception("agent_center telegram dialog messages failed")
        return jsonify({"error": "Не удалось загрузить переписку."}), 500
    for r in reversed(rows):
        local = r["created_at"].astimezone(MSK_TZ) if r["created_at"] else None
        text = (r["text"] or "").strip()
        inbound = r["direction"] == "in"
        turns.append({
            "id": int(r["id"]),
            "date": local.strftime("%d.%m.%Y") if local else "",
            "time": local.strftime("%H:%M") if local else "",
            "question": text if inbound else "",
            "answer": "" if inbound else text,
            "status": r["status"] or "ok",
            "error": None,
            "latency_ms": None,
            "kind": TG_KINDS.get(r["kind"], r["kind"]),
        })
    return jsonify({"turns": turns})


def telegram_agents_list() -> list[dict[str, Any]]:
    """Агенты с телеграмным мостом. Токен наружу не отдаётся никогда — только признак.

    Живут в общей таблице `agents`: у телеграмного агента тот же редактор возможностей, что и
    у битриксового субагента (инструменты, инструкции, знания), отличается только мост."""
    try:
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT slug, name, telegram_username AS username, role_prompt,"
                            " is_active, telegram_bot_user_id AS bot_user_id,"
                            " telegram_bot_token IS NOT NULL AS has_token"
                            " FROM agents WHERE telegram_bot_token IS NOT NULL"
                            " ORDER BY created_at")
                return [dict(r) for r in cur.fetchall()]
    except Exception:  # noqa: BLE001
        logging.exception("telegram agents list failed")
        return []


@app.get("/api/agent-center/telegram-agents")
def agent_center_telegram_agents():
    return jsonify({"agents": telegram_agents_list()})


@app.get("/api/agent-center/telegram-access")
def agent_center_telegram_access():
    """Кто может писать Telegram-агентам. До этого список жил строкой в .env на сервере."""
    try:
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, bot, username, tg_user_id, display_name, is_active, note"
                            " FROM telegram_bot_access ORDER BY bot, username")
                rows = [dict(r) for r in cur.fetchall()]
    except Exception:  # noqa: BLE001
        logging.exception("agent_center telegram access failed")
        return jsonify({"error": "Не удалось загрузить доступ."}), 500
    agents = [{"slug": slug, **meta,
               "users": [r for r in rows if r["bot"] == slug]}
              for slug, meta in _tg_channel_meta().items()]
    return jsonify({"agents": agents})


@app.post("/api/agent-center/telegram-access")
def agent_center_telegram_access_save():
    """Добавить/убрать доступ. Telegram не умеет искать людей по @username, поэтому ключ —
    username, а числовой id дописывается сам, когда человек впервые написал агенту."""
    body = request.get_json(silent=True) or {}
    bot = str(body.get("bot") or "").strip()
    username = str(body.get("username") or "").strip().lstrip("@").lower()
    if bot not in _tg_channel_meta():
        return jsonify({"error": "Неизвестный агент."}), 400
    if not re.fullmatch(r"[a-z0-9_]{3,64}", username or ""):
        return jsonify({"error": "Укажите @username (латиница, цифры, подчёркивание)."}), 400
    remove = bool(body.get("remove"))
    try:
        with pg_connect() as conn:
            with conn.cursor() as cur:
                if remove:
                    cur.execute("DELETE FROM telegram_bot_access WHERE bot = %s AND username = %s",
                                (bot, username))
                else:
                    cur.execute(
                        "INSERT INTO telegram_bot_access (bot, username, display_name, note)"
                        " VALUES (%s, %s, %s, %s)"
                        " ON CONFLICT (bot, username) DO UPDATE SET is_active = true,"
                        " display_name = COALESCE(EXCLUDED.display_name, telegram_bot_access.display_name)",
                        (bot, username, str(body.get("display_name") or "").strip() or None,
                         str(body.get("note") or "").strip() or None))
    except Exception:  # noqa: BLE001
        logging.exception("agent_center telegram access save failed")
        return jsonify({"error": "Не удалось сохранить доступ."}), 500
    return jsonify({"ok": True, "bot": bot, "username": username, "removed": remove})


@app.get("/api/agent-center/dialogs")
def agent_center_dialogs():
    channel = (request.args.get("channel") or "bitrix").strip().lower()
    if channel == "telegram":
        agent = (request.args.get("agent") or "all").strip()
        kind = (request.args.get("kind") or "all").strip().lower()
        try:
            dialogs = _telegram_dialogs((request.args.get("q") or "").strip(), agent, kind,
                                        _limit_arg(_DIALOGS_LIMIT_DEFAULT, 500))
        except Exception:  # noqa: BLE001
            logging.exception("agent_center telegram dialogs failed")
            return jsonify({"error": "Не удалось загрузить Telegram-переписки."}), 500
        note = "" if dialogs else ("Переписок пока нет. Здесь появятся диалоги, в которых "
                                   "участвовал агент.")
        return jsonify({"dialogs": dialogs, "note": note})
    q = (request.args.get("q") or "").strip()
    # Each bot keeps its OWN dialog history — do not pool them. Subagent turns carry
    # agent_slug=<slug>; the universal/main agent's turns have agent_slug IS NULL.
    # agent="all" (or absent) = no filter; agent=<slug>/"main" = only that bot's dialogs.
    agent = (request.args.get("agent") or "all").strip()
    agent_filter = ""
    agent_params: list[Any] = []
    if agent and agent != "all":
        if agent == MAIN_AGENT_SLUG:
            agent_filter = " AND agent_slug IS NULL"
        else:
            agent_filter = " AND agent_slug = %s"
            agent_params = [agent]
    # In-task mentions ("Тебя позвали ПРЯМО В ЗАДАЧЕ …") are logged with dialog_id="task-<id>"
    # by b24bot; regular private chats use the numeric user id. Keep the two streams apart so
    # in-task threads never mix into the ordinary dialog list. kind=chat (default) = only private
    # chats; kind=task = only in-task threads; kind=all = both. (% is doubled for psycopg.)
    kind = (request.args.get("kind") or "chat").strip().lower()
    if kind == "task":
        kind_filter = " AND dialog_id LIKE 'task-%%'"
    elif kind == "all":
        kind_filter = ""
    else:
        kind_filter = " AND dialog_id NOT LIKE 'task-%%'"
    limit = _limit_arg(_DIALOGS_LIMIT_DEFAULT, 500)
    # A "dialog" belongs to a specific bot. In Bitrix a private bot chat is keyed by the
    # USER (dialog_id = user id), so the SAME dialog_id is reused by every bot that user
    # messages. Key by (agent_slug, dialog_id) and scope every aggregate to that bot, so
    # one bot's turns never leak into another's list entry, preview or counts.
    # Source of truth = the FULL message journal: it also holds messages that never had a
    # question (proactive DMs from the daily check-in, task offers, notification posts, digests)
    # and inbound messages that have not been answered yet.
    sql = f"""
        WITH last AS (
            SELECT DISTINCT ON (agent_slug, dialog_id)
                   dialog_id, agent_slug, bitrix_user_id, direction, kind, text, created_at
            FROM bitrix_bot_messages
            WHERE dialog_id IS NOT NULL{agent_filter}{kind_filter}
            ORDER BY agent_slug, dialog_id, id DESC
        ),
        agg AS (
            SELECT dialog_id, agent_slug,
                   COUNT(*) AS turns,
                   MAX(created_at) AS last_at,
                   MAX(bitrix_user_id) AS any_uid
            FROM bitrix_bot_messages
            WHERE dialog_id IS NOT NULL{agent_filter}{kind_filter}
            GROUP BY dialog_id, agent_slug
        )
        SELECT l.dialog_id, l.agent_slug, COALESCE(l.bitrix_user_id, a.any_uid) AS bitrix_user_id,
               l.direction, l.kind, l.text, a.turns, a.last_at
        FROM last l
        JOIN agg a ON a.dialog_id = l.dialog_id
                  AND a.agent_slug IS NOT DISTINCT FROM l.agent_slug
    """
    params: list[Any] = list(agent_params) + list(agent_params)  # last CTE + agg CTE
    if q:
        sql += (
            " WHERE l.dialog_id IN (SELECT DISTINCT dialog_id FROM bitrix_bot_messages"
            f" WHERE text ILIKE %s{agent_filter}{kind_filter})"
        )
        params.append(f"%{q}%")
        params.extend(agent_params)  # keep search within the selected bot too
    sql += " ORDER BY a.last_at DESC LIMIT %s"
    params.append(limit)
    dialogs = []
    try:
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        names = _user_names()
        errs = _dialog_error_counts()
        laststat = _dialog_last_status()
        for r in rows:
            did = r["dialog_id"] or ""
            uid = int(r["bitrix_user_id"]) if r["bitrix_user_id"] is not None else None
            info = names.get(uid or -1, {})
            task_id = None
            if did.startswith("task-"):
                try:
                    task_id = int(did[len("task-"):])
                except ValueError:
                    task_id = None
            preview = _strip_b24_markup(r["text"] or "")
            if r["direction"] == "out":
                preview = "Агент: " + preview
            if len(preview) > 100:
                preview = preview[:100].rstrip() + "…"
            slug = r["agent_slug"] or MAIN_AGENT_SLUG
            if did.lower().startswith("chat"):
                title = "📢 Канал уведомлений"
            elif task_id:
                title = f"Задача №{task_id}"
            else:
                title = info.get("name") or (f"Сотрудник #{uid}" if uid else "Сотрудник")
            dialogs.append({
                "dialog_id": did,
                "task_id": task_id,
                "bitrix_user_id": uid,
                "user_name": title,
                "user_position": info.get("position") or "",
                "tier": "ops",
                "agent_slug": slug,
                "last_message": preview,
                "last_status": laststat.get((slug, did), "ok"),
                "turns": int(r["turns"]),
                "errors": int(errs.get((slug, did), 0)),
                "time": _when_label(r["last_at"]),
            })
    except Exception:  # noqa: BLE001
        logging.exception("agent_center dialogs failed")
        return jsonify({"error": "Не удалось загрузить диалоги."}), 500
    return jsonify({"dialogs": dialogs})


def _dialog_error_counts() -> dict:
    """Failed turns per (agent, dialog) — the message journal stores what was actually sent, while
    the turn log keeps the error status, so the error badge keeps working.

    Разобранные ошибки (error_resolved_at) не считаются: иначе один давний таймаут навсегда
    помечал диалог как проблемный и снять это было нечем (владелец 2026-07-20)."""
    out: dict = {}
    try:
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT dialog_id, agent_slug, COUNT(*) AS n FROM bitrix_bot_interactions"
                    " WHERE status <> 'ok' AND error_resolved_at IS NULL"
                    " GROUP BY dialog_id, agent_slug")
                for r in cur.fetchall():
                    out[(r["agent_slug"] or MAIN_AGENT_SLUG, r["dialog_id"])] = int(r["n"])
    except Exception:  # noqa: BLE001
        logging.debug("agent_center: error counts failed", exc_info=True)
    return out


MAIN_SLUG_SENTINEL = "main"


def list_dialog_errors(dialog_id: str = "", agent_slug: str = "", include_resolved: bool = False,
                       limit: int = 50) -> list[dict[str, Any]]:
    """Ошибочные ходы для разбора: что упало, когда, у кого и разобрано ли это.

    Один источник правды и для интерфейса, и для MCP-инструмента агента."""
    where = ["status <> 'ok'"]
    params: list[Any] = []
    if not include_resolved:
        where.append("error_resolved_at IS NULL")
    if dialog_id:
        where.append("dialog_id = %s")
        params.append(str(dialog_id))
    if agent_slug:
        if agent_slug == MAIN_SLUG_SENTINEL:
            where.append("agent_slug IS NULL")
        else:
            where.append("agent_slug = %s")
            params.append(agent_slug)
    params.append(max(1, min(int(limit or 50), 200)))
    rows: list[dict[str, Any]] = []
    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT id, created_at, dialog_id, agent_slug, bitrix_user_id, status,
                           left(coalesce(error, ''), 300) AS error,
                           left(coalesce(question, ''), 200) AS question,
                           error_resolved_at, error_resolved_by, error_resolved_task,
                           error_resolved_note
                    FROM bitrix_bot_interactions
                    WHERE {' AND '.join(where)}
                    ORDER BY created_at DESC LIMIT %s""",
                params,
            )
            for r in cur.fetchall():
                rows.append({
                    "interaction_id": r["id"],
                    "at": r["created_at"].isoformat() if r["created_at"] else None,
                    "dialog_id": r["dialog_id"],
                    "agent_slug": r["agent_slug"] or MAIN_SLUG_SENTINEL,
                    "bitrix_user_id": r["bitrix_user_id"],
                    "status": r["status"],
                    "error": r["error"],
                    "question_preview": r["question"],
                    "resolved": r["error_resolved_at"] is not None,
                    "resolved_at": r["error_resolved_at"].isoformat() if r["error_resolved_at"] else None,
                    "resolved_by": r["error_resolved_by"],
                    "resolved_task_id": r["error_resolved_task"],
                    "resolved_note": r["error_resolved_note"],
                })
    return rows


def resolve_dialog_errors(dialog_id: str, agent_slug: str = "", task_id: Any = None,
                          note: str = "", by: str = "", interaction_id: Any = None) -> int:
    """Пометить ошибки разобранными. Возвращает число снятых отметок.

    Требование владельца: указывать номер задачи, в которой ошибка устранена, — чтобы метка
    снималась не «просто так», а со ссылкой на проделанную работу."""
    if not dialog_id and interaction_id is None:
        raise ValueError("нужен dialog_id или interaction_id")
    where = ["status <> 'ok'", "error_resolved_at IS NULL"]
    params: list[Any] = [str(by or "")[:120], to_int(task_id), str(note or "")[:500]]
    if interaction_id is not None:
        where.append("id = %s")
        params.append(int(interaction_id))
    else:
        where.append("dialog_id = %s")
        params.append(str(dialog_id))
        if agent_slug:
            if agent_slug == MAIN_SLUG_SENTINEL:
                where.append("agent_slug IS NULL")
            else:
                where.append("agent_slug = %s")
                params.append(agent_slug)
    with pg_connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    f"""UPDATE bitrix_bot_interactions
                        SET error_resolved_at = now(), error_resolved_by = %s,
                            error_resolved_task = %s, error_resolved_note = %s
                        WHERE {' AND '.join(where)}""",
                    params,
                )
                return cur.rowcount or 0


@app.get("/api/agent-center/dialog-errors")
def agent_center_dialog_errors():
    """Список ошибок диалога — что именно упало и разобрано ли."""
    try:
        rows = list_dialog_errors(
            dialog_id=(request.args.get("dialog_id") or "").strip(),
            agent_slug=(request.args.get("agent") or "").strip(),
            include_resolved=(request.args.get("include_resolved") or "").lower() in {"1", "true", "yes"},
            limit=_limit_arg(50, 200),
        )
    except Exception:  # noqa: BLE001
        logging.exception("agent_center: dialog errors failed")
        return jsonify({"error": "Не удалось получить список ошибок."}), 500
    return jsonify({"errors": rows, "total": len(rows)})


@app.post("/api/agent-center/dialog-errors/resolve")
def agent_center_resolve_dialog_errors():
    """Снять метку «ОШИБКА» с диалога, указав задачу, в которой всё устранено."""
    body = request.get_json(silent=True) or {}
    dialog_id = str(body.get("dialog_id") or "").strip()
    task_id = body.get("task_id")
    if not dialog_id and body.get("interaction_id") is None:
        return jsonify({"error": "Укажите диалог."}), 400
    if to_int(task_id) is None and not str(body.get("note") or "").strip():
        return jsonify({"error": "Укажите номер задачи Битрикса или комментарий, "
                                 "в чём ошибка устранена."}), 400
    try:
        n = resolve_dialog_errors(
            dialog_id=dialog_id,
            agent_slug=str(body.get("agent") or "").strip(),
            task_id=task_id,
            note=str(body.get("note") or ""),
            by=str(body.get("by") or "владелец (интерфейс)"),
            interaction_id=body.get("interaction_id"),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:  # noqa: BLE001
        logging.exception("agent_center: resolve dialog errors failed")
        return jsonify({"error": "Не удалось снять метку."}), 500
    return jsonify({"ok": True, "resolved": n})


def _dialog_last_status() -> dict:
    """Status of the MOST RECENT turn per (agent, dialog) from the turn log, so the dialog list
    shows a real last_status (timeout/error/busy) instead of a hardcoded 'ok'. Dialogs with only
    proactive messages (no turn) default to 'ok'."""
    out: dict = {}
    try:
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT ON (agent_slug, dialog_id) dialog_id, agent_slug, status"
                    " FROM bitrix_bot_interactions ORDER BY agent_slug, dialog_id, id DESC")
                for r in cur.fetchall():
                    out[(r["agent_slug"] or MAIN_AGENT_SLUG, r["dialog_id"])] = r["status"] or "ok"
    except Exception:  # noqa: BLE001
        logging.debug("agent_center: last status failed", exc_info=True)
    return out


@app.get("/api/agent-center/dialog-messages")
def agent_center_dialog_messages():
    dialog_id = (request.args.get("dialog_id") or "").strip()
    if not dialog_id:
        return jsonify({"error": "Укажите dialog_id."}), 400
    if (request.args.get("channel") or "").strip().lower() == "telegram":
        return _telegram_dialog_messages(dialog_id, (request.args.get("agent") or "all").strip())
    # Same dialog_id is shared across bots (Bitrix keys a private bot chat by the user),
    # so a thread MUST be scoped to the bot or it leaks other bots' turns into this one.
    agent = (request.args.get("agent") or "all").strip()
    agent_filter = ""
    agent_params: list[Any] = []
    if agent and agent != "all":
        if agent == MAIN_AGENT_SLUG:
            agent_filter = " AND agent_slug IS NULL"
        else:
            agent_filter = " AND agent_slug = %s"
            agent_params = [agent]
    limit = _limit_arg(_MESSAGES_LIMIT_DEFAULT, 1000)
    turns = []
    try:
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, created_at, direction, kind, text"
                    f" FROM bitrix_bot_messages WHERE dialog_id = %s{agent_filter} ORDER BY id DESC LIMIT %s",
                    [dialog_id, *agent_params, limit],
                )
                rows = cur.fetchall()
                cur.execute(
                    "SELECT created_at, status, error FROM bitrix_bot_interactions"
                    f" WHERE dialog_id = %s{agent_filter} AND status <> 'ok' ORDER BY created_at",
                    [dialog_id, *agent_params],
                )
                fails = [(fr["created_at"], fr["status"], fr["error"]) for fr in cur.fetchall()]
        _KIND_LABEL = {"notification": "уведомление", "task_comment": "комментарий в задаче",
                       "system": "система"}
        for r in reversed(rows):
            local = r["created_at"].astimezone(MSK_TZ) if r["created_at"] else None
            text = (r["text"] or "").strip()
            inbound = r["direction"] == "in"
            mstatus, merror = "ok", None
            if not inbound and fails and r["created_at"]:
                for fc, fs, fe in fails:
                    if fc and abs((fc - r["created_at"]).total_seconds()) <= 120:
                        mstatus, merror = fs, fe
                        break
            turns.append({
                "id": int(r["id"]),
                "date": local.strftime("%d.%m.%Y") if local else "",
                "time": local.strftime("%H:%M") if local else "",
                "question": text if inbound else "",
                "answer": "" if inbound else _strip_b24_markup(text),
                "status": mstatus,
                "error": merror,
                "latency_ms": None,
                "tier": "ops",
                "session_name": _KIND_LABEL.get(r["kind"] or "", ""),
            })
    except Exception:  # noqa: BLE001
        logging.exception("agent_center dialog messages failed")
        return jsonify({"error": "Не удалось загрузить переписку."}), 500
    return jsonify({"dialog_id": dialog_id, "turns": turns})


@app.get("/api/agent-center/agents")
def agent_center_agents():
    day_start = msk_now().replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = day_start - timedelta(days=7)
    st: dict[str, Any] = {}
    users: list[str] = []
    try:
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT"
                    " COUNT(*) FILTER (WHERE created_at >= %s) AS turns_today,"
                    " COUNT(*) FILTER (WHERE created_at >= %s) AS turns_7d,"
                    " COUNT(*) FILTER (WHERE status <> 'ok' AND created_at >= %s) AS errors_7d,"
                    " AVG(latency_ms) FILTER (WHERE created_at >= %s AND latency_ms IS NOT NULL) AS avg_latency_7d,"
                    " MAX(created_at) AS last_at"
                    # main card = the universal agent only; subagent turns (agent_slug set)
                    # belong to their own cards, so keep the counts unmixed here too.
                    " FROM bitrix_bot_interactions WHERE agent_slug IS NULL",
                    (day_start, week_start, week_start, week_start),
                )
                st = dict(cur.fetchone() or {})
                cur.execute("SELECT bitrix_user_id, display_name FROM agent_access ORDER BY bitrix_user_id")
                access_rows = cur.fetchall()
        names = _user_names()
        for r in access_rows:
            uid = int(r["bitrix_user_id"])
            users.append(names.get(uid, {}).get("name") or r["display_name"] or f"#{uid}")
    except Exception:  # noqa: BLE001
        logging.exception("agent_center agents failed")
        return jsonify({"error": "Не удалось загрузить агентов."}), 500
    avg_ms = st.get("avg_latency_7d")
    main_row = _agent_by_slug(MAIN_AGENT_SLUG)
    main_agent = {
        **_MAIN_AGENT_META,
        "name": _main_bot_name() or (main_row and main_row.get("name")) or _MAIN_AGENT_META["name"],
        "is_system": True,
        "is_active": True,
        "channels": ["Bitrix"],
        "users_count": len(users),
        "users_preview": ", ".join(users[:3]) + (f" +{len(users) - 3}" if len(users) > 3 else ""),
        "turns_today": int(st.get("turns_today") or 0),
        "turns_7d": int(st.get("turns_7d") or 0),
        "errors_7d": int(st.get("errors_7d") or 0),
        "avg_speed": f"{round(float(avg_ms) / 1000)} сек" if avg_ms else "—",
        "last_at": _when_label(st.get("last_at")),
    }
    agents = [main_agent]
    try:
        sub_stats: dict[str, dict[str, Any]] = {}
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT agent_slug,"
                    " COUNT(*) FILTER (WHERE created_at >= %s) AS turns_today,"
                    " COUNT(*) FILTER (WHERE created_at >= %s) AS turns_7d,"
                    " AVG(latency_ms) FILTER (WHERE created_at >= %s AND latency_ms IS NOT NULL) AS avg_ms,"
                    " MAX(created_at) AS last_at"
                    " FROM bitrix_bot_interactions WHERE agent_slug IS NOT NULL GROUP BY agent_slug",
                    (day_start, week_start, week_start),
                )
                for r in cur.fetchall():
                    sub_stats[r["agent_slug"]] = dict(r)
        names = _user_names()
        for a in _load_agents_full():
            if a["slug"] == MAIN_AGENT_SLUG:
                continue  # the universal agent is rendered as the main card above, not as a subagent
            ss = sub_stats.get(a["slug"], {})
            member_names = [names.get(uid, {}).get("name") or f"#{uid}" for uid in sorted(a["members"])]
            sub_avg = ss.get("avg_ms")
            # Канал агента = его мост. Телеграмный агент — тот же субагент с тем же редактором
            # возможностей, просто говорит через другой мессенджер.
            channels = (["Bitrix"] if a["bitrix_bot_id"] else []) + \
                       (["Telegram"] if a.get("has_telegram") else [])
            tg_handle = f"@{a['telegram_username']}" if a.get("telegram_username") else ""
            agents.append({
                "id": a["slug"],
                "name": a["name"],
                "kind": (f"Telegram {tg_handle} • {_LEVEL_LABELS.get(a['tier'], 'база знаний')}"
                         if a.get("has_telegram")
                         else f"субагент • {_LEVEL_LABELS.get(a['tier'], 'база знаний')}"),
                "icon": "box",
                "icon_bg": "bg-blue-100 text-blue-500",
                "is_system": False,
                "is_active": bool(a["is_active"]),
                "handle": tg_handle,
                "channels": channels,
                "users_count": len(member_names),
                "users_preview": ", ".join(member_names[:3]) + (f" +{len(member_names) - 3}" if len(member_names) > 3 else ""),
                "turns_today": int(ss.get("turns_today") or 0),
                "turns_7d": int(ss.get("turns_7d") or 0),
                "errors_7d": 0,
                "avg_speed": f"{round(float(sub_avg) / 1000)} сек" if sub_avg else "—",
                "last_at": _when_label(ss.get("last_at")),
            })
    except Exception:  # noqa: BLE001
        logging.exception("agent_center: subagents list failed")
    agents.extend(_telegram_agent_cards())
    return jsonify({"agents": agents})


def _telegram_agent_cards() -> list[dict[str, Any]]:
    """Карточки Telegram-агентов. Каналы разделены: у этих агентов нет ни Битрикса, ни
    bitrix_bot_interactions — их обороты считаются по журналу telegram_bot_messages."""
    day_start = msk_now().replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = day_start - timedelta(days=7)
    stats: dict[str, dict[str, Any]] = {}
    access: dict[str, list[str]] = {}
    try:
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT bot,"
                    " COUNT(*) FILTER (WHERE created_at >= %s) AS turns_today,"
                    " COUNT(*) FILTER (WHERE created_at >= %s) AS turns_7d,"
                    " COUNT(*) FILTER (WHERE status <> 'ok' AND created_at >= %s) AS errors_7d,"
                    " COUNT(DISTINCT dialog_id) AS dialogs,"
                    " MAX(created_at) AS last_at"
                    " FROM telegram_bot_messages GROUP BY bot",
                    (day_start, week_start, week_start))
                stats = {r["bot"]: dict(r) for r in cur.fetchall()}
                cur.execute("SELECT bot, username FROM telegram_bot_access WHERE is_active"
                            " ORDER BY username")
                for r in cur.fetchall():
                    access.setdefault(r["bot"], []).append("@" + str(r["username"]))
    except Exception:  # noqa: BLE001
        logging.exception("agent_center: telegram agent cards failed")
        return []
    cards = []
    for slug, meta in _tg_channel_meta().items():
        st = stats.get(slug, {})
        users = access.get(slug, [])
        cards.append({
            "id": slug,
            # Имя ровно как в Telegram: владелец ищет в кабинете то же, что видит в мессенджере.
            "name": meta["name"],
            "kind": f"Telegram • {meta['subtitle']}",
            "icon": "box",
            "icon_bg": "bg-sky-100 text-sky-500",
            "is_system": True,
            "is_active": True,
            "channels": ["Telegram"],
            "handle": meta["handle"],
            "users_count": len(users),
            "users_preview": ", ".join(users[:3]) + (f" +{len(users) - 3}" if len(users) > 3 else ""),
            "turns_today": int(st.get("turns_today") or 0),
            "turns_7d": int(st.get("turns_7d") or 0),
            "errors_7d": int(st.get("errors_7d") or 0),
            "avg_speed": "—",
            "last_at": _when_label(st.get("last_at")),
        })
    return cards


@app.get("/api/agent-center/tools")
def agent_center_tools():
    """The real MCP tool registry with legacy connector buckets and the core-toolset
    flag (the compact set the chat-bot runs on). Same lazy-import idiom the /mcp*
    HTTP handlers in app.py use."""
    try:
        from mcp.context_server import (
            CORE_TOOL_NAMES,
            FAQ_TOOL_NAMES,
            OPS_TOOL_NAMES,
            TOOLS,
        )
    except Exception:  # noqa: BLE001
        logging.exception("agent_center tools: context_server import failed")
        return jsonify({"error": "Не удалось загрузить список инструментов."}), 500
    tools = []
    for name, spec in TOOLS.items():
        desc = re.sub(r"\s+", " ", str(spec.get("description") or "")).strip()
        first_sentence = desc.split(". ")[0].strip()
        short = first_sentence if 0 < len(first_sentence) <= 200 else desc[:180].rstrip() + ("…" if len(desc) > 180 else "")
        tiers = ["admin"]
        if name in OPS_TOOL_NAMES:
            tiers.append("ops")
        if name in FAQ_TOOL_NAMES:
            tiers.append("faq")
        tools.append({
            "name": name,
            "description": short,
            "tiers": tiers,
            "core": name in CORE_TOOL_NAMES,
        })
    return jsonify({"tools": tools, "total": len(tools)})


# --- Monitoring (/api/agent-center/monitoring) ---------------------------------------------

_STARTED_MONO = time.monotonic()
_APP_DIR = Path(__file__).resolve().parent
_HEALTH_CACHE: dict[str, Any] = {"at": 0.0, "bitrix": None}
_GIT_CACHE: dict[str, Any] = {"at": 0.0, "head": "", "log": []}


def _uptime_label() -> str:
    total_min = int((time.monotonic() - _STARTED_MONO) // 60)
    days, rem = divmod(total_min, 1440)
    hours, minutes = divmod(rem, 60)
    if days:
        return f"{days} дн {hours} ч"
    if hours:
        return f"{hours} ч {minutes} мин"
    return f"{minutes} мин"


def _ago_label(dt: Any) -> str:
    if not dt:
        return "нет данных"
    minutes = int((msk_now() - dt.astimezone(MSK_TZ)).total_seconds() // 60)
    if minutes < 1:
        return "только что"
    if minutes < 60:
        return f"{minutes} мин назад"
    if minutes < 1440:
        return f"{minutes // 60} ч назад"
    return f"{minutes // 1440} дн назад"


def _event_time_label(dt: Any) -> str:
    if not dt:
        return ""
    local = dt.astimezone(MSK_TZ)
    return local.strftime("%H:%M") if local.date() == msk_now().date() else local.strftime("%d.%m %H:%M")


def _git_info() -> dict[str, Any]:
    """HEAD sha + recent commits for the deploy feed; cached 60s."""
    now = time.monotonic()
    if now - _GIT_CACHE["at"] < 60:
        return _GIT_CACHE
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=_APP_DIR,
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        raw = subprocess.run(
            ["git", "log", "-3", "--pretty=%ct|%h|%s"], cwd=_APP_DIR,
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        log = []
        for line in raw.splitlines():
            ts, sha, subject = line.split("|", 2)
            log.append({"at": datetime.fromtimestamp(int(ts), tz=timezone.utc), "sha": sha, "subject": subject})
        _GIT_CACHE.update(at=now, head=head, log=log)
    except Exception:  # noqa: BLE001
        logging.exception("agent_center: git info failed")
    return _GIT_CACHE


def _bitrix_ping_ms() -> int | None:
    """Light Bitrix REST liveness (server.time via the bot portal client), cached 60s."""
    now = time.monotonic()
    if now - _HEALTH_CACHE["at"] < 60:
        return _HEALTH_CACHE["bitrix"]
    ping: int | None = None
    try:
        from b24bot import _b24_testbot_call, b24_testbot_client
        t0 = time.perf_counter()
        _b24_testbot_call(b24_testbot_client(), "server.time", {})
        ping = int((time.perf_counter() - t0) * 1000)
    except Exception:  # noqa: BLE001
        logging.warning("agent_center: bitrix ping failed", exc_info=True)
    _HEALTH_CACHE.update(at=now, bitrix=ping)
    return ping


def _zoom_ping_ok() -> bool:
    """Zoom OAuth liveness (token issuance), cached 5 min — the cheapest signal
    that the integration credentials still work."""
    now = time.monotonic()
    cached = _HEALTH_CACHE.get("zoom")
    if cached is not None and now - _HEALTH_CACHE.get("zoom_at", 0.0) < 300:
        return cached
    ok = False
    try:
        from zoom import zoom_access_token
        ok = bool(zoom_access_token())
    except Exception:  # noqa: BLE001
        logging.warning("agent_center: zoom ping failed", exc_info=True)
    _HEALTH_CACHE.update(zoom=ok, zoom_at=now)
    return ok


def _server_memory() -> tuple[float, float] | None:
    try:
        info: dict[str, int] = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            parts = line.split()
            info[parts[0].rstrip(":")] = int(parts[1])
        total_gb = info["MemTotal"] / 1048576
        used_gb = (info["MemTotal"] - info["MemAvailable"]) / 1048576
        return used_gb, total_gb
    except Exception:  # noqa: BLE001
        return None


def _monitoring_agent_filter(agent: str | None) -> tuple[str, dict[str, Any]]:
    """WHERE-suffix isolating one agent's rows in bitrix_bot_interactions /
    bitrix_error_reports: 'main' → the universal agent (agent_slug IS NULL — same
    convention both tables use), a slug → that subagent, ''/'all'/None → no filter."""
    key = str(agent or "all").strip()
    if key in ("", "all"):
        return "", {}
    if key == MAIN_AGENT_SLUG:
        return " AND agent_slug IS NULL", {}
    return " AND agent_slug = %(agent_slug)s", {"agent_slug": key}


def monitoring_payload(chart_days: int = 1, agent: str = "all") -> dict[str, Any]:
    """Live monitoring snapshot; shared by the SPA endpoint, the agent's
    get_agent_monitoring MCP tool and the half-hourly health watchdog.
    `agent` scopes turn stats, the speed chart and the events feed to one bot
    ('all' = every agent together); system health is infrastructure shared by
    all agents and stays global in every view."""
    chart_days = min(max(int(chart_days or 1), 1), 90)
    flt, fparams = _monitoring_agent_filter(agent)
    now_msk = msk_now()
    today_start = now_msk.replace(hour=0, minute=0, second=0, microsecond=0)
    yday_start = today_start - timedelta(days=1)
    yday_same_time = now_msk - timedelta(days=1)
    day_ago = now_msk - timedelta(hours=24)
    chart_since = now_msk - timedelta(days=chart_days)
    week_ago = now_msk - timedelta(days=7)
    db_t0 = time.perf_counter()
    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT"
                " COUNT(*) FILTER (WHERE created_at >= %(today)s) AS turns_today,"
                " COUNT(*) FILTER (WHERE created_at >= %(yday)s AND created_at < %(yday_same)s) AS turns_yday_same,"
                " AVG(latency_ms) FILTER (WHERE created_at >= %(today)s AND status = 'ok') AS avg_today,"
                " AVG(latency_ms) FILTER (WHERE created_at >= %(yday)s AND created_at < %(today)s AND status = 'ok') AS avg_yday,"
                " COUNT(*) FILTER (WHERE created_at >= %(day_ago)s AND status <> 'ok') AS errors_24h,"
                " MAX(created_at) FILTER (WHERE status <> 'ok' AND created_at >= %(day_ago)s) AS last_error_at,"
                " COUNT(DISTINCT dialog_id) FILTER (WHERE created_at >= %(week_ago)s) AS dialogs_7d,"
                " MAX(created_at) AS last_turn_at,"
                " MAX(created_at) FILTER (WHERE status = 'ok') AS last_ok_at"
                " FROM bitrix_bot_interactions WHERE TRUE" + flt,
                {"today": today_start, "yday": yday_start, "yday_same": yday_same_time,
                 "day_ago": day_ago, "week_ago": week_ago, **fparams},
            )
            st = dict(cur.fetchone() or {})
            cur.execute(
                "SELECT created_at, latency_ms, status FROM bitrix_bot_interactions"
                " WHERE created_at >= %(since)s" + flt + " ORDER BY created_at",
                {"since": chart_since, **fparams},
            )
            turn_rows = cur.fetchall()
            cur.execute(
                "SELECT created_at, dialog_id, bitrix_user_id, error, latency_ms, status"
                " FROM bitrix_bot_interactions"
                " WHERE created_at >= %(since)s AND (status <> 'ok' OR latency_ms > 300000)"
                + flt + " ORDER BY id DESC LIMIT 12",
                {"since": week_ago, **fparams},
            )
            notable_rows = cur.fetchall()
            cur.execute(
                "SELECT created_at, reporter_name, report_text FROM bitrix_error_reports"
                " WHERE created_at >= %(since)s" + flt + " ORDER BY id DESC LIMIT 8",
                {"since": week_ago, **fparams},
            )
            report_rows = cur.fetchall()
            # Health is infrastructure (shared brain), so its freshness signal must stay
            # global even when cards/chart are scoped to one agent with few or no turns.
            global_last_ok = st.get("last_ok_at")
            if flt:
                cur.execute("SELECT MAX(created_at) FILTER (WHERE status = 'ok') AS m FROM bitrix_bot_interactions")
                global_last_ok = (cur.fetchone() or {}).get("m")
            zoom_last_at = drive_last_at = None
            try:
                cur.execute("SELECT MAX(synced_at) AS m FROM zoom_calls")
                zoom_last_at = (cur.fetchone() or {}).get("m")
                cur.execute("SELECT MAX(last_seen_at) AS m FROM company_drive_sources")
                drive_last_at = (cur.fetchone() or {}).get("m")
            except Exception:  # noqa: BLE001
                logging.warning("agent_center: zoom/drive freshness query failed", exc_info=True)
    db_ms = int((time.perf_counter() - db_t0) * 1000)

    # Minute-precision speed chart: every turn of the window is its own point.
    time_fmt = "%H:%M" if chart_days == 1 else "%d.%m %H:%M"
    chart = [
        {
            "time": r["created_at"].astimezone(MSK_TZ).strftime(time_fmt),
            "speed": round(int(r["latency_ms"]) / 1000) if r["latency_ms"] else 0,
            "error": r["status"] != "ok",
        }
        for r in turn_rows
    ]

    # Cards with day-over-day deltas.
    turns_today = int(st.get("turns_today") or 0)
    turns_yday_same = int(st.get("turns_yday_same") or 0)
    turns_delta = (
        f"{'▲ +' if turns_today >= turns_yday_same else '▼ '}{round((turns_today - turns_yday_same) / turns_yday_same * 100)}% к вчера"
        if turns_yday_same else "вчера к этому часу — 0"
    )
    avg_today = float(st["avg_today"]) / 1000 if st.get("avg_today") else None
    avg_yday = float(st["avg_yday"]) / 1000 if st.get("avg_yday") else None
    if avg_today is not None and avg_yday is not None:
        diff = round(avg_yday - avg_today)
        speed_delta = f"▲ быстрее на {diff} сек" if diff >= 0 else f"▼ медленнее на {-diff} сек"
    else:
        speed_delta = "вчера данных нет"
    errors_24h = int(st.get("errors_24h") or 0)
    cards = [
        {"label": "Ходов сегодня", "value": str(turns_today), "sub": turns_delta,
         "tone": "good" if turns_today >= turns_yday_same else "muted"},
        {"label": "Средняя скорость", "value": f"{round(avg_today)} сек" if avg_today is not None else "—",
         "sub": speed_delta, "tone": "good" if avg_today is not None and (avg_yday or 0) >= avg_today else "muted"},
        {"label": "Ошибки за 24 часа", "value": str(errors_24h),
         "sub": ("последняя " + _event_time_label(st.get("last_error_at"))) if errors_24h else "чисто ✨",
         "tone": "bad" if errors_24h else "good"},
        {"label": "Диалогов за 7 дней", "value": str(int(st.get("dialogs_7d") or 0)),
         "sub": "уникальных чатов с агентом", "tone": "muted"},
    ]

    # System health.
    health = [{"label": "База данных (PostgreSQL)", "status": f"ok • {db_ms} мс", "type": "ok"}]
    try:
        from mcp.context_server import TOOLS
        health.append({"label": f"MCP-инструменты ({len(TOOLS)})", "status": "зарегистрированы", "type": "ok"})
    except Exception:  # noqa: BLE001
        health.append({"label": "MCP-инструменты", "status": "модуль не загрузился", "type": "warn"})
    last_ok = global_last_ok
    ok_fresh = bool(last_ok and (now_msk - last_ok.astimezone(MSK_TZ)) < timedelta(hours=24))
    health.append({
        "label": "Мозг агента (Hermes)",
        "status": f"успешный ход {_ago_label(last_ok)}",
        "type": "ok" if ok_fresh else "warn",
    })
    bitrix_ms = _bitrix_ping_ms()
    health.append({
        "label": "Bitrix REST",
        "status": f"ok • {bitrix_ms / 1000:.1f} с".replace(".", ",") if bitrix_ms is not None else "не отвечает",
        "type": "ok" if bitrix_ms is not None else "warn",
    })
    zoom_ok = _zoom_ping_ok()
    zoom_fresh = bool(zoom_last_at and (now_msk - zoom_last_at.astimezone(MSK_TZ)) < timedelta(days=4))
    health.append({
        "label": "Zoom API",
        "status": (
            ("токен ok" if zoom_ok else "токен не выдаётся")
            + (f" • синк {_ago_label(zoom_last_at)}" if zoom_last_at else " • синков не было")
        ),
        "type": "ok" if zoom_ok and zoom_fresh else "warn",
    })
    drive_fresh = bool(drive_last_at and (now_msk - drive_last_at.astimezone(MSK_TZ)) < timedelta(days=2))
    health.append({
        "label": "Google Drive (документы)",
        "status": f"синк {_ago_label(drive_last_at)}" if drive_last_at else "синков не было",
        "type": "ok" if drive_fresh else "warn",
    })
    mem = _server_memory()
    if mem:
        used_gb, total_gb = mem
        health.append({
            "label": "Память сервера",
            "status": f"{used_gb:.1f} / {total_gb:.0f} ГБ".replace(".", ","),
            "type": "warn" if (total_gb - used_gb) < 0.3 else "ok",
        })

    # Events feed: errors + slow turns + user error-reports + deploys, newest first.
    names = _user_names()
    stamped: list[tuple[Any, dict[str, Any]]] = []
    for r in notable_rows:
        uid = int(r["bitrix_user_id"]) if r["bitrix_user_id"] is not None else None
        who = names.get(uid or -1, {}).get("name") or (f"#{uid}" if uid else "сотрудник")
        if r["status"] != "ok":
            err = re.sub(r"\s+", " ", (r["error"] or "ошибка без текста")).strip()[:140]
            text = f"Ошибка в диалоге {r['dialog_id']} ({who}): {err}"
            etype = "error"
        else:
            text = f"Медленный ход ({round((r['latency_ms'] or 0) / 60000)} мин) — диалог {r['dialog_id']} ({who})"
            etype = "info"
        stamped.append((r["created_at"], {"type": etype, "text": text}))
    for r in report_rows:
        text = f"«Ошибка/Предложение» от {r['reporter_name'] or 'сотрудника'}: " + re.sub(r"\s+", " ", r["report_text"]).strip()[:140]
        stamped.append((r["created_at"], {"type": "report", "text": text}))
    if not flt:  # deploys are system-wide — they belong to the "all agents" feed only
        for c in _git_info()["log"]:
            stamped.append((c["at"], {"type": "deploy", "text": f"Деплой: {c['subject']} ({c['sha']})"}))
    stamped.sort(key=lambda pair: pair[0], reverse=True)
    events = [
        {"time": _event_time_label(at), **payload}
        for at, payload in stamped[:20]
    ]
    if not events:
        events = [{"time": _event_time_label(now_msk), "type": "success", "text": "Событий нет — всё чисто"}]

    try:
        from b24bot import _HERMES_MAX_CONCURRENCY, _HERMES_RUN_SLOTS
        slots_total = _HERMES_MAX_CONCURRENCY
        slots_busy = max(0, slots_total - _HERMES_RUN_SLOTS._value)  # noqa: SLF001 — live gauge
    except Exception:  # noqa: BLE001
        slots_total, slots_busy = None, None

    return {
        "status": {
            "uptime": _uptime_label(),
            "last_turn": _ago_label(st.get("last_turn_at")),
            "slots_busy": slots_busy,
            "slots_total": slots_total,
            "version": _git_info()["head"],
        },
        "cards": cards,
        "chart": chart,
        "health": health,
        "events": events,
        "problems": [h["label"] + ": " + h["status"] for h in health if h["type"] != "ok"],
    }


@app.get("/api/agent-center/monitoring")
def agent_center_monitoring():
    try:
        chart_days = int(request.args.get("chart_days") or 1)
    except (TypeError, ValueError):
        chart_days = 1
    agent = str(request.args.get("agent") or "all").strip() or "all"
    try:
        return jsonify(monitoring_payload(chart_days, agent))
    except Exception:  # noqa: BLE001
        logging.exception("agent_center monitoring failed")
        return jsonify({"error": "Не удалось загрузить мониторинг."}), 500


# --- Health watchdog: proactive checks every 30 min with instant TG alerts ------------------
# The monitoring page only shows problems when someone looks at it; this loop looks at it
# FOR us (same health snapshot: DB, MCP, brain freshness, Bitrix, Zoom, Drive, RAM) and
# alerts the Albery notifications group the moment something goes red — plus one
# recovery message when everything is green again. Per-problem cooldown avoids spam.

_WATCHDOG_INTERVAL_S = int(os.getenv("AGENT_HEALTH_CHECK_INTERVAL_S", "1800"))
_WATCHDOG_COOLDOWN_S = int(os.getenv("AGENT_HEALTH_ALERT_COOLDOWN_S", "10800"))
_watchdog_last_alert: dict[str, float] = {}
_watchdog_had_problems = False


def _health_watchdog_once() -> None:
    global _watchdog_had_problems
    payload = monitoring_payload()
    problems = payload.get("problems") or []
    now = time.monotonic()
    fresh = [p for p in problems if now - _watchdog_last_alert.get(p, -_WATCHDOG_COOLDOWN_S) >= _WATCHDOG_COOLDOWN_S]
    from b24bot import _albery_tg_notify
    if fresh:
        for p in fresh:
            _watchdog_last_alert[p] = now
        text = "⚠️ Мониторинг Albery: проблемы\n" + "\n".join(f"— {p}" for p in problems) + \
            "\nДетали: /agent-monitoring (повторы приглушены на " + str(_WATCHDOG_COOLDOWN_S // 3600) + " ч)"
        ok, err = _albery_tg_notify(text)
        if not ok:
            logging.error("agent_center watchdog: alert delivery failed: %s", err)
    if problems:
        _watchdog_had_problems = True
    elif _watchdog_had_problems:
        _watchdog_had_problems = False
        _watchdog_last_alert.clear()
        ok, err = _albery_tg_notify("✅ Мониторинг Albery: все системы снова в норме")
        if not ok:
            logging.error("agent_center watchdog: recovery delivery failed: %s", err)


def _health_watchdog_loop() -> None:
    time.sleep(120)  # let the app finish booting before the first check
    while True:
        try:
            _health_watchdog_once()
        except Exception:  # noqa: BLE001
            logging.exception("agent_center: health watchdog check failed")
        time.sleep(_WATCHDOG_INTERVAL_S)


if os.getenv("AGENT_HEALTH_WATCHDOG", "1").strip() != "0":
    import threading

    threading.Thread(target=_health_watchdog_loop, daemon=True, name="agent-health-watchdog").start()


# --- Subagents ------------------------------------------------------------------------------
# A subagent = its own Bitrix bot (registered through the SAME local application via
# imbot.register — no new app needed), its own hermes connector (mcp_servers entry in
# /root/.hermes/config.yaml pointing at /mcp-agent/<slug>/<token>; the bot's CLI runs
# read the config fresh every turn, so no gateway restart), a member allowlist and a
# personal instruction store the agent extends itself (self-learning, scoped by the
# connector URL so an agent can only ever write to ITS OWN store).

_HERMES_CONFIG = Path(os.getenv("HERMES_CONFIG", "/root/.hermes/config.yaml"))
_AGENT_MCP_PUBLIC_BASE = os.getenv("AGENT_MCP_PUBLIC_BASE", "https://mcp.m4s.ru")
_AGENT_SELF_INSTRUCTIONS_MAX = int(os.getenv("AGENT_SELF_INSTRUCTIONS_MAX", "30"))
# 24k: the legal-contract skill alone is ~10k, and a silent cut here disabled the
# lawyer's whole formatting guide (2026-07-14). Truncation must never be silent.
_AGENT_INSTRUCTION_CHARS_MAX = 24000
_AGENT_CACHE: dict[str, Any] = {"at": 0.0, "by_bot": {}, "by_slug": {}}
_AGENT_COLORS = ("GREEN", "MINT", "PINK", "ORANGE", "PURPLE", "AQUA", "LIGHT_BLUE", "GRAY")

# Access levels are presets + the owner-only-tools gate (see _agent_allowed_pool), not a
# hard tool cap. 'developer' = может держать опасные admin-инструменты.
AGENT_LEVELS = ("faq", "ops", "developer")
_LEVEL_LABELS = {"faq": "база знаний", "ops": "все функции", "developer": "разработчик"}

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
    "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def _agent_slug(name: str) -> str:
    out = "".join(_TRANSLIT.get(ch, ch) for ch in name.lower())
    out = re.sub(r"[^a-z0-9]+", "-", out).strip("-")[:32]
    return out or "agent"


def _agent_cache_bust() -> None:
    _AGENT_CACHE.update(at=0.0, by_bot={}, by_slug={})


_REPO_ROOT = Path(__file__).resolve().parent


def registry_git_sync(message: str) -> None:
    """Best-effort commit+push of agent_knowledge/ so owner/self edits land on GitHub
    with history. Runs on the box (root + deploy key). Never raises — the edit is
    already saved on disk and enforced from there; git is for versioning/backup.
    Disabled unless REGISTRY_GIT_SYNC=1 (so local/dev never tries to push)."""
    if os.getenv("REGISTRY_GIT_SYNC", "0").strip() != "1":
        return

    def _git(*args: str, timeout: int = 45) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(_REPO_ROOT), *args],
            capture_output=True, text=True, timeout=timeout,
        )

    try:
        _git("add", "agent_knowledge")
        if not _git("status", "--porcelain", "agent_knowledge").stdout.strip():
            return  # nothing changed
        _git("commit", "-m", message)
        if _git("push", "origin", "HEAD:main", timeout=60).returncode != 0:
            # origin moved on (a code deploy) — rebase our small registry commit on top.
            _git("pull", "--rebase", "origin", "main", timeout=60)
            _git("push", "origin", "HEAD:main", timeout=60)
    except Exception:  # noqa: BLE001
        logging.warning("registry_git_sync failed (edit saved on disk, retried next change)", exc_info=True)


def resync_instructions_to_git() -> None:
    """Mirror the DB instruction tree into the git registry (scope-preserving) and push,
    so edits made via the app's instruction editor / upsert_ai_instruction take effect
    (the agent reads git). Best-effort, non-fatal; no-op if the registry is absent."""
    try:
        from agent_knowledge import registry_present, resync_instructions_to_git as _write
        if not registry_present():
            return
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    WITH RECURSIVE t AS (
                        SELECT id, parent_id, name, content, sort_order, ARRAY[name]::text[] AS path
                        FROM ai_instruction_folders WHERE parent_id IS NULL
                        UNION ALL
                        SELECT c.id, c.parent_id, c.name, c.content, c.sort_order, t.path || c.name
                        FROM ai_instruction_folders c JOIN t ON t.id = c.parent_id
                    )
                    SELECT id::text AS id, name, content, sort_order,
                           array_to_string(path, ' / ') AS path
                    FROM t ORDER BY path
                    """
                )
                rows = [dict(r) for r in cur.fetchall()]
        written, removed = _write(rows)
        if written or removed:
            registry_git_sync("instructions: sync edit from app/upsert to registry")
    except Exception:  # noqa: BLE001
        logging.exception("resync_instructions_to_git failed")


def _load_agents_full() -> list[dict[str, Any]]:
    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id::text AS id, slug, name, role_prompt, position, tier, tools, tools_customized,"
                " bitrix_bot_id, telegram_username, telegram_bot_user_id,"
                " telegram_bot_token IS NOT NULL AS has_telegram,"
                " mcp_token, is_active, color, created_at FROM agents ORDER BY created_at"
            )
            agents = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT agent_id::text AS agent_id, bitrix_user_id FROM agent_members")
            members = cur.fetchall()
            # Legacy DB personal instructions — used only as a fallback for agents that
            # have not been migrated to git files yet (see load_agent_learned).
            cur.execute(
                "SELECT agent_id::text AS agent_id, name, content, source, updated_at"
                " FROM agent_instructions ORDER BY created_at"
            )
            legacy_instructions = cur.fetchall()
    by_id = {a["id"]: a for a in agents}
    # Per-agent connections (instructions/skills) AND personal instructions live in the
    # GitHub registry, not the DB — the versioned source of truth the owner manages on
    # GitHub. Manifest = agent_knowledge/agents/<slug>.yaml; personal = .../learned/*.md.
    from agent_knowledge import load_agent_learned, load_manifest
    for a in agents:
        a["members"] = set()
        manifest = load_manifest(a["slug"])
        a["linked_instruction_ids"] = set(manifest["instructions"])
        a["linked_skill_ids"] = set(manifest["skills"])
        learned = load_agent_learned(a["slug"])
        a["instructions"] = learned if learned is not None else []
        a["_learned_from_files"] = learned is not None
    for m in members:
        if m["agent_id"] in by_id:
            by_id[m["agent_id"]]["members"].add(int(m["bitrix_user_id"]))
    # Fallback: agents not yet migrated to git files keep showing their DB rows.
    for i in legacy_instructions:
        a = by_id.get(i["agent_id"])
        if a and not a["_learned_from_files"]:
            a["instructions"].append({
                "id": None, "name": i["name"], "content": i["content"],
                "source": i["source"], "created_by": "", "created_at": "",
                "updated_by": "", "updated_at": i["updated_at"], "origin_dialog": "",
            })
    return agents


def agent_for_bot_id(bot_id: Any) -> dict[str, Any] | None:
    """Resolve an incoming Bitrix BOT_ID to a subagent profile (60s cache).
    Returns None for the main bot / unknown ids. Called by b24bot on every event."""
    key = str(bot_id or "").strip()
    if not key:
        return None
    now = time.monotonic()
    if now - _AGENT_CACHE["at"] > 60:
        try:
            agents = _load_agents_full()
            _AGENT_CACHE.update(
                at=now,
                by_bot={str(a["bitrix_bot_id"]): a for a in agents if a["bitrix_bot_id"]},
                by_slug={a["slug"]: a for a in agents},
            )
        except Exception:  # noqa: BLE001
            logging.exception("agent_center: agents cache reload failed")
    return _AGENT_CACHE["by_bot"].get(key)


def _agent_by_slug(slug: str) -> dict[str, Any] | None:
    agent_for_bot_id("0")  # refresh cache if stale
    return _AGENT_CACHE["by_slug"].get(slug)


# --- Universal (main) agent as a first-class configurable Agent ------------------------------
# The main Bitrix bot is modelled as an ordinary agent row (slug='main', level 'ops') so it
# shares the exact same editor and enforcement as the others — it is simply the broadest one.
# Its bitrix_bot_id stays NULL on purpose so agent_for_bot_id() never resolves the main bot to
# it (main-bot turns keep the general assistant prompt, not the specialised-subagent one); the
# main turn is instead routed to this agent's own connector when the universal mode is on.
MAIN_AGENT_SLUG = "main"


def ensure_main_agent() -> dict[str, Any] | None:
    """Idempotently make sure the universal (main) agent row + its /mcp-agent/main connector
    exist. Best-effort and non-fatal: a failure here must never break app boot or the live bot
    (the turn path falls back to the classic tier connectors). Touches only the DB and the
    Hermes config file — never b24bot — so it is import-order safe."""
    import secrets
    try:
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT mcp_token FROM agents WHERE slug = %s", (MAIN_AGENT_SLUG,))
                row = cur.fetchone()
                if row:
                    token = row["mcp_token"]
                else:
                    token = secrets.token_urlsafe(32)
                    cur.execute(
                        "INSERT INTO agents (slug, name, role_prompt, tier, mcp_token, color, is_active)"
                        " VALUES (%s, %s, %s, 'ops', %s, 'ORANGE', TRUE) ON CONFLICT (slug) DO NOTHING",
                        (MAIN_AGENT_SLUG, "Универсальный агент", "", token),
                    )
                    cur.execute("SELECT mcp_token FROM agents WHERE slug = %s", (MAIN_AGENT_SLUG,))
                    token = cur.fetchone()["mcp_token"]
        try:
            _hermes_connector_add(MAIN_AGENT_SLUG, token)
        except Exception:  # noqa: BLE001
            logging.exception("ensure_main_agent: hermes connector add failed")
        _agent_cache_bust()
        return _agent_by_slug(MAIN_AGENT_SLUG)
    except Exception:  # noqa: BLE001
        logging.exception("ensure_main_agent failed")
        return None


def universal_main_connector() -> str | None:
    """Toolset name for the universal main turn, or None if not ready (→ classic fallback).
    Ready = the row exists AND its connector line is present in the Hermes config, AND the
    UNIVERSAL_MAIN_AGENT flag is on. Read live each call so the flag can be flipped via env
    without a code deploy (safe rollout: deploy dormant → verify connector → flip → verify bot)."""
    if os.getenv("UNIVERSAL_MAIN_AGENT", "0").strip() != "1":
        return None
    agent = _agent_by_slug(MAIN_AGENT_SLUG)
    if not agent or not agent.get("is_active"):
        return None
    try:
        if f"  agent-{MAIN_AGENT_SLUG}:" not in _HERMES_CONFIG.read_text(encoding="utf-8"):
            return None
    except OSError:
        return None
    return f"agent-{MAIN_AGENT_SLUG}"


# --- Bitrix bot auto-registration (same local application, new CODE per agent) --------------

def _register_agent_bot(slug: str, name: str, color: str, position: str = "") -> Any:
    from b24bot import B24_APP_HANDLER_URL, _b24_app_access_token, _b24_app_call
    endpoint, access = _b24_app_access_token()
    if not endpoint or not access:
        raise RuntimeError("Нет OAuth-токенов приложения Bitrix (state пуст) — переустановите локальное приложение.")
    payload = {
        "CODE": f"albery_agent_{slug}"[:50].replace("-", "_"),
        "TYPE": "B",
        "OPENLINE": "N",
        "EVENT_MESSAGE_ADD": B24_APP_HANDLER_URL,
        "EVENT_WELCOME_MESSAGE": B24_APP_HANDLER_URL,
        "EVENT_BOT_DELETE": B24_APP_HANDLER_URL,
        "PROPERTIES": {"NAME": name, "COLOR": color, "WORK_POSITION": position or "ИИ-агент Albery"},
    }
    result = _b24_app_call(endpoint, access, "imbot.register", payload).get("result")
    if not result:
        raise RuntimeError("imbot.register вернул пустой результат")
    return result


def _unregister_agent_bot(bot_id: Any) -> None:
    if not bot_id:
        return
    from b24bot import _b24_app_access_token, _b24_app_call
    try:
        endpoint, access = _b24_app_access_token()
        _b24_app_call(endpoint, access, "imbot.unregister", {"BOT_ID": bot_id})
    except Exception:  # noqa: BLE001
        logging.exception("agent_center: imbot.unregister failed for bot %s", bot_id)


def _sync_bitrix_bot(bot_id: Any, name: str | None = None, position: str | None = None) -> None:
    """Push name and/or job title (должность) to the Bitrix bot so the messenger shows exactly
    what's set in the app — one source of truth. Only the provided fields are sent."""
    props: dict[str, Any] = {}
    if name is not None:
        props["NAME"] = name
    if position is not None:
        props["WORK_POSITION"] = position
    if not props:
        return
    from b24bot import _b24_app_access_token, _b24_app_call
    endpoint, access = _b24_app_access_token()
    if not (endpoint and access):
        raise RuntimeError("Нет OAuth-токенов приложения Bitrix — напишите боту любое сообщение и повторите.")
    _b24_app_call(endpoint, access, "imbot.update", {"BOT_ID": bot_id, "FIELDS": {"PROPERTIES": props}})


def _rename_bitrix_bot(bot_id: Any, name: str) -> None:
    _sync_bitrix_bot(bot_id, name=name)


def _main_bot_name() -> str | None:
    """The main bot's CURRENT display name straight from the portal (10-min cache)."""
    now = time.monotonic()
    if now - _AGENT_CACHE.get("main_name_at", 0.0) < 600:
        return _AGENT_CACHE.get("main_name")
    name = None
    try:
        from b24bot import _b24_load_state, _b24_testbot_call, b24_testbot_client
        bot_id = (_b24_load_state() or {}).get("bot_id")
        if bot_id:
            data = _b24_testbot_call(b24_testbot_client(), "user.get", {"ID": bot_id})
            users = data.get("result") or []
            if users and isinstance(users[0], dict):
                name = " ".join(p for p in (users[0].get("NAME"), users[0].get("LAST_NAME")) if p).strip() or None
            if not name:
                # Bots usually aren't returned by user.get — read the bot registry (its NAME
                # property is the messenger display name, e.g. «Агент Албери»).
                from b24bot import _b24_app_access_token, _b24_app_call
                ep, ac = _b24_app_access_token()
                if ep and ac:
                    res = _b24_app_call(ep, ac, "imbot.bot.list", {}).get("result")
                    entries = list(res.values()) if isinstance(res, dict) else (res or [])
                    for info in entries:
                        if not isinstance(info, dict):
                            continue
                        if str(info.get("ID") or info.get("BOT_ID") or "") != str(bot_id):
                            continue
                        props = info.get("PROPERTIES") if isinstance(info.get("PROPERTIES"), dict) else {}
                        name = (info.get("NAME") or props.get("NAME") or "").strip() or None
                        break
    except Exception:  # noqa: BLE001
        logging.warning("agent_center: main bot name lookup failed", exc_info=True)
    _AGENT_CACHE.update(main_name_at=now, main_name=name)
    return name


# --- Hermes connector management (textual config edit, comments preserved) ------------------

def _hermes_connector_add(slug: str, token: str) -> None:
    """Insert an mcp_servers entry `agent-<slug>` right after the `mcp_servers:` line.
    Textual edit (no yaml re-dump) keeps the hand-tuned config comments intact; the
    result is validated by parsing, with an automatic backup restore on failure."""
    if not _HERMES_CONFIG.exists():
        raise RuntimeError(f"Hermes config не найден: {_HERMES_CONFIG}")
    text = _HERMES_CONFIG.read_text(encoding="utf-8")
    marker = f"  agent-{slug}:"
    if marker in text:
        return
    lines = text.splitlines(keepends=True)
    insert_at = next((i + 1 for i, line in enumerate(lines) if line.rstrip() == "mcp_servers:"), None)
    if insert_at is None:
        raise RuntimeError("В hermes config нет секции mcp_servers")
    block = (
        f"  agent-{slug}:\n"
        f"    url: {_AGENT_MCP_PUBLIC_BASE}/mcp-agent/{slug}/{token}\n"
        f"    enabled: true\n"
        f"    timeout: 300\n"
    )
    backup = _HERMES_CONFIG.with_name(f"config.yaml.bak-agent-{slug}-{int(time.time())}")
    backup.write_text(text, encoding="utf-8")
    new_text = "".join(lines[:insert_at]) + block + "".join(lines[insert_at:])
    import yaml
    yaml.safe_load(new_text)  # validate before touching the live file
    _HERMES_CONFIG.write_text(new_text, encoding="utf-8")


def _hermes_connector_remove(slug: str) -> None:
    try:
        text = _HERMES_CONFIG.read_text(encoding="utf-8")
    except OSError:
        return
    lines = text.splitlines(keepends=True)
    out, skipping = [], False
    for line in lines:
        if line.rstrip() == f"  agent-{slug}:":
            skipping = True
            continue
        if skipping:
            if line.startswith("    ") or not line.strip():
                continue
            skipping = False
        out.append(line)
    new_text = "".join(out)
    if new_text != text:
        import yaml
        yaml.safe_load(new_text)
        _HERMES_CONFIG.write_text(new_text, encoding="utf-8")


# --- Subagent CRUD API ----------------------------------------------------------------------

@app.post("/api/agent-center/agents")
def agent_center_create_agent():
    import secrets
    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "").strip()
    if not name or len(name) > 60:
        return jsonify({"error": "Укажите имя агента (до 60 символов)."}), 400
    # No level selector any more: a new agent starts with the broad 'ops' preset (все функции,
    # без admin); the owner then narrows/extends its tools in the capability panel.
    # Канал агента задаётся мостом: есть токен бота — агент живёт в Telegram, нет — в Битриксе.
    # Токен проверяем ДО создания записи: битый токен не должен оставлять мёртвого агента.
    tg_token = str(body.get("telegram_bot_token") or "").strip()
    tg_who: dict[str, Any] = {}
    if tg_token:
        import tg_multi
        try:
            tg_who = tg_multi.describe(tg_token)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": f"Telegram не принял токен: {str(exc)[:200]}"}), 400
        if not tg_who.get("username"):
            return jsonify({"error": "Telegram не вернул @username бота — проверьте токен."}), 400
    tier = str(body.get("tier") or "ops").strip()
    if tier not in AGENT_LEVELS:
        tier = "ops"
    role_prompt = str(body.get("role_prompt") or "").strip()[:4000]
    position = str(body.get("position") or "").strip()[:100] or "ИИ-агент Albery"
    members = [int(m) for m in (body.get("members") or []) if str(m).strip().isdigit()]
    slug = _agent_slug(name)
    token = secrets.token_urlsafe(32)
    try:
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM agents WHERE slug = %s", (slug,))
                if cur.fetchone():
                    return jsonify({"error": f"Агент со slug «{slug}» уже есть — назовите иначе."}), 409
                cur.execute("SELECT COUNT(*) AS n FROM agents")
                color = _AGENT_COLORS[int(cur.fetchone()["n"]) % len(_AGENT_COLORS)]
                cur.execute(
                    "INSERT INTO agents (slug, name, role_prompt, position, tier, mcp_token, color)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id::text AS id",
                    (slug, name, role_prompt, position, tier, token, color),
                )
                agent_id = cur.fetchone()["id"]
                for uid in members:
                    cur.execute(
                        "INSERT INTO agent_members (agent_id, bitrix_user_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                        (agent_id, uid),
                    )
    except Exception:  # noqa: BLE001
        logging.exception("agent create: db insert failed")
        return jsonify({"error": "Не удалось сохранить агента."}), 500

    warnings = []
    bot_id = None
    if tg_token:
        # Телеграмный мост вместо битриксового. Всё остальное у агента такое же: свой коннектор
        # agent-<slug>, набор инструментов, инструкции и знания — редактор возможностей общий.
        try:
            with pg_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE agents SET telegram_bot_token = %s, telegram_username = %s,"
                        " telegram_bot_user_id = %s, updated_at = now() WHERE id = %s",
                        (tg_token, tg_who.get("username"), tg_who.get("bot_user_id"), agent_id))
        except Exception as exc:  # noqa: BLE001
            logging.exception("agent create: telegram bridge failed")
            warnings.append(f"Telegram-мост не сохранён: {str(exc)[:200]}")
    else:
        try:
            bot_id = _register_agent_bot(slug, name, color, position)
            with pg_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE agents SET bitrix_bot_id = %s, updated_at = now() WHERE id = %s", (bot_id, agent_id))
        except Exception as exc:  # noqa: BLE001
            logging.exception("agent create: bitrix bot registration failed")
            warnings.append(f"Bitrix-бот не зарегистрирован: {str(exc)[:200]}")
    try:
        _hermes_connector_add(slug, token)
    except Exception as exc:  # noqa: BLE001
        logging.exception("agent create: hermes connector add failed")
        warnings.append(f"Hermes-коннектор не добавлен: {str(exc)[:200]}")
    _agent_cache_bust()
    return jsonify({"ok": True, "slug": slug, "bitrix_bot_id": bot_id, "warnings": warnings})


def _main_access_member_ids() -> list[int]:
    """The universal agent's team = people with an explicit non-'none' agent_access grant."""
    out: list[int] = []
    try:
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT bitrix_user_id FROM agent_access WHERE tier <> 'none' ORDER BY bitrix_user_id")
                out = [int(r["bitrix_user_id"]) for r in cur.fetchall()]
    except Exception:  # noqa: BLE001
        logging.exception("main access members load failed")
    return out


# --- Management surface for a top-access agent (wrapped by MCP tools; see context_server) ----

def resolve_bitrix_user(name_or_id: Any) -> tuple[int | None, str]:
    """Resolve a Bitrix user name (or numeric id) to (id, display_name). Name match is a
    case-insensitive substring against the portal directory; ambiguous/none → (None, input)."""
    s = str(name_or_id or "").strip()
    if not s:
        return None, s
    names = _user_names()
    if s.isdigit():
        uid = int(s)
        return uid, names.get(uid, {}).get("name") or f"#{uid}"
    matches = [(uid, info.get("name") or "") for uid, info in names.items()
               if s.lower() in (info.get("name") or "").lower()]
    if len(matches) == 1:
        return matches[0][0], matches[0][1]
    # Prefer an exact (case-insensitive) full-name hit if the substring was ambiguous.
    exact = [m for m in matches if (m[1] or "").lower() == s.lower()]
    if len(exact) == 1:
        return exact[0]
    return None, s


def mgmt_list_agents() -> dict[str, Any]:
    """Every agent (universal + subagents) with its live config summary — for a managing agent."""
    names = _user_names()
    out = []
    for a in _load_agents_full():
        is_main = a["slug"] == MAIN_AGENT_SLUG
        member_ids = _main_access_member_ids() if is_main else sorted(a["members"])
        out.append({
            "slug": a["slug"],
            "name": a["name"],
            "position": a.get("position") or "",
            "kind": "универсальный" if is_main else "субагент",
            "is_active": bool(a["is_active"]),
            "tools_enabled": len(_agent_tool_names(a)),
            "instructions_linked": len(a.get("linked_instruction_ids") or set()),
            "skills_linked": len(a.get("linked_skill_ids") or set()),
            "team": [names.get(uid, {}).get("name") or f"#{uid}" for uid in member_ids],
        })
    return {"agents": out, "count": len(out)}


@app.get("/api/agent-center/agents/<slug>")
def agent_center_agent_detail(slug: str):
    display_bot_id: Any = None
    if slug == MAIN_AGENT_SLUG:
        ensure_main_agent()
        # The main bot pre-exists in Bitrix — its name/id there are the source of truth. Adopt the
        # live name into the row (so app == Bitrix, e.g. «Агент Албери») and show the real bot id
        # (the row keeps bitrix_bot_id NULL on purpose, so agent_for_bot_id never treats the main
        # bot as a subagent). No register button then — the main bot is already registered.
        live_name = _main_bot_name()
        try:
            from b24bot import _b24_load_state
            display_bot_id = (_b24_load_state() or {}).get("bot_id")
        except Exception:  # noqa: BLE001
            logging.warning("main detail: bot id lookup failed", exc_info=True)
        if live_name:
            try:
                with pg_connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute("UPDATE agents SET name = %s, updated_at = now()"
                                    " WHERE slug = %s AND name <> %s", (live_name, MAIN_AGENT_SLUG, live_name))
                _agent_cache_bust()
            except Exception:  # noqa: BLE001
                logging.exception("main detail: name self-heal failed")
    agent = _agent_by_slug(slug)
    if not agent:
        return jsonify({"error": "Агент не найден."}), 404
    names = _user_names()
    # The universal agent's team lives in agent_access (company-wide); subagents' in agent_members.
    member_ids = _main_access_member_ids() if slug == MAIN_AGENT_SLUG else sorted(agent["members"])
    return jsonify({
        "slug": agent["slug"],
        "name": agent["name"],
        "position": agent.get("position") or "",
        "role_prompt": agent["role_prompt"],
        "tier": agent["tier"],
        "is_main": slug == MAIN_AGENT_SLUG,
        "is_active": agent["is_active"],
        "bitrix_bot_id": display_bot_id if slug == MAIN_AGENT_SLUG else agent["bitrix_bot_id"],
        "members": [
            {"id": uid, "name": names.get(uid, {}).get("name") or f"#{uid}"}
            for uid in member_ids
        ],
        "instructions": [
            {"id": i["id"], "name": i["name"], "content": i["content"], "source": i["source"],
             "created_by": i.get("created_by") or "",
             "updated_by": i.get("updated_by") or "",
             "origin_dialog": i.get("origin_dialog") or "",
             "created": _when_label(i.get("created_at")),
             "updated": _when_label(i.get("updated_at"))}
            for i in agent["instructions"]
        ],
    })


@app.patch("/api/agent-center/agents/<slug>")
def agent_center_agent_update(slug: str):
    body = request.get_json(silent=True) or {}
    if slug == MAIN_AGENT_SLUG:
        # The universal agent is a real row now; persist its editable fields (name/role
        # prompt/active) AND, on a name change, rename the actual Bitrix bot. Tier is fixed
        # to 'ops' (no admin) and members are managed via agent_access, not here.
        ensure_main_agent()
        warnings: list[str] = []
        try:
            with pg_connect() as conn:
                with conn.cursor() as cur:
                    new_name = str(body.get("name") or "").strip()[:60] if "name" in body else None
                    new_pos = str(body.get("position") or "").strip()[:100] if "position" in body else None
                    if new_name:
                        cur.execute("UPDATE agents SET name = %s, updated_at = now() WHERE slug = %s",
                                    (new_name, MAIN_AGENT_SLUG))
                    if new_pos is not None:
                        cur.execute("UPDATE agents SET position = %s, updated_at = now() WHERE slug = %s",
                                    (new_pos, MAIN_AGENT_SLUG))
                    if new_name or new_pos is not None:
                        try:
                            from b24bot import _b24_load_state
                            main_bot_id = (_b24_load_state() or {}).get("bot_id")
                            if main_bot_id:
                                _sync_bitrix_bot(main_bot_id, name=new_name, position=new_pos)
                        except Exception as exc:  # noqa: BLE001
                            logging.exception("main agent bitrix sync failed")
                            warnings.append(f"Имя/должность в Bitrix не обновились: {str(exc)[:160]}")
                    if "role_prompt" in body:
                        cur.execute("UPDATE agents SET role_prompt = %s, updated_at = now() WHERE slug = %s",
                                    (str(body["role_prompt"] or "").strip()[:4000], MAIN_AGENT_SLUG))
                    if "is_active" in body:
                        cur.execute("UPDATE agents SET is_active = %s, updated_at = now() WHERE slug = %s",
                                    (bool(body["is_active"]), MAIN_AGENT_SLUG))
                    # The universal agent's team lives in agent_access: grant the listed users
                    # (все функции) and drop the rows of those removed (back to default).
                    if isinstance(body.get("members"), list):
                        want = {int(m) for m in body["members"] if str(m).strip().isdigit()}
                        cur.execute("SELECT bitrix_user_id FROM agent_access WHERE tier <> 'none'")
                        have = {int(r["bitrix_user_id"]) for r in cur.fetchall()}
                        for uid in want - have:
                            cur.execute(
                                "INSERT INTO agent_access (bitrix_user_id, tier) VALUES (%s, 'ops')"
                                " ON CONFLICT (bitrix_user_id) DO UPDATE SET tier = 'ops'",
                                (uid,),
                            )
                        for uid in have - want:
                            cur.execute("DELETE FROM agent_access WHERE bitrix_user_id = %s", (uid,))
        except Exception:  # noqa: BLE001
            logging.exception("main agent update failed")
            return jsonify({"error": "Не удалось сохранить изменения."}), 500
        _AGENT_CACHE.update(main_name_at=0.0)
        _agent_cache_bust()
        return jsonify({"ok": True, "warnings": warnings})
    agent = _agent_by_slug(slug)
    if not agent:
        return jsonify({"error": "Агент не найден."}), 404
    warnings = []
    try:
        with pg_connect() as conn:
            with conn.cursor() as cur:
                new_name = str(body.get("name") or "").strip()[:60] if ("name" in body and str(body.get("name")).strip()) else None
                new_pos = str(body.get("position") or "").strip()[:100] if "position" in body else None
                if new_name:
                    cur.execute("UPDATE agents SET name = %s, updated_at = now() WHERE slug = %s",
                                (new_name, slug))
                if new_pos is not None:
                    cur.execute("UPDATE agents SET position = %s, updated_at = now() WHERE slug = %s",
                                (new_pos, slug))
                # Push name/должность to the Bitrix bot so app and messenger match exactly.
                sync_name = new_name if (new_name and new_name != agent["name"]) else None
                sync_pos = new_pos if (new_pos is not None and new_pos != (agent.get("position") or "")) else None
                if agent.get("bitrix_bot_id") and (sync_name or sync_pos):
                    try:
                        _sync_bitrix_bot(agent["bitrix_bot_id"], name=sync_name, position=sync_pos)
                    except Exception as exc:  # noqa: BLE001
                        logging.exception("agent name/position sync in bitrix failed")
                        warnings.append(f"Имя/должность в Bitrix не обновились: {str(exc)[:160]}")
                if "role_prompt" in body:
                    cur.execute("UPDATE agents SET role_prompt = %s, updated_at = now() WHERE slug = %s",
                                (str(body["role_prompt"] or "").strip()[:4000], slug))
                if "tier" in body and str(body["tier"]) in AGENT_LEVELS:
                    cur.execute("UPDATE agents SET tier = %s, updated_at = now() WHERE slug = %s",
                                (str(body["tier"]), slug))
                if "is_active" in body:
                    cur.execute("UPDATE agents SET is_active = %s, updated_at = now() WHERE slug = %s",
                                (bool(body["is_active"]), slug))
                if isinstance(body.get("members"), list):
                    members = [int(m) for m in body["members"] if str(m).strip().isdigit()]
                    cur.execute("DELETE FROM agent_members WHERE agent_id = (SELECT id FROM agents WHERE slug = %s)", (slug,))
                    for uid in members:
                        cur.execute(
                            "INSERT INTO agent_members (agent_id, bitrix_user_id)"
                            " SELECT id, %s FROM agents WHERE slug = %s ON CONFLICT DO NOTHING",
                            (uid, slug),
                        )
    except Exception:  # noqa: BLE001
        logging.exception("agent update failed")
        return jsonify({"error": "Не удалось сохранить изменения."}), 500
    _agent_cache_bust()
    return jsonify({"ok": True, "warnings": warnings})


@app.post("/api/agent-center/agents/<slug>/register-bot")
def agent_center_agent_register_bot(slug: str):
    """Retry Bitrix bot registration for an agent created while the app tokens were
    unavailable; also re-ensures the hermes connector (both are idempotent)."""
    agent = _agent_by_slug(slug)
    if not agent:
        return jsonify({"error": "Агент не найден."}), 404
    warnings = []
    bot_id = agent.get("bitrix_bot_id")
    if not bot_id:
        try:
            bot_id = _register_agent_bot(slug, agent["name"], agent.get("color") or "GREEN", agent.get("position") or "")
            with pg_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE agents SET bitrix_bot_id = %s, updated_at = now() WHERE slug = %s",
                                (bot_id, slug))
        except Exception as exc:  # noqa: BLE001
            logging.exception("agent register-bot retry failed")
            return jsonify({"error": f"Регистрация не удалась: {str(exc)[:200]}"}), 502
    try:
        _hermes_connector_add(slug, agent["mcp_token"])
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"Hermes-коннектор: {str(exc)[:160]}")
    _agent_cache_bust()
    return jsonify({"ok": True, "bitrix_bot_id": bot_id, "warnings": warnings})


@app.delete("/api/agent-center/agents/<slug>")
def agent_center_agent_delete(slug: str):
    if slug == MAIN_AGENT_SLUG:
        return jsonify({"error": "Универсальный (основной) агент удалить нельзя."}), 400
    agent = _agent_by_slug(slug)
    if not agent:
        return jsonify({"error": "Агент не найден."}), 404
    _unregister_agent_bot(agent.get("bitrix_bot_id"))
    try:
        _hermes_connector_remove(slug)
    except Exception:  # noqa: BLE001
        logging.exception("agent delete: hermes connector remove failed")
    try:
        with pg_connect() as conn:
            with conn.cursor() as cur:
                # Automations go with the agent — an orphaned row would fire forever
                # into "агент не найден" errors.
                cur.execute("DELETE FROM agent_automations WHERE agent_slug = %s", (slug,))
                cur.execute("DELETE FROM agents WHERE slug = %s", (slug,))
    except Exception:  # noqa: BLE001
        logging.exception("agent delete failed")
        return jsonify({"error": "Не удалось удалить агента."}), 500
    _agent_cache_bust()
    return jsonify({"ok": True})


@app.post("/api/agent-center/agents/<slug>/instructions")
def agent_center_instruction_add(slug: str):
    agent = _agent_by_slug(slug)
    if not agent:
        return jsonify({"error": "Агент не найден."}), 404
    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "").strip()[:80]
    content = str(body.get("content") or "").strip()[:_AGENT_INSTRUCTION_CHARS_MAX]
    if not name or not content:
        return jsonify({"error": "Нужны name и content."}), 400
    try:
        from agent_knowledge import save_agent_learned
        save_agent_learned(slug, name, content, source="owner", actor="владелец (панель)")
    except Exception:  # noqa: BLE001
        logging.exception("agent instruction add failed")
        return jsonify({"error": "Не удалось сохранить инструкцию."}), 500
    registry_git_sync(f"agent {slug}: owner instruction «{name}»")
    _agent_cache_bust()
    return jsonify({"ok": True})


@app.post("/api/agent-center/agents/<slug>/instructions/<inst_id>/promote")
def agent_center_instruction_promote(slug: str, inst_id: str):
    """Promote a personal instruction into the shared library (optional instruction),
    so it can be connected to other agents. The original personal one stays."""
    try:
        from agent_knowledge import promote_learned_to_library
        result = promote_learned_to_library(slug, inst_id)
    except Exception:  # noqa: BLE001
        logging.exception("promote instruction failed")
        return jsonify({"error": "Не удалось повысить до общих."}), 500
    if not result:
        return jsonify({"error": "Личная инструкция не найдена."}), 404
    registry_git_sync(f"promote {slug}/{inst_id} -> library «{result['name']}»")
    _agent_cache_bust()
    return jsonify({"ok": True, **result})


@app.delete("/api/agent-center/agents/<slug>/instructions/<inst_id>")
def agent_center_instruction_delete(slug: str, inst_id: str):
    # inst_id is the file slug (== _safe_component(name)); owner may delete any source.
    try:
        from agent_knowledge import delete_agent_learned
        delete_agent_learned(slug, inst_id, only_self=False)
    except Exception:  # noqa: BLE001
        logging.exception("agent instruction delete failed")
        return jsonify({"error": "Не удалось удалить."}), 500
    registry_git_sync(f"agent {slug}: delete instruction {inst_id}")
    _agent_cache_bust()
    return jsonify({"ok": True})


# --- Per-agent config: tools / library instructions / skills --------------------------------
# The single place the owner shapes an agent's capability surface. Every toggle here has
# real backend teeth: tool toggles change what the connector serves (invisible when off);
# instruction/skill selections change exactly what is injected into the agent's turn (an
# agent cannot apply a library doc it is not linked to). A fixed baseline (mandatory tools)
# stays on regardless — that is the "settings that don't change" the owner asked for.

def _library_instructions() -> list[dict[str, Any]]:
    """Instruction library — the pool an agent can be given, same source the knowledge
    page shows. Source of truth is the GitHub registry (agent_knowledge/instructions);
    falls back to the legacy DB table (ai_instruction_folders) when the registry is
    absent, so the switch to git is safe. ``id`` is the folder path in registry mode
    (stable, human-readable) and the DB uuid in fallback mode."""
    from agent_knowledge import load_instructions  # lazy: no import cycle

    reg = load_instructions()
    if reg is not None:
        out: list[dict[str, Any]] = []
        for i in reg:
            content = re.sub(r"\s+", " ", (i["content"] or "")).strip()
            if not content:
                continue
            out.append({
                "id": i["id"],
                "title": i["name"],
                "parent": i["parent"],
                "content": content,
                "scope": i.get("scope") or "universal",
                "updated_at": i.get("updated_at"),
                "description": (content[:160].rstrip() + "…") if len(content) > 160 else content,
            })
        return out
    out = []
    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT f.id::text AS id, f.name, p.name AS parent_name, f.content, f.updated_at"
                " FROM ai_instruction_folders f"
                " LEFT JOIN ai_instruction_folders p ON p.id = f.parent_id"
                " ORDER BY f.sort_order, f.name"
            )
            for r in cur.fetchall():
                content = re.sub(r"\s+", " ", (r["content"] or "")).strip()
                if not content:
                    continue
                out.append({
                    "id": r["id"],
                    "title": r["name"],
                    "parent": r["parent_name"] or "",
                    "content": content,
                    "scope": "universal",
                    "updated_at": r["updated_at"],
                    "description": (content[:160].rstrip() + "…") if len(content) > 160 else content,
                })
    return out


@app.get("/api/agent-center/agents/<slug>/config")
def agent_center_agent_config(slug: str):
    """The agent's full capability surface for the constructor: every registry tool
    with its on/off + fixed state, and the whole instruction/skill library with
    what's selected."""
    agent = _agent_by_slug(slug)
    if not agent:
        return jsonify({"error": "Агент не найден."}), 404
    try:
        from mcp.context_server import (
            CORE_TOOL_NAMES,
            FAQ_TOOL_NAMES,
            OPS_TOOL_NAMES,
            OWNER_ONLY_TOOL_NAMES,
            TOOLS,
        )
    except Exception:  # noqa: BLE001
        logging.exception("agent config: context_server import failed")
        return jsonify({"error": "Не удалось загрузить инструменты."}), 500
    pool = _agent_allowed_pool(agent)          # what this agent may hold
    enabled = _agent_tool_names(agent)          # effective set the connector serves
    fixed = MANDATORY_AGENT_TOOLS & pool
    # The whole registry is available to the constructor; legacy tier is a preset, not a cap.
    tools = []
    for name in sorted(TOOLS):
        spec = TOOLS.get(name, {})
        desc = re.sub(r"\s+", " ", str(spec.get("description") or "")).strip()
        first = desc.split(". ")[0].strip()
        short = first if 0 < len(first) <= 200 else desc[:180].rstrip() + ("…" if len(desc) > 180 else "")
        # Privilege class of the tool itself (for chips/lock): admin = owner-only/dangerous.
        if name in OWNER_ONLY_TOOL_NAMES:
            cls = "admin"
        elif name in FAQ_TOOL_NAMES:
            cls = "faq"
        else:
            cls = "ops"
        tiers = ["admin"] + (["ops"] if name in OPS_TOOL_NAMES else []) + (["faq"] if name in FAQ_TOOL_NAMES else [])
        tools.append({
            "name": name, "description": short, "tiers": tiers, "class": cls,
            "core": name in CORE_TOOL_NAMES,
            "fixed": name in fixed, "enabled": name in enabled,
            "allowed": name in pool,
        })
    sel_instr = agent.get("linked_instruction_ids") or set()
    # scope: universal instructions go to EVERY agent (checkbox locked on); optional
    # ones are connected per-agent (real teeth via start_here scoping).
    instructions = [
        {"id": i["id"], "title": i["title"], "parent": i["parent"],
         "description": i["description"], "scope": i.get("scope") or "universal",
         "selected": (i.get("scope") == "universal") or (i["id"] in sel_instr)}
        for i in _library_instructions()
    ]
    sel_skills = agent.get("linked_skill_ids") or set()
    skills = [
        {"id": s["id"], "title": s["title"], "parent": s.get("parent") or "",
         "description": s["description"], "custom": s.get("custom", False),
         "kind": s.get("kind") or "shared",
         "selected": s["id"] in sel_skills}
        for s in _hermes_skills()
    ]
    return jsonify({
        "slug": slug,
        "tier": agent["tier"],
        "tools_customized": bool(agent.get("tools_customized")),
        "tools": tools,
        "tools_total": len(TOOLS),
        "instructions": instructions,
        "skills": skills,
    })


@app.put("/api/agent-center/agents/<slug>/config")
def agent_center_agent_config_save(slug: str):
    """Persist the constructor state: which tools are enabled (mandatory ones are
    always kept), and which library instructions/skills are linked. Values are
    validated against the real registry / library so stale names are dropped."""
    agent = _agent_by_slug(slug)
    if not agent:
        return jsonify({"error": "Агент не найден."}), 404
    body = request.get_json(silent=True) or {}
    pool = _agent_allowed_pool(agent)
    fixed = MANDATORY_AGENT_TOOLS & pool
    # Tools: keep only real registry names; the mandatory baseline is always included.
    requested_tools = {str(t) for t in (body.get("tools") or []) if str(t)}
    enabled_tools = sorted((requested_tools & pool) | fixed)
    # Only OPTIONAL instructions are per-agent connections; universal ones are always
    # on and never stored in a manifest. Keep the manifest to real, meaningful links.
    optional_instr = {i["id"] for i in _library_instructions() if (i.get("scope") or "universal") == "optional"}
    valid_skills = {s["id"] for s in _hermes_skills()}
    instr_ids = sorted({str(x) for x in (body.get("instructions") or [])} & optional_instr)
    skill_ids = sorted({str(x) for x in (body.get("skills") or [])} & valid_skills)
    try:
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM agents WHERE slug = %s", (slug,))
                row = cur.fetchone()
                if not row:
                    return jsonify({"error": "Агент не найден."}), 404
                agent_id = row["id"]
                # Tools stay operational config in the DB (already enforced per-connector).
                cur.execute(
                    "UPDATE agents SET tools = %s, tools_customized = TRUE, updated_at = now() WHERE id = %s",
                    (enabled_tools, agent_id),
                )
        # Instruction/skill connections -> GitHub registry manifest (versioned source of
        # truth). Commit+push so the change is on GitHub with history.
        from agent_knowledge import save_manifest
        save_manifest(slug, instr_ids, skill_ids)
    except Exception:  # noqa: BLE001
        logging.exception("agent config save failed")
        return jsonify({"error": "Не удалось сохранить настройки."}), 500
    registry_git_sync(f"agent {slug}: update connected instructions/skills")
    _agent_cache_bust()
    return jsonify({"ok": True, "tools": enabled_tools, "instructions": instr_ids, "skills": skill_ids})


def agent_selected_knowledge(agent: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Resolve an agent's linked library items to injectable content for its turn.
    Returns {instructions: [{title, content}], skills: [{title, description}]}. Only
    linked items are returned — this is what makes the selection a real capability
    boundary rather than a cosmetic list."""
    skill_ids = agent.get("linked_skill_ids") or set()
    # Instructions are delivered through the SCOPED start_here / get_ai_instructions
    # tools (universal + this agent's connected optional ones), the single instruction
    # channel for every agent (main included) — so they are NOT injected here again to
    # avoid duplicating them in the prompt. Skills, which start_here does not carry,
    # are injected below.
    instructions: list[dict[str, Any]] = []
    skills: list[dict[str, Any]] = []
    if skill_ids:
        from agent_knowledge import load_skill_content
        for s in _hermes_skills():
            if s["id"] in skill_ids:
                entry = {
                    "title": (f"{s['parent']} / {s['title']}" if s.get("parent") else s["title"]),
                    "description": s["description"],
                }
                # Custom (shared) registry skills are NOT loaded by the Hermes gateway —
                # inject their full body, or the model only ever sees the description line.
                if s.get("custom"):
                    content = load_skill_content(s["id"])
                    if content:
                        if len(content) > _AGENT_INSTRUCTION_CHARS_MAX:
                            logging.warning(
                                "agent skill %s is %d chars — TRUNCATED to %d, the tail is invisible "
                                "to the agent", s["id"], len(content), _AGENT_INSTRUCTION_CHARS_MAX)
                        entry["content"] = content[:_AGENT_INSTRUCTION_CHARS_MAX]
                skills.append(entry)
    return {"instructions": instructions, "skills": skills}


# --- Per-agent MCP endpoint (/mcp-agent/<slug>/<token>) with self-learning ------------------
# The agent's ONLY connector. Tool scope = its exact enabled whitelist, PLUS
# self-learning tools handled RIGHT HERE with the slug from the URL — so an agent
# can read/write exclusively its own instruction store, never global instructions,
# never another agent's.

_SELF_TOOL_SPECS: dict[str, dict[str, Any]] = {
    "upsert_my_instruction": {
        "description": (
            "САМООБУЧЕНИЕ: сохрани или обнови СВОЮ личную инструкцию/навык (только твою, "
            "других агентов и глобальные правила ты трогать не можешь). Используй, когда узнал "
            "устойчивое правило работы, специфику команды или полезный приём — сформулируй кратко "
            "и по делу. name — короткое имя (например «Формат отчёта по остаткам»), content — сам текст."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Короткое имя инструкции (до 80 символов)."},
                "content": {"type": "string", "description": "Текст инструкции/навыка (до 8000 символов)."},
            },
            "required": ["name", "content"],
        },
    },
    "list_my_instructions": {
        "description": "САМООБУЧЕНИЕ: список твоих личных инструкций/навыков (имя, источник, текст).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "delete_my_instruction": {
        "description": (
            "САМООБУЧЕНИЕ: удали СВОЮ личную инструкцию по имени. Удалять можно только те, "
            "что ты сам создал (source=self); инструкции владельца защищены."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Имя инструкции."}},
            "required": ["name"],
        },
    },
}


def _agent_self_tool_call(agent: dict[str, Any], name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Personal-instruction self-learning tools, backed by git files (with attribution)."""
    from agent_automations import AUTOMATION_SELF_TOOL_SPECS, automation_self_tool_call
    if name in AUTOMATION_SELF_TOOL_SPECS:
        return automation_self_tool_call(agent, name, args)
    from agent_knowledge import (
        count_agent_self_learned,
        delete_agent_learned,
        load_agent_learned,
        save_agent_learned,
    )
    slug = agent["slug"]
    actor = f"агент «{agent.get('name') or slug}» (самообучение)"
    if name == "list_my_instructions":
        rows = [
            {"name": i["name"], "source": i["source"], "content": i["content"],
             "created_by": i.get("created_by"), "updated_at": i.get("updated_at")}
            for i in (load_agent_learned(slug) or [])
        ]
        return {"instructions": rows, "count": len(rows)}
    inst_name = str(args.get("name") or "").strip()[:80]
    if not inst_name:
        raise ValueError("Укажите name.")
    if name == "upsert_my_instruction":
        content = str(args.get("content") or "").strip()[:_AGENT_INSTRUCTION_CHARS_MAX]
        if not content:
            raise ValueError("Укажите content.")
        existing = {i["name"] for i in (load_agent_learned(slug) or []) if i["source"] == "self"}
        if inst_name not in existing and count_agent_self_learned(slug) >= _AGENT_SELF_INSTRUCTIONS_MAX:
            raise ValueError(
                f"Лимит {_AGENT_SELF_INSTRUCTIONS_MAX} самоинструкций: удали неактуальную "
                "(delete_my_instruction) или объедини несколько в одну."
            )
        save_agent_learned(slug, inst_name, content, source="self", actor=actor,
                           dialog=str(args.get("_dialog_id") or "") or None)
        registry_git_sync(f"agent {slug}: self-learned «{inst_name}»")
        _agent_cache_bust()
        return {"ok": True, "saved": inst_name}
    if name == "delete_my_instruction":
        if not delete_agent_learned(slug, inst_name, only_self=True):
            raise ValueError("Такой самоинструкции нет (инструкции владельца удалять нельзя).")
        registry_git_sync(f"agent {slug}: delete self-learned «{inst_name}»")
        _agent_cache_bust()
        return {"ok": True, "deleted": inst_name}
    raise ValueError(f"Неизвестный инструмент: {name}")


# Fixed baseline: tools EVERY agent always keeps, no matter how its tools are
# customized — the minimum needed to read its own instructions, orient itself and
# answer from company knowledge. Intersected with the tier set below, so a faq agent
# never gains an ops tool through the baseline. Shown locked-on in the UI.
MANDATORY_AGENT_TOOLS: set[str] = {
    "start_here_always_read_ai_instructions",
    "get_context_guide",
    "get_ai_instructions",
    "search_company_knowledge",
}


def _agent_allowed_pool(agent: dict[str, Any]) -> set[str]:
    """The tools an agent may be given. There is no separate access-level gate any more —
    an agent's power is simply defined by which tools are enabled (owner asked to drop the
    level concept, 2026-07-03). Any tool from the full registry can be enabled, including
    dangerous admin ones (those still carry the 'admin' chip + a confirm in the UI)."""
    from mcp.context_server import TOOLS
    return set(TOOLS)


def _agent_preset_default(agent: dict[str, Any]) -> set[str]:
    """Enabled set for an agent that was never customized — seeded from the legacy preset:
    база знаний → read-only faq set, все функции → operational set, разработчик → everything."""
    from mcp.context_server import FAQ_TOOL_NAMES, OPS_TOOL_NAMES, TOOLS
    tier = agent.get("tier")
    if tier == "developer":
        return set(TOOLS)
    if tier == "ops":
        return set(OPS_TOOL_NAMES)
    return set(FAQ_TOOL_NAMES)


def _agent_tool_names(agent: dict[str, Any]) -> set[str]:
    """Tools the agent's connector actually serves. Selection is from the FULL registry
    (legacy tier is only a creation/default preset, not an access gate), intersected with
    the registry so stale names disappear, and the mandatory baseline is always forced on.
    Default (never customized) = the preset chosen at creation time.
    This is the hard gate: tools/list over the connector returns exactly this set, so a
    disabled tool is invisible and uncallable regardless of the prompt."""
    pool = _agent_allowed_pool(agent)
    fixed = MANDATORY_AGENT_TOOLS & pool
    if agent.get("tools_customized"):
        whitelist = {t for t in (agent.get("tools") or []) if t}
        return fixed | (whitelist & pool)
    return (_agent_preset_default(agent) & pool) | fixed


def _mcp_agent_auth(slug: str, path_token: str | None) -> dict[str, Any] | None:
    import hmac as _hmac
    agent = _agent_by_slug(slug)
    if not agent or not path_token:
        return None
    if not _hmac.compare_digest(str(path_token), str(agent["mcp_token"])):
        return None
    return agent


@app.get("/mcp-agent/<slug>/<path:path_token>")
def mcp_agent_info(slug: str, path_token: str | None = None):
    agent = _mcp_agent_auth(slug, path_token)
    if not agent:
        return jsonify({"error": "forbidden"}), 403
    return jsonify({
        "name": f"albery-agent-{slug}",
        "transport": "http-json-rpc",
        "endpoint": f"/mcp-agent/{slug}",
        "scope": f"персональный набор инструментов агента «{agent['name']}» + личное самообучение",
        "methods": ["initialize", "tools/list", "tools/call"],
        "tools": sorted(_agent_tool_names(agent) | set(_SELF_TOOL_SPECS)),
    })


@app.post("/mcp-agent/<slug>/<path:path_token>")
def mcp_agent_http(slug: str, path_token: str | None = None):
    agent = _mcp_agent_auth(slug, path_token)
    if not agent:
        return jsonify({"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32001, "message": "forbidden"}}), 403
    if not agent["is_active"]:
        return jsonify({"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32002, "message": "агент выключен"}}), 403
    from mcp.context_server import handle_request
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32700, "message": "Request body must be JSON."}}), 400
    method = str(payload.get("method") or "")
    req_id = payload.get("id")
    tool_names = _agent_tool_names(agent)

    if method == "tools/call":
        tool = str(((payload.get("params") or {}).get("name")) or "")
        if tool in _SELF_TOOL_SPECS:
            args = ((payload.get("params") or {}).get("arguments")) or {}
            try:
                result = _agent_self_tool_call(agent, tool, args)
                import json as _json
                body = {"jsonrpc": "2.0", "id": req_id,
                        "result": {"content": [{"type": "text", "text": _json.dumps(result, ensure_ascii=False)}]}}
            except ValueError as exc:
                body = {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": str(exc)}}
            except Exception:  # noqa: BLE001
                logging.exception("agent self-tool failed: %s/%s", slug, tool)
                body = {"jsonrpc": "2.0", "id": req_id,
                        "error": {"code": -32000, "message": "внутренняя ошибка самообучения"}}
            return jsonify(body), 200

    # Per-agent instruction scope: universal instructions + the ones this agent's
    # manifest connects. start_here / get_ai_instructions over this connector return
    # exactly that set, so an unconnected optional instruction is unreadable here.
    from agent_knowledge import allowed_instruction_paths
    instruction_scope = allowed_instruction_paths(agent["slug"])
    response = handle_request(
        payload,
        tool_names=tool_names,
        allow_owner_tools=True,
        instruction_scope=instruction_scope,
        agent_slug=agent["slug"],
    )
    if response is None:
        return ("", 202)
    if method == "tools/list" and isinstance(response, dict):
        tools_list = ((response.get("result") or {}).get("tools"))
        if isinstance(tools_list, list):
            for name, spec in _SELF_TOOL_SPECS.items():
                tools_list.append({"name": name, "description": spec["description"],
                                   "inputSchema": spec["inputSchema"]})
    from app import mcp_status_code
    return jsonify(response), mcp_status_code(response)


# --- Usage accounting (/api/agent-center/usage) ---------------------------------------------

def _duration_label(ms: float | int | None) -> str:
    total_min = int((ms or 0) // 60000)
    if total_min < 1:
        return f"{int((ms or 0) // 1000)} сек"
    hours, minutes = divmod(total_min, 60)
    if hours:
        return f"{hours} ч {minutes} мин"
    return f"{minutes} мин"


_HERMES_STATE_DB = os.getenv("HERMES_STATE_DB", "/root/.hermes/state.db")
_SESSION_MATCH_TOLERANCE_S = 90
_ACTIVITY_GAP_MIN = 30


def _hermes_cli_sessions(since_epoch: float) -> list[dict[str, Any]]:
    """Real per-run token usage from the Hermes CLI session store (state.db).
    Every bot turn spawns one `hermes -z` run = one session row here."""
    import sqlite3
    try:
        db = sqlite3.connect(f"file:{_HERMES_STATE_DB}?mode=ro", uri=True, timeout=3)
        try:
            rows = db.execute(
                "SELECT started_at, input_tokens, output_tokens, reasoning_tokens, cache_read_tokens"
                " FROM sessions WHERE source = 'cli' AND started_at >= ? ORDER BY started_at",
                (since_epoch,),
            ).fetchall()
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        logging.warning("agent_center: hermes state.db unavailable", exc_info=True)
        return []
    return [
        {
            "start": float(r[0] or 0),
            "tokens": int(r[1] or 0) + int(r[2] or 0) + int(r[3] or 0),
            "cache": int(r[4] or 0),
            "used": False,
        }
        for r in rows
    ]


def usage_payload(period: str) -> dict[str, Any]:
    """Per-employee usage for a period. Tokens are REAL where possible: each bot
    turn is matched by start time (created_at − latency) to its `hermes -z` CLI
    session in state.db (±90s); unmatched turns fall back to a text-size estimate.
    Two times are reported: сколько сотрудник провёл в работе с агентом (turns
    grouped into activity sessions with 30-min gaps) and сколько работал сам агент
    (sum of turn latencies)."""
    period = (period or "7").strip().lower()
    now_msk = msk_now()
    if period == "today":
        since = now_msk.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        try:
            days = min(max(int(period), 1), 365)
        except (TypeError, ValueError):
            days = 7
        since = now_msk - timedelta(days=days)
    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT bitrix_user_id, created_at, latency_ms, status,"
                " length(COALESCE(question, '')) + length(COALESCE(answer, '')) AS chars"
                " FROM bitrix_bot_interactions WHERE created_at >= %s ORDER BY created_at",
                (since,),
            )
            turns = cur.fetchall()

    sessions = _hermes_cli_sessions(since.timestamp() - _SESSION_MATCH_TOLERANCE_S)
    per_user: dict[int | None, dict[str, Any]] = {}
    matched_turns = 0
    for t in turns:
        uid = int(t["bitrix_user_id"]) if t["bitrix_user_id"] is not None else None
        u = per_user.setdefault(uid, {
            "turns": 0, "errors": 0, "agent_ms": 0, "tokens": 0, "cache": 0,
            "matched": 0, "starts": [], "last_at": None,
        })
        latency = int(t["latency_ms"] or 0)
        turn_start = t["created_at"].timestamp() - latency / 1000
        u["turns"] += 1
        u["agent_ms"] += latency
        u["errors"] += 1 if t["status"] != "ok" else 0
        u["starts"].append((turn_start, latency))
        u["last_at"] = t["created_at"]
        best = None
        for s in sessions:
            if s["used"]:
                continue
            delta = abs(s["start"] - turn_start)
            if delta <= _SESSION_MATCH_TOLERANCE_S and (best is None or delta < best[0]):
                best = (delta, s)
        if best is not None:
            best[1]["used"] = True
            u["tokens"] += best[1]["tokens"]
            u["cache"] += best[1]["cache"]
            u["matched"] += 1
            matched_turns += 1
        else:
            u["tokens"] += int(t["chars"] or 0) // 3  # fallback: text-size estimate

    names = _user_names()
    rows = []
    for uid, u in per_user.items():
        # Employee time with the agent: group turns into sessions by 30-min gaps.
        activity_ms = 0
        block_start = block_end = None
        for start, latency in sorted(u["starts"]):
            end = start + latency / 1000
            if block_start is None or start - block_end > _ACTIVITY_GAP_MIN * 60:
                if block_start is not None:
                    activity_ms += int((block_end - block_start) * 1000)
                block_start, block_end = start, end
            else:
                block_end = max(block_end, end)
        if block_start is not None:
            activity_ms += int((block_end - block_start) * 1000)
        info = names.get(uid or -1, {})
        rows.append({
            "bitrix_user_id": uid,
            "name": info.get("name") or (f"Сотрудник #{uid}" if uid else "Без имени"),
            "position": info.get("position") or "",
            "turns": u["turns"],
            "time_ms": activity_ms,
            "time_label": _duration_label(activity_ms),
            "agent_time_label": _duration_label(u["agent_ms"]),
            "errors": u["errors"],
            "tokens_est": u["tokens"],
            "cache_tokens": u["cache"],
            "matched": u["matched"],
            "last_at": _when_label(u["last_at"]),
        })
    rows.sort(key=lambda x: x["tokens_est"], reverse=True)
    total_turns = sum(x["turns"] for x in rows)
    totals = {
        "turns": total_turns,
        "time_ms": sum(x["time_ms"] for x in rows),
        "time_label": _duration_label(sum(x["time_ms"] for x in rows)),
        "agent_time_label": _duration_label(sum(u["agent_ms"] for u in per_user.values())),
        "tokens_est": sum(x["tokens_est"] for x in rows),
        "cache_tokens": sum(x["cache_tokens"] for x in rows),
        "users": len(rows),
        "matched_turns": matched_turns,
        "coverage_pct": round(matched_turns / total_turns * 100) if total_turns else 0,
    }
    return {"period": period, "rows": rows, "totals": totals}


@app.get("/api/agent-center/usage")
def agent_center_usage():
    try:
        return jsonify(usage_payload(request.args.get("period") or "7"))
    except Exception:  # noqa: BLE001
        logging.exception("agent_center usage failed")
        return jsonify({"error": "Не удалось загрузить статистику использования."}), 500


def agent_center_report(period: str = "7") -> dict[str, Any]:
    """Combined monitoring + usage snapshot for the agent's own analysis
    (resolved via app_workflow_function by the get_agent_monitoring MCP tool)."""
    return {"monitoring": monitoring_payload(), "usage": usage_payload(str(period))}


_HERMES_SKILLS_DIR = Path(os.getenv("HERMES_SKILLS_DIR", "/root/.hermes/skills"))
_REPO_SKILLS_DIR = Path(__file__).resolve().parent / "scripts" / "hermes_skills"
_FRONTMATTER_FIELD_RE = re.compile(r"^(name|description):\s*(.*)$")


def _skill_frontmatter(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return out
    for line in lines[1:60]:
        if line.strip() == "---":
            break
        m = _FRONTMATTER_FIELD_RE.match(line.strip())
        if m:
            out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


def _hermes_skills() -> list[dict[str, Any]]:
    """The skill library. Source of truth is the GitHub registry
    (agent_knowledge/skills + agent_knowledge/hermes_base); falls back to the live
    Hermes skills dir (/root/.hermes/skills) when the registry is absent. Skill ids
    match across both sources (skill:<path> relative to the skills root)."""
    from agent_knowledge import load_skills  # lazy: no import cycle

    reg = load_skills()
    if reg is not None:
        out: list[dict[str, Any]] = []
        for s in reg:
            out.append({
                "id": s["id"],
                "title": s["title"],
                "parent": s.get("parent") or "",
                "description": s["description"],
                "type": "Скилл",
                "custom": bool(s.get("custom")),
                "kind": s.get("kind") or "shared",
                "has_content": True,
                "updated": "обновлено " + _when_label(s["updated_at"]) if s.get("updated_at") else "",
            })
        return out
    skills: list[dict[str, Any]] = []
    if not _HERMES_SKILLS_DIR.is_dir():
        return skills
    custom_names = {p.name for p in _REPO_SKILLS_DIR.iterdir() if p.is_dir()} if _REPO_SKILLS_DIR.is_dir() else set()
    for skill_md in sorted(_HERMES_SKILLS_DIR.rglob("SKILL.md")):
        try:
            rel_parts = skill_md.relative_to(_HERMES_SKILLS_DIR).parts
            meta = _skill_frontmatter(skill_md.read_text(encoding="utf-8", errors="replace"))
            name = meta.get("name") or skill_md.parent.name
            mtime = datetime.fromtimestamp(skill_md.stat().st_mtime, tz=timezone.utc)
            desc = re.sub(r"\s+", " ", meta.get("description") or "").strip()
            skills.append({
                "id": "skill:" + "/".join(rel_parts[:-1]),
                "title": name,
                "parent": rel_parts[0] if len(rel_parts) > 2 else "",
                "description": (desc[:160].rstrip() + "…") if len(desc) > 160 else desc,
                "type": "Скилл",
                "custom": skill_md.parent.name in custom_names,
                "has_content": True,
                "updated": "обновлено " + _when_label(mtime),
            })
        except Exception:  # noqa: BLE001
            logging.exception("agent_center: skill parse failed for %s", skill_md)
    return skills


@app.get("/api/agent-center/knowledge")
def agent_center_knowledge():
    """Knowledge page: company instructions + skills, sourced from the GitHub
    registry (with DB fallback via _library_instructions / _hermes_skills)."""
    items = []
    try:
        for i in _library_instructions():
            items.append({
                "id": i["id"],
                "title": i["title"],
                "parent": i["parent"],
                "description": i["description"],
                "type": "Инструкция",
                "scope": i.get("scope") or "universal",
                "custom": True,
                "has_content": True,
                "updated": ("обновлено " + _when_label(i["updated_at"])) if i.get("updated_at") else "",
            })
    except Exception:  # noqa: BLE001
        logging.exception("agent_center knowledge failed")
        return jsonify({"error": "Не удалось загрузить базу знаний."}), 500
    items.extend(_hermes_skills())
    return jsonify({"items": items})


@app.put("/api/agent-center/knowledge/instruction-scope")
def agent_center_instruction_scope():
    """Flip a library instruction between universal (goes to every agent) and optional
    (connected per-agent). This is a LIBRARY-level change affecting all agents; it edits
    the instruction's frontmatter in the GitHub registry."""
    body = request.get_json(silent=True) or {}
    path = str(body.get("path") or "").strip()
    scope = str(body.get("scope") or "").strip().lower()
    if not path or scope not in ("universal", "optional"):
        return jsonify({"error": "Нужны path и scope (universal|optional)."}), 400
    try:
        from agent_knowledge import set_instruction_scope
        ok = set_instruction_scope(path, scope)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:  # noqa: BLE001
        logging.exception("instruction scope change failed")
        return jsonify({"error": "Не удалось изменить область инструкции."}), 500
    if not ok:
        return jsonify({"error": "Инструкция не найдена в реестре (нужен git-режим)."}), 404
    registry_git_sync(f"instruction scope: {path} -> {scope}")
    _agent_cache_bust()
    return jsonify({"ok": True, "path": path, "scope": scope})


# Bootstrap the universal (main) agent row + connector once at import. Best-effort: guarded
# inside ensure_main_agent, never fatal. Routing to it stays behind UNIVERSAL_MAIN_AGENT (off
# by default), so this only prepares the dormant connector — the live bot is untouched until
# the flag is flipped. Set ENSURE_MAIN_AGENT=0 to skip entirely.
if os.getenv("ENSURE_MAIN_AGENT", "1").strip() != "0":
    try:
        ensure_main_agent()
    except Exception:  # noqa: BLE001
        logging.exception("agent_center: ensure_main_agent bootstrap failed")


# Per-agent scheduled automations: registers its /api/agent-center/* routes and the
# scheduler thread at import, and contributes three self-tools to every agent connector
# (schedule/list/delete my automation) — merged into _SELF_TOOL_SPECS so mcp_agent_http
# advertises and dispatches them alongside the self-learning tools.
import agent_automations as _agent_automations  # noqa: E402

_SELF_TOOL_SPECS.update(_agent_automations.AUTOMATION_SELF_TOOL_SPECS)

# Agent-owned scheduler for recurring Bitrix tasks: starts its minute-tick thread at import (same
# process as the MCP tools). Recurring tasks are fired by us — the portal has no Bitrix subscription
# so Bitrix's own recurring-task templates never spawn. Kill-switch: RECURRING_TASKS_SCHEDULER=0.
import recurring_scheduler as _recurring_scheduler  # noqa: E402,F401
import task_checkin as _task_checkin  # noqa: E402,F401  (daily 12:00 offers + dossiers)
