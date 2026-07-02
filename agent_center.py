"""Agent Center read-only API (/api/agent-center/*) — backs the "Центр Агента" SPA section.

Serves data that already lives in PostgreSQL: employee↔bot dialogs
(bitrix_bot_interactions), tier-derived agent profiles with usage stats
(agent_access + interactions) and the instruction library (ai_instruction_folders).

Registers routes on the shared Flask `app` at import time (same pattern as b24bot);
app.py imports this module at the bottom. Every endpoint is GET/read-only and sits
behind the site's admin session login + /api gate (require_admin_auth in app.py).
"""
from __future__ import annotations

import logging
import re

from datetime import timedelta
from flask import jsonify
from flask import request
from typing import Any

from app import (
    MSK_TZ,
    app,
    msk_now,
    pg_connect,
)
from b24bot import _b24_portal_user_directory

_DIALOGS_LIMIT_DEFAULT = 100
_MESSAGES_LIMIT_DEFAULT = 200

# The three live "agents" today = the bot access tiers. When real subagent profiles
# get their own table this map becomes seed/meta for the system rows only.
_TIER_META = {
    "admin": {
        "name": "Админ",
        "kind": "системный • полный доступ",
        "icon": "crown",
        "icon_bg": "bg-amber-100 text-amber-500",
    },
    "ops": {
        "name": "Основной агент",
        "kind": "системный • все функции",
        "icon": "zap",
        "icon_bg": "bg-orange-100 text-orange-500",
    },
    "faq": {
        "name": "FAQ-агент",
        "kind": "системный • только знания",
        "icon": "book",
        "icon_bg": "bg-emerald-100 text-emerald-500",
    },
}

_B24_MARKUP_RE = re.compile(r"\[/?(?:b|i|u|s|code|quote|url(?:=[^\]]*)?)\]", re.IGNORECASE)


def _strip_b24_markup(text: str) -> str:
    return _B24_MARKUP_RE.sub("", text or "").strip()


def _limit_arg(default: int, ceiling: int) -> int:
    try:
        value = int(request.args.get("limit") or default)
    except (TypeError, ValueError):
        value = default
    return min(max(value, 1), ceiling)


def _when_label(dt: Any) -> str:
    """15:05 for today, «вчера», DD.MM otherwise — matches the dialog-list design."""
    if not dt:
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
        for uid, info in _b24_portal_user_directory().items():
            out[uid] = {"name": info.get("name") or f"#{uid}", "position": info.get("position") or ""}
    except Exception:  # noqa: BLE001
        logging.exception("agent_center: portal directory load failed")
    return out


@app.get("/api/agent-center/dialogs")
def agent_center_dialogs():
    channel = (request.args.get("channel") or "bitrix").strip().lower()
    if channel == "telegram":
        return jsonify({"dialogs": [], "note": "Telegram-переписка появится после моста с Hermes."})
    q = (request.args.get("q") or "").strip()
    limit = _limit_arg(_DIALOGS_LIMIT_DEFAULT, 500)
    sql = """
        WITH last AS (
            SELECT DISTINCT ON (dialog_id)
                   dialog_id, bitrix_user_id, tier, question, status, created_at
            FROM bitrix_bot_interactions
            WHERE dialog_id IS NOT NULL
            ORDER BY dialog_id, id DESC
        ),
        agg AS (
            SELECT dialog_id,
                   COUNT(*) AS turns,
                   COUNT(*) FILTER (WHERE status <> 'ok') AS errors,
                   MAX(created_at) AS last_at
            FROM bitrix_bot_interactions
            WHERE dialog_id IS NOT NULL
            GROUP BY dialog_id
        )
        SELECT l.dialog_id, l.bitrix_user_id, l.tier, l.question, l.status,
               a.turns, a.errors, a.last_at
        FROM last l JOIN agg a USING (dialog_id)
    """
    params: list[Any] = []
    if q:
        sql += (
            " WHERE l.dialog_id IN (SELECT DISTINCT dialog_id FROM bitrix_bot_interactions"
            " WHERE question ILIKE %s OR answer ILIKE %s)"
        )
        like = f"%{q}%"
        params.extend([like, like])
    sql += " ORDER BY a.last_at DESC LIMIT %s"
    params.append(limit)
    dialogs = []
    try:
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        names = _user_names()
        for r in rows:
            uid = int(r["bitrix_user_id"]) if r["bitrix_user_id"] is not None else None
            info = names.get(uid or -1, {})
            preview = _strip_b24_markup(r["question"] or "")
            if len(preview) > 100:
                preview = preview[:100].rstrip() + "…"
            dialogs.append({
                "dialog_id": r["dialog_id"],
                "bitrix_user_id": uid,
                "user_name": info.get("name") or (f"Сотрудник #{uid}" if uid else "Сотрудник"),
                "user_position": info.get("position") or "",
                "tier": r["tier"] or "faq",
                "last_message": preview,
                "last_status": r["status"],
                "turns": int(r["turns"]),
                "errors": int(r["errors"]),
                "time": _when_label(r["last_at"]),
            })
    except Exception:  # noqa: BLE001
        logging.exception("agent_center dialogs failed")
        return jsonify({"error": "Не удалось загрузить диалоги."}), 500
    return jsonify({"dialogs": dialogs})


@app.get("/api/agent-center/dialog-messages")
def agent_center_dialog_messages():
    dialog_id = (request.args.get("dialog_id") or "").strip()
    if not dialog_id:
        return jsonify({"error": "Укажите dialog_id."}), 400
    limit = _limit_arg(_MESSAGES_LIMIT_DEFAULT, 1000)
    turns = []
    try:
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, created_at, question, answer, status, error, latency_ms, tier, session_name"
                    " FROM bitrix_bot_interactions WHERE dialog_id = %s ORDER BY id DESC LIMIT %s",
                    (dialog_id, limit),
                )
                rows = cur.fetchall()
        for r in reversed(rows):
            local = r["created_at"].astimezone(MSK_TZ) if r["created_at"] else None
            turns.append({
                "id": int(r["id"]),
                "date": local.strftime("%d.%m.%Y") if local else "",
                "time": local.strftime("%H:%M") if local else "",
                "question": (r["question"] or "").strip(),
                "answer": _strip_b24_markup(r["answer"] or ""),
                "status": r["status"],
                "error": (r["error"] or "").strip(),
                "latency_ms": r["latency_ms"],
                "tier": r["tier"] or "faq",
                "session_name": r["session_name"] or "",
            })
    except Exception:  # noqa: BLE001
        logging.exception("agent_center dialog messages failed")
        return jsonify({"error": "Не удалось загрузить переписку."}), 500
    return jsonify({"dialog_id": dialog_id, "turns": turns})


@app.get("/api/agent-center/agents")
def agent_center_agents():
    day_start = msk_now().replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = day_start - timedelta(days=7)
    stats: dict[str, dict[str, Any]] = {}
    members: dict[str, list[str]] = {}
    try:
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT tier,"
                    " COUNT(*) FILTER (WHERE created_at >= %s) AS turns_today,"
                    " COUNT(*) FILTER (WHERE created_at >= %s) AS turns_7d,"
                    " COUNT(*) FILTER (WHERE status <> 'ok' AND created_at >= %s) AS errors_7d,"
                    " AVG(latency_ms) FILTER (WHERE created_at >= %s AND latency_ms IS NOT NULL) AS avg_latency_7d,"
                    " MAX(created_at) AS last_at"
                    " FROM bitrix_bot_interactions GROUP BY tier",
                    (day_start, week_start, week_start, week_start),
                )
                for r in cur.fetchall():
                    stats[(r["tier"] or "faq")] = dict(r)
                cur.execute(
                    "SELECT a.tier, a.bitrix_user_id, a.display_name FROM agent_access a ORDER BY a.bitrix_user_id"
                )
                access_rows = cur.fetchall()
        names = _user_names()
        for r in access_rows:
            uid = int(r["bitrix_user_id"])
            name = names.get(uid, {}).get("name") or r["display_name"] or f"#{uid}"
            members.setdefault(r["tier"], []).append(name)
    except Exception:  # noqa: BLE001
        logging.exception("agent_center agents failed")
        return jsonify({"error": "Не удалось загрузить агентов."}), 500
    agents = []
    for tier in ("ops", "faq", "admin"):
        meta = _TIER_META[tier]
        st = stats.get(tier, {})
        users = members.get(tier, [])
        avg_ms = st.get("avg_latency_7d")
        agents.append({
            "id": tier,
            "name": meta["name"],
            "kind": meta["kind"],
            "icon": meta["icon"],
            "icon_bg": meta["icon_bg"],
            "is_active": True,
            "channels": ["Bitrix"],
            "users_count": len(users),
            "users_preview": ", ".join(users[:3]) + (f" +{len(users) - 3}" if len(users) > 3 else ""),
            "turns_today": int(st.get("turns_today") or 0),
            "turns_7d": int(st.get("turns_7d") or 0),
            "errors_7d": int(st.get("errors_7d") or 0),
            "avg_speed": f"{round(float(avg_ms) / 1000)} сек" if avg_ms else "—",
            "last_at": _when_label(st.get("last_at")),
        })
    return jsonify({"agents": agents})


@app.get("/api/agent-center/knowledge")
def agent_center_knowledge():
    items = []
    try:
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT f.id::text AS id, f.name, f.content, f.updated_at,"
                    " p.name AS parent_name"
                    " FROM ai_instruction_folders f"
                    " LEFT JOIN ai_instruction_folders p ON p.id = f.parent_id"
                    " ORDER BY f.sort_order, f.name"
                )
                for r in cur.fetchall():
                    content = re.sub(r"\s+", " ", (r["content"] or "")).strip()
                    items.append({
                        "id": r["id"],
                        "title": r["name"],
                        "parent": r["parent_name"] or "",
                        "description": (content[:160].rstrip() + "…") if len(content) > 160 else content,
                        "has_content": bool(content),
                        "updated": ("обновлено " + _when_label(r["updated_at"])) if r["updated_at"] else "",
                    })
    except Exception:  # noqa: BLE001
        logging.exception("agent_center knowledge failed")
        return jsonify({"error": "Не удалось загрузить базу знаний."}), 500
    return jsonify({"items": items})
