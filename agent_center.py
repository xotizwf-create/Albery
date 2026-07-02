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

import logging
import os
import re

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
from b24bot import _b24_portal_user_directory

_DIALOGS_LIMIT_DEFAULT = 100
_MESSAGES_LIMIT_DEFAULT = 200

# There is exactly one live agent today: the Bitrix24 bot. Access tiers
# (admin/ops/faq) are per-employee grants INSIDE it (agent_access), not separate
# agents. When subagents arrive (own Bitrix app/user each) they become extra rows.
_MAIN_AGENT_META = {
    "id": "main",
    "name": "Основной агент",
    "kind": "Bitrix24-бот • Мозг Гермеса",
    "icon": "zap",
    "icon_bg": "bg-orange-100 text-orange-500",
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
                    " FROM bitrix_bot_interactions",
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
    agent = {
        **_MAIN_AGENT_META,
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
    return jsonify({"agents": [agent]})


@app.get("/api/agent-center/tools")
def agent_center_tools():
    """The real MCP tool registry with tier availability (admin=/mcp, ops=/mcp-ops,
    faq=/mcp-faq) and the core-toolset flag (the compact set the chat-bot runs on).
    Same lazy-import idiom the /mcp* HTTP handlers in app.py use."""
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
    """The skill library the agent's Hermes gateway loads (all SKILL.md under the
    skills dir; no allowlist in config → everything is advertised to the model)."""
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
                        "type": "Инструкция",
                        "custom": True,
                        "has_content": bool(content),
                        "updated": ("обновлено " + _when_label(r["updated_at"])) if r["updated_at"] else "",
                    })
    except Exception:  # noqa: BLE001
        logging.exception("agent_center knowledge failed")
        return jsonify({"error": "Не удалось загрузить базу знаний."}), 500
    items.extend(_hermes_skills())
    return jsonify({"items": items})
