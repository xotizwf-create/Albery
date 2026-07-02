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
from b24bot import _b24_portal_user_directory

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


@app.get("/api/agent-center/monitoring")
def agent_center_monitoring():
    now_msk = msk_now()
    today_start = now_msk.replace(hour=0, minute=0, second=0, microsecond=0)
    yday_start = today_start - timedelta(days=1)
    yday_same_time = now_msk - timedelta(days=1)
    day_ago = now_msk - timedelta(hours=24)
    week_ago = now_msk - timedelta(days=7)
    try:
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
                    (day_ago,),
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
        db_ms = int((time.perf_counter() - db_t0) * 1000)
    except Exception:  # noqa: BLE001
        logging.exception("agent_center monitoring failed")
        return jsonify({"error": "Не удалось загрузить мониторинг."}), 500

    # Hourly speed chart for the last 24h (MSK buckets).
    buckets: dict[str, list[int]] = {}
    order: list[str] = []
    for offset in range(23, -1, -1):
        label = (now_msk - timedelta(hours=offset)).strftime("%H:00")
        order.append(label)
        buckets[label] = []
    for r in turn_rows:
        if r["latency_ms"] and r["status"] == "ok":
            label = r["created_at"].astimezone(MSK_TZ).strftime("%H:00")
            if label in buckets:
                buckets[label].append(int(r["latency_ms"]))
    chart = [
        {
            "time": label,
            "speed": round(sum(vals) / len(vals) / 1000) if vals else None,
            "turns": len(vals),
        }
        for label, vals in ((label, buckets[label]) for label in order)
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

    return jsonify({
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
    })


# --- Usage accounting (/api/agent-center/usage) ---------------------------------------------

def _duration_label(ms: float | int | None) -> str:
    total_min = int((ms or 0) // 60000)
    if total_min < 1:
        return f"{int((ms or 0) // 1000)} сек"
    hours, minutes = divmod(total_min, 60)
    if hours:
        return f"{hours} ч {minutes} мин"
    return f"{minutes} мин"


@app.get("/api/agent-center/usage")
def agent_center_usage():
    """Per-employee usage for a period: turns, total agent working time (sum of
    turn latencies) and an estimated token spend. Tokens are estimated from the
    question+answer text volume (~3 chars/token for Russian) — the bot does not
    log exact usage from Hermes yet; when it does, this switches to real numbers."""
    period = (request.args.get("period") or "7").strip().lower()
    now_msk = msk_now()
    if period == "today":
        since = now_msk.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        try:
            days = min(max(int(period), 1), 365)
        except (TypeError, ValueError):
            days = 7
        since = now_msk - timedelta(days=days)
    rows = []
    try:
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT bitrix_user_id,"
                    " COUNT(*) AS turns,"
                    " COALESCE(SUM(latency_ms), 0) AS total_ms,"
                    " AVG(latency_ms) AS avg_ms,"
                    " COUNT(*) FILTER (WHERE status <> 'ok') AS errors,"
                    " COALESCE(SUM(length(COALESCE(question, '')) + length(COALESCE(answer, ''))), 0) AS chars,"
                    " MAX(created_at) AS last_at"
                    " FROM bitrix_bot_interactions"
                    " WHERE created_at >= %s"
                    " GROUP BY bitrix_user_id",
                    (since,),
                )
                agg = cur.fetchall()
    except Exception:  # noqa: BLE001
        logging.exception("agent_center usage failed")
        return jsonify({"error": "Не удалось загрузить статистику использования."}), 500
    names = _user_names()
    for r in agg:
        uid = int(r["bitrix_user_id"]) if r["bitrix_user_id"] is not None else None
        info = names.get(uid or -1, {})
        tokens = int(r["chars"]) // 3
        rows.append({
            "bitrix_user_id": uid,
            "name": info.get("name") or (f"Сотрудник #{uid}" if uid else "Без имени"),
            "position": info.get("position") or "",
            "turns": int(r["turns"]),
            "time_ms": int(r["total_ms"]),
            "time_label": _duration_label(r["total_ms"]),
            "avg_label": f"{round(float(r['avg_ms']) / 1000)} сек" if r["avg_ms"] else "—",
            "errors": int(r["errors"]),
            "tokens_est": tokens,
            "last_at": _when_label(r["last_at"]),
        })
    rows.sort(key=lambda x: x["tokens_est"], reverse=True)
    totals = {
        "turns": sum(x["turns"] for x in rows),
        "time_ms": sum(x["time_ms"] for x in rows),
        "time_label": _duration_label(sum(x["time_ms"] for x in rows)),
        "tokens_est": sum(x["tokens_est"] for x in rows),
        "users": len(rows),
    }
    return jsonify({"period": period, "rows": rows, "totals": totals})


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
