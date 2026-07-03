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
import subprocess
import time

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

_DIALOGS_LIMIT_DEFAULT = 100
_MESSAGES_LIMIT_DEFAULT = 200


# Direct URLs for the Центр Агента pages (the SPA routes itself by pathname).
@app.get("/agent")
@app.get("/agent-dialogs")
@app.get("/agent-knowledge")
@app.get("/agent-monitoring")
@app.get("/agent-usage")
def agent_center_spa():
    from app import index
    return index()

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
        # Lazy import: b24bot may still be mid-import when a script imports it first
        # (b24bot → app → agent_center), so never touch it at module level here.
        from b24bot import _b24_portal_user_directory
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
    main_agent = {
        **_MAIN_AGENT_META,
        "name": _main_bot_name() or _MAIN_AGENT_META["name"],
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
            ss = sub_stats.get(a["slug"], {})
            member_names = [names.get(uid, {}).get("name") or f"#{uid}" for uid in sorted(a["members"])]
            sub_avg = ss.get("avg_ms")
            agents.append({
                "id": a["slug"],
                "name": a["name"],
                "kind": f"субагент • {'все функции' if a['tier'] == 'ops' else 'база знаний'}",
                "icon": "box",
                "icon_bg": "bg-blue-100 text-blue-500",
                "is_system": False,
                "is_active": bool(a["is_active"]),
                "channels": ["Bitrix"] if a["bitrix_bot_id"] else [],
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
    return jsonify({"agents": agents})


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


def monitoring_payload(chart_days: int = 1) -> dict[str, Any]:
    """Live monitoring snapshot; shared by the SPA endpoint, the agent's
    get_agent_monitoring MCP tool and the half-hourly health watchdog."""
    chart_days = min(max(int(chart_days or 1), 1), 90)
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
                " FROM bitrix_bot_interactions",
                {"today": today_start, "yday": yday_start, "yday_same": yday_same_time,
                 "day_ago": day_ago, "week_ago": week_ago},
            )
            st = dict(cur.fetchone() or {})
            cur.execute(
                "SELECT created_at, latency_ms, status FROM bitrix_bot_interactions"
                " WHERE created_at >= %s ORDER BY created_at",
                (chart_since,),
            )
            turn_rows = cur.fetchall()
            cur.execute(
                "SELECT created_at, dialog_id, bitrix_user_id, error, latency_ms, status"
                " FROM bitrix_bot_interactions"
                " WHERE created_at >= %s AND (status <> 'ok' OR latency_ms > 300000)"
                " ORDER BY id DESC LIMIT 12",
                (week_ago,),
            )
            notable_rows = cur.fetchall()
            cur.execute(
                "SELECT created_at, reporter_name, report_text FROM bitrix_error_reports"
                " WHERE created_at >= %s ORDER BY id DESC LIMIT 8",
                (week_ago,),
            )
            report_rows = cur.fetchall()
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
    last_ok = st.get("last_ok_at")
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
        text = f"«Сообщить об ошибке» от {r['reporter_name'] or 'сотрудника'}: " + re.sub(r"\s+", " ", r["report_text"]).strip()[:140]
        stamped.append((r["created_at"], {"type": "report", "text": text}))
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
    try:
        return jsonify(monitoring_payload(chart_days))
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
_AGENT_INSTRUCTION_CHARS_MAX = 8000
_AGENT_CACHE: dict[str, Any] = {"at": 0.0, "by_bot": {}, "by_slug": {}}
_AGENT_COLORS = ("GREEN", "MINT", "PINK", "ORANGE", "PURPLE", "AQUA", "LIGHT_BLUE", "GRAY")

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


def _load_agents_full() -> list[dict[str, Any]]:
    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id::text AS id, slug, name, role_prompt, tier, tools, bitrix_bot_id,"
                " mcp_token, is_active, color, created_at FROM agents ORDER BY created_at"
            )
            agents = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT agent_id::text AS agent_id, bitrix_user_id FROM agent_members")
            members = cur.fetchall()
            cur.execute(
                "SELECT id::text AS id, agent_id::text AS agent_id, name, content, source, updated_at"
                " FROM agent_instructions ORDER BY created_at"
            )
            instructions = cur.fetchall()
    by_id = {a["id"]: a for a in agents}
    for a in agents:
        a["members"] = set()
        a["instructions"] = []
    for m in members:
        if m["agent_id"] in by_id:
            by_id[m["agent_id"]]["members"].add(int(m["bitrix_user_id"]))
    for i in instructions:
        if i["agent_id"] in by_id:
            by_id[i["agent_id"]]["instructions"].append(dict(i))
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


# --- Bitrix bot auto-registration (same local application, new CODE per agent) --------------

def _register_agent_bot(slug: str, name: str, color: str) -> Any:
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
        "PROPERTIES": {"NAME": name, "COLOR": color, "WORK_POSITION": "ИИ-агент Albery"},
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


def _rename_bitrix_bot(bot_id: Any, name: str) -> None:
    """Push a UI rename to the Bitrix bot so the messenger shows the same name."""
    from b24bot import _b24_app_access_token, _b24_app_call
    endpoint, access = _b24_app_access_token()
    if not (endpoint and access):
        raise RuntimeError("Нет OAuth-токенов приложения Bitrix — напишите боту любое сообщение и повторите.")
    _b24_app_call(endpoint, access, "imbot.update",
                  {"BOT_ID": bot_id, "FIELDS": {"PROPERTIES": {"NAME": name}}})


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
    tier = str(body.get("tier") or "faq").strip()
    if tier not in ("faq", "ops"):
        return jsonify({"error": "Уровень должен быть faq или ops."}), 400
    role_prompt = str(body.get("role_prompt") or "").strip()[:4000]
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
                    "INSERT INTO agents (slug, name, role_prompt, tier, mcp_token, color)"
                    " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id::text AS id",
                    (slug, name, role_prompt, tier, token, color),
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
    try:
        bot_id = _register_agent_bot(slug, name, color)
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


@app.get("/api/agent-center/agents/<slug>")
def agent_center_agent_detail(slug: str):
    agent = _agent_by_slug(slug)
    if not agent:
        return jsonify({"error": "Агент не найден."}), 404
    names = _user_names()
    return jsonify({
        "slug": agent["slug"],
        "name": agent["name"],
        "role_prompt": agent["role_prompt"],
        "tier": agent["tier"],
        "is_active": agent["is_active"],
        "bitrix_bot_id": agent["bitrix_bot_id"],
        "members": [
            {"id": uid, "name": names.get(uid, {}).get("name") or f"#{uid}"}
            for uid in sorted(agent["members"])
        ],
        "instructions": [
            {"id": i["id"], "name": i["name"], "content": i["content"], "source": i["source"],
             "updated": _when_label(i["updated_at"])}
            for i in agent["instructions"]
        ],
    })


@app.patch("/api/agent-center/agents/<slug>")
def agent_center_agent_update(slug: str):
    body = request.get_json(silent=True) or {}
    if slug == "main":
        # The main agent lives in Bitrix only; renaming it = renaming the main bot.
        name = str(body.get("name") or "").strip()[:60]
        if not name:
            return jsonify({"error": "Укажите имя."}), 400
        try:
            from b24bot import _b24_load_state
            main_bot_id = (_b24_load_state() or {}).get("bot_id")
            if not main_bot_id:
                return jsonify({"error": "Основной бот не найден в state — напишите ему сообщение и повторите."}), 409
            _rename_bitrix_bot(main_bot_id, name)
        except Exception as exc:  # noqa: BLE001
            logging.exception("main agent rename failed")
            return jsonify({"error": f"Не удалось переименовать бота: {str(exc)[:200]}"}), 502
        _AGENT_CACHE.update(main_name_at=0.0)
        return jsonify({"ok": True})
    agent = _agent_by_slug(slug)
    if not agent:
        return jsonify({"error": "Агент не найден."}), 404
    warnings = []
    try:
        with pg_connect() as conn:
            with conn.cursor() as cur:
                if "name" in body and str(body["name"]).strip():
                    new_name = str(body["name"]).strip()[:60]
                    cur.execute("UPDATE agents SET name = %s, updated_at = now() WHERE slug = %s",
                                (new_name, slug))
                    if agent.get("bitrix_bot_id") and new_name != agent["name"]:
                        try:
                            _rename_bitrix_bot(agent["bitrix_bot_id"], new_name)
                        except Exception as exc:  # noqa: BLE001
                            logging.exception("agent rename in bitrix failed")
                            warnings.append(f"Имя в Bitrix не обновилось: {str(exc)[:160]}")
                if "role_prompt" in body:
                    cur.execute("UPDATE agents SET role_prompt = %s, updated_at = now() WHERE slug = %s",
                                (str(body["role_prompt"] or "").strip()[:4000], slug))
                if "tier" in body and str(body["tier"]) in ("faq", "ops"):
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
            bot_id = _register_agent_bot(slug, agent["name"], agent.get("color") or "GREEN")
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
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO agent_instructions (agent_id, name, content, source)"
                    " SELECT id, %s, %s, 'owner' FROM agents WHERE slug = %s"
                    " ON CONFLICT (agent_id, name) DO UPDATE SET content = EXCLUDED.content, updated_at = now()",
                    (name, content, slug),
                )
    except Exception:  # noqa: BLE001
        logging.exception("agent instruction add failed")
        return jsonify({"error": "Не удалось сохранить инструкцию."}), 500
    _agent_cache_bust()
    return jsonify({"ok": True})


@app.delete("/api/agent-center/agents/<slug>/instructions/<inst_id>")
def agent_center_instruction_delete(slug: str, inst_id: str):
    try:
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM agent_instructions WHERE id::text = %s"
                    " AND agent_id = (SELECT id FROM agents WHERE slug = %s)",
                    (inst_id, slug),
                )
    except Exception:  # noqa: BLE001
        logging.exception("agent instruction delete failed")
        return jsonify({"error": "Не удалось удалить."}), 500
    _agent_cache_bust()
    return jsonify({"ok": True})


# --- Per-agent MCP endpoint (/mcp-agent/<slug>/<token>) with self-learning ------------------
# The agent's ONLY connector. Tool scope = its tier set (faq → read-only knowledge;
# ops → operational, no admin tools) ∩ optional per-agent whitelist, PLUS three
# self-learning tools handled RIGHT HERE with the slug from the URL — so an agent
# can read/write exclusively its own instruction store, never global instructions,
# never another agent's. Global admin tools are structurally unreachable.

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
    slug = agent["slug"]
    if name == "list_my_instructions":
        rows = [
            {"name": i["name"], "source": i["source"], "content": i["content"]}
            for i in (_agent_by_slug(slug) or agent).get("instructions", [])
        ]
        return {"instructions": rows, "count": len(rows)}
    inst_name = str(args.get("name") or "").strip()[:80]
    if not inst_name:
        raise ValueError("Укажите name.")
    if name == "upsert_my_instruction":
        content = str(args.get("content") or "").strip()[:_AGENT_INSTRUCTION_CHARS_MAX]
        if not content:
            raise ValueError("Укажите content.")
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) AS n FROM agent_instructions"
                    " WHERE agent_id = (SELECT id FROM agents WHERE slug = %s) AND source = 'self'",
                    (slug,),
                )
                if int(cur.fetchone()["n"]) >= _AGENT_SELF_INSTRUCTIONS_MAX:
                    raise ValueError(
                        f"Лимит {_AGENT_SELF_INSTRUCTIONS_MAX} самоинструкций: удали неактуальную "
                        "(delete_my_instruction) или объедини несколько в одну."
                    )
                cur.execute(
                    "INSERT INTO agent_instructions (agent_id, name, content, source)"
                    " SELECT id, %s, %s, 'self' FROM agents WHERE slug = %s"
                    " ON CONFLICT (agent_id, name) DO UPDATE SET content = EXCLUDED.content, updated_at = now()",
                    (inst_name, content, slug),
                )
        _agent_cache_bust()
        return {"ok": True, "saved": inst_name}
    if name == "delete_my_instruction":
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM agent_instructions WHERE name = %s AND source = 'self'"
                    " AND agent_id = (SELECT id FROM agents WHERE slug = %s)",
                    (inst_name, slug),
                )
                deleted = cur.rowcount
        _agent_cache_bust()
        if not deleted:
            raise ValueError("Такой самоинструкции нет (инструкции владельца удалять нельзя).")
        return {"ok": True, "deleted": inst_name}
    raise ValueError(f"Неизвестный инструмент: {name}")


def _agent_tool_names(agent: dict[str, Any]) -> set[str]:
    from mcp.context_server import FAQ_TOOL_NAMES, OPS_TOOL_NAMES
    base = set(OPS_TOOL_NAMES) if agent["tier"] == "ops" else set(FAQ_TOOL_NAMES)
    whitelist = {t for t in (agent.get("tools") or []) if t}
    return (base & whitelist) if whitelist else base


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
        "scope": f"tier={agent['tier']} + личное самообучение агента «{agent['name']}»",
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

    response = handle_request(payload, tool_names=tool_names)
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
