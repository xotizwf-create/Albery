"""Daily task check-in + per-employee agent dossier (владелец 2026-07-11, задача 1304).

Once a day (12:00 МСК) the pipeline scans OPEN tasks created by people and finds the ones the
agent can genuinely accelerate — not background noise. Centralized and cheap by design:

  stage 0  deterministic filters (free): responsible has agent access; not an agent/cron task
           (those get an offer at creation already); not a mass hand-out (same title to ≥3
           people); no physical/decision stop-words (оплатить/замерить/ознакомиться/…);
           not a test task; no offer in this task yet.
  stage 1  ONE Groq batch call (free): «может ли агент сделать ≥50% работы своими реальными
           инструментами?» per task, with a one-line reason.
  stage 2  Codex writes a personal offer comment (task_offers pipeline) — only for the winners,
           capped per run, so the expensive model spends a handful of turns a day.

After posting, the run refreshes the per-employee DOSSIER (who works with the agent, who
ignores it, which of their tasks are automatable) and DMs the people it offered to — the first
DM carries the owner's «давайте поработаем вместе» message, later ones are a short digest.

Safety: kill-switch B24_TASK_CHECKIN=0, per-run offer cap, one DM per person per run and not
more often than once a day, atomic per-date claim in task_checkin_runs (no double runs across
restarts), dry-run mode for previews, everything best-effort — a failure never touches the
main bot. Tasks the check-in must NEVER touch: agent-created (offers exist), «Итоги созвона» /
«Рекомендации» (cron products), mass HR hand-outs, payments and physical-world work.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import timedelta
from typing import Any

from app import MSK_TZ, msk_now, pg_connect

_CHECKIN_HOUR = int(os.getenv("B24_TASK_CHECKIN_HOUR", "12"))
_OFFER_CAP = int(os.getenv("B24_TASK_CHECKIN_OFFER_CAP", "8"))
_CLASSIFY_CAP = 40   # tasks per Groq batch — plenty for this portal's volume

# Stop-words: the agent cannot pay bills, measure goods, drive trucks or make the boss's
# decisions. Matched against title+description, lowercased.
_STOP_WORDS = (
    "оплат", "пополн", "выплат", "платеж", "платёж",
    "замер", "отгруз", "погруз", "упаков", "привезти", "забор груза", "приемк", "приёмк",
    "позвонить", "переговор", "созвонит",
    "ознаком", "согласова", "подтверд",
    "итоги созвона", "рекомендации", "заполнить профиль", "анонс обучения",
)
_TEST_MARKERS = ("🧪", "probe", "[e2e", "удалится", "(del)", "тест ")
_OPEN_STATUSES = {"1", "2", "3"}  # new / pending / in progress


def checkin_enabled() -> bool:
    return os.getenv("B24_TASK_CHECKIN", "1").strip() != "0"


def is_working_day(dt=None) -> bool:
    """Mon-Fri. The agent must NOT message employees on weekends (owner rule 2026-07-12) —
    the scheduled check-in only fires on working days. A manual/forced run (owner asked
    explicitly) is not gated by this."""
    dt = dt or msk_now()
    return dt.isoweekday() <= 5


# --- stage 0: deterministic filters ------------------------------------------------------------

def _live_open_tasks() -> list[dict[str, Any]]:
    """Open tasks straight from Bitrix (the local index keeps ghosts of deleted tasks)."""
    from mcp.context_server import _webhook_raw
    out: list[dict[str, Any]] = []
    start = 0
    while True:
        r = _webhook_raw("tasks.task.list", {
            "filter": {"REAL_STATUS": sorted(_OPEN_STATUSES)},
            "select": ["ID", "TITLE", "DESCRIPTION", "RESPONSIBLE_ID", "CREATED_BY", "STATUS"],
            "start": start,
        })
        res = r.get("result") or {}
        tasks = res.get("tasks") or []
        for t in tasks:
            out.append({
                "id": int(t.get("id")),
                "title": str(t.get("title") or ""),
                "description": str(t.get("description") or ""),
                "responsible_id": int(t.get("responsibleId") or 0),
                "creator_id": int(t.get("createdBy") or 0),
            })
        nxt = r.get("next")
        if not nxt or not tasks:
            break
        start = int(nxt)
    return out


def filter_tasks(tasks: list[dict[str, Any]], offered_ids: set[int],
                 access_ok: dict[int, bool]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Stage-0 filters. Returns (survivors, drop-stats). Pure logic — unit-tested."""
    stats = {"offered": 0, "no_access": 0, "stop_word": 0, "test": 0, "mass": 0}
    title_counts: dict[str, int] = {}
    for t in tasks:
        key = t["title"].strip().lower()
        title_counts[key] = title_counts.get(key, 0) + 1
    survivors = []
    for t in tasks:
        blob = (t["title"] + " " + t["description"]).lower()
        if t["id"] in offered_ids:
            stats["offered"] += 1
            continue
        if not access_ok.get(t["responsible_id"], False):
            stats["no_access"] += 1
            continue
        if any(m in blob for m in _TEST_MARKERS):
            stats["test"] += 1
            continue
        if title_counts.get(t["title"].strip().lower(), 0) >= 3:
            stats["mass"] += 1
            continue
        if any(w in blob for w in _STOP_WORDS):
            stats["stop_word"] += 1
            continue
        survivors.append(t)
    return survivors, stats


# --- stage 1: one cheap batch classification ----------------------------------------------------

def classify_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One Groq call for the whole batch: can the agent do >=50% of the work with its REAL
    tools? Returns [{id, help, reason}]. On failure returns [] (the run posts nothing —
    честнее, чем спамить наугад)."""
    if not tasks:
        return []
    from task_offers import _groq_chat
    listing = "\n".join(
        f"- id={t['id']} | {t['title'][:120]} | {t['description'][:200]}" for t in tasks[:_CLASSIFY_CAP])
    prompt = (
        "Ты отбираешь задачи Bitrix24, в которых корпоративный ИИ-агент может РЕАЛЬНО ускорить "
        "работу, выполнив не меньше половины её сам. Его реальные возможности: собрать/сверить/"
        "актуализировать данные и Google-таблицы; подготовить анализ, отчёт, сводку, список, "
        "спецификацию; работать с CRM-воронками, сделками и задачами Bitrix; написать/"
        "отредактировать тексты (отзывы, шаблоны, письма, анонсы); подготовить документ Word; "
        "найти информацию в интернете и в базе знаний компании; проанализировать комментарии и "
        "вложения задачи. Он НЕ может: платить, ездить, звонить, измерять и трогать физический "
        "мир, принимать решения за руководителя.\n\n"
        "Задачи:\n" + listing + "\n\n"
        "Ответь СТРОГО JSON: {\"tasks\": [{\"id\": <id>, \"help\": true|false, "
        "\"reason\": \"<по-русски, одна строка: что именно агент сделает>\"}]}. Отбирай строго: "
        "сомневаешься — help=false. Лучше 3 точных попадания, чем 10 натяжек."
    )
    try:
        raw = _groq_chat(prompt)
        from task_offers import _extract_json
        data = _extract_json(raw)
        rows = data.get("tasks") if isinstance(data.get("tasks"), list) else []
        out = []
        for r in rows:
            try:
                out.append({"id": int(r.get("id")), "help": bool(r.get("help")),
                            "reason": str(r.get("reason") or "")[:300]})
            except (TypeError, ValueError):
                continue
        return out
    except Exception:  # noqa: BLE001
        logging.warning("task checkin: classification failed", exc_info=True)
        return []


# --- dossier ------------------------------------------------------------------------------------

def refresh_dossiers() -> None:
    """Recompute the deterministic dossier fields from source data (idempotent). Keeps the
    free-form notes/automatable text untouched."""
    import b24bot
    directory = b24bot._b24_portal_user_directory()
    now = msk_now()
    month_ago = now - timedelta(days=30)
    try:
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT bitrix_user_id, count(*) AS turns, "
                    "count(*) FILTER (WHERE dialog_id LIKE 'task-%%') AS task_turns, "
                    "max(created_at) AS last_use FROM bitrix_bot_interactions "
                    "WHERE created_at >= %s AND bitrix_user_id IS NOT NULL GROUP BY 1", (month_ago,))
                use = {int(r["bitrix_user_id"]): dict(r) for r in cur.fetchall()}
                cur.execute(
                    "SELECT responsible_id, count(*) AS made, "
                    "count(*) FILTER (WHERE state='declined') AS declined, max(offered_at) AS last_o "
                    "FROM bitrix_task_agent_offers GROUP BY 1")
                offers = {int(r["responsible_id"]): dict(r) for r in cur.fetchall() if r["responsible_id"]}
                cur.execute(
                    "SELECT o.responsible_id, count(DISTINCT o.task_id) AS engaged "
                    "FROM bitrix_task_agent_offers o "
                    "WHERE EXISTS (SELECT 1 FROM bitrix_bot_interactions i "
                    "  WHERE i.dialog_id = 'task-' || o.task_id AND i.bitrix_user_id = o.responsible_id "
                    "  AND i.created_at >= o.offered_at) GROUP BY 1")
                engaged = {int(r["responsible_id"]): int(r["engaged"]) for r in cur.fetchall() if r["responsible_id"]}
                for uid, info in directory.items():
                    u, o = use.get(uid, {}), offers.get(uid, {})
                    cur.execute(
                        "INSERT INTO employee_agent_dossier (bitrix_user_id, full_name, agent_access, "
                        "turns_30d, task_turns_30d, last_agent_use, offers_made, offers_engaged, "
                        "offers_declined, last_offer_at, updated_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now()) "
                        "ON CONFLICT (bitrix_user_id) DO UPDATE SET full_name=EXCLUDED.full_name, "
                        "agent_access=EXCLUDED.agent_access, turns_30d=EXCLUDED.turns_30d, "
                        "task_turns_30d=EXCLUDED.task_turns_30d, last_agent_use=EXCLUDED.last_agent_use, "
                        "offers_made=EXCLUDED.offers_made, offers_engaged=EXCLUDED.offers_engaged, "
                        "offers_declined=EXCLUDED.offers_declined, last_offer_at=EXCLUDED.last_offer_at, "
                        "updated_at=now()",
                        (uid, info.get("name"), b24bot._b24_main_allows(uid),
                         int(u.get("turns") or 0), int(u.get("task_turns") or 0), u.get("last_use"),
                         int(o.get("made") or 0), int(engaged.get(uid) or 0),
                         int(o.get("declined") or 0), o.get("last_o")))
    except Exception:  # noqa: BLE001
        logging.warning("task checkin: dossier refresh failed", exc_info=True)


def note_automatable(uid: int, task_title: str, reason: str) -> None:
    """Append a rolling «эту задачу агент может ускорить» observation to the dossier."""
    line = f"{msk_now().strftime('%d.%m')}: «{task_title[:80]}» — {reason[:160]}"
    try:
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO employee_agent_dossier (bitrix_user_id, automatable, updated_at) "
                    "VALUES (%s, %s, now()) ON CONFLICT (bitrix_user_id) DO UPDATE SET "
                    "automatable = left(coalesce(employee_agent_dossier.automatable || chr(10), '') "
                    "|| EXCLUDED.automatable, 2000), updated_at = now()",
                    (uid, line))
    except Exception:  # noqa: BLE001
        logging.warning("task checkin: automatable note failed uid=%s", uid, exc_info=True)


# --- DMs ----------------------------------------------------------------------------------------

_FIRST_DM = (
    "{name}, в ваших задачах я написал, как могу помочь — давайте поработаем вместе! "
    "Я могу очень сильно облегчить вашу работу.\n\n{tasks}\n\n"
    "Вы можете общаться со мной прямо внутри задач (просто отвечайте на мои комментарии) и "
    "здесь, в личных сообщениях: задавайте любые вопросы, поручайте рутину — я со всем помогу."
)
_NEXT_DM = ("{name}, я снова посмотрел ваши задачи и написал в них, чем могу ускорить "
            "работу:\n\n{tasks}\n\nОтветьте прямо в задаче — и я возьмусь.")


def _send_dms(offers_by_user: dict[int, list[dict[str, Any]]], main_bot_id: Any) -> int:
    import b24bot
    directory = b24bot._b24_portal_user_directory()
    sent = 0
    for uid, tasks in offers_by_user.items():
        try:
            with pg_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT first_dm_at, last_dm_at FROM employee_agent_dossier "
                                "WHERE bitrix_user_id=%s", (uid,))
                    row = cur.fetchone() or {}
            if row.get("last_dm_at") and (msk_now() - row["last_dm_at"].astimezone(MSK_TZ)) < timedelta(hours=20):
                continue  # never more than one DM a day
            from mcp.context_server import _task_deep_link
            name = (directory.get(uid, {}).get("name") or "").split()[0] or "Коллега"
            listing = "\n".join(
                f"— [URL={_task_deep_link(t['id'])}]{t['title'][:70]}[/URL]" for t in tasks[:5])
            tpl = _FIRST_DM if not row.get("first_dm_at") else _NEXT_DM
            ok, err = b24bot._albery_bitrix_notify(tpl.format(name=name, tasks=listing),
                                                   dialog_id=str(uid), bot_id=main_bot_id)
            if ok:
                sent += 1
                with pg_connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO employee_agent_dossier (bitrix_user_id, first_dm_at, last_dm_at, updated_at) "
                            "VALUES (%s, now(), now(), now()) ON CONFLICT (bitrix_user_id) DO UPDATE SET "
                            "first_dm_at = coalesce(employee_agent_dossier.first_dm_at, now()), "
                            "last_dm_at = now(), updated_at = now()", (uid,))
            else:
                logging.warning("task checkin: DM to %s failed: %s", uid, err)
        except Exception:  # noqa: BLE001
            logging.warning("task checkin: DM flow failed uid=%s", uid, exc_info=True)
    return sent


# --- the run ------------------------------------------------------------------------------------

def run_checkin(*, dry_run: bool = False, only_users: set[int] | None = None,
                offer_cap: int | None = None, force: bool = False) -> dict[str, Any]:
    """The daily pipeline. dry_run = full selection, nothing posted; only_users = limit the
    posting to these responsibles (careful live tests); force = ignore the daily claim."""
    import b24bot
    from task_offers import _post_offer
    report: dict[str, Any] = {"dry_run": dry_run}
    if not force and not dry_run:
        try:
            with pg_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("INSERT INTO task_checkin_runs (run_date) VALUES (%s) "
                                "ON CONFLICT (run_date) DO NOTHING RETURNING run_date",
                                (msk_now().date(),))
                    if cur.fetchone() is None:
                        return {"skipped": "already ran today"}
        except Exception:  # noqa: BLE001
            logging.warning("task checkin: claim failed", exc_info=True)
            return {"skipped": "claim failed"}

    tasks = _live_open_tasks()
    report["scanned"] = len(tasks)
    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT task_id FROM bitrix_task_agent_offers")
            offered_ids = {int(r["task_id"]) for r in cur.fetchall()}
    access_ok = {uid: b24bot._b24_main_allows(uid)
                 for uid in {t["responsible_id"] for t in tasks}}
    survivors, stats = filter_tasks(tasks, offered_ids, access_ok)
    report["filter_stats"] = stats
    report["passed_filters"] = len(survivors)

    verdicts = classify_tasks(survivors)
    by_id = {t["id"]: t for t in survivors}
    picked = [dict(by_id[v["id"]], reason=v["reason"]) for v in verdicts
              if v.get("help") and v.get("id") in by_id]
    if only_users is not None:
        picked = [t for t in picked if t["responsible_id"] in only_users]
    cap = offer_cap if offer_cap is not None else _OFFER_CAP
    picked = picked[:cap]
    report["picked"] = [{"id": t["id"], "title": t["title"][:80],
                         "responsible_id": t["responsible_id"], "reason": t["reason"]}
                        for t in picked]

    offers_posted = 0
    offers_by_user: dict[int, list[dict[str, Any]]] = {}
    if not dry_run:
        for t in picked:
            try:
                _post_offer(t["id"], t["title"], t["description"], None,
                            t["responsible_id"], t["creator_id"])
                offers_posted += 1
                offers_by_user.setdefault(t["responsible_id"], []).append(t)
                note_automatable(t["responsible_id"], t["title"], t["reason"])
            except Exception:  # noqa: BLE001
                logging.warning("task checkin: offer failed task=%s", t["id"], exc_info=True)
        refresh_dossiers()
        main_bot = b24bot.to_int(b24bot._b24_load_state().get("bot_id"))
        report["dms_sent"] = _send_dms(offers_by_user, main_bot)
    report["offers_posted"] = offers_posted

    if not dry_run and not force:
        try:
            with pg_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE task_checkin_runs SET finished_at=now(), scanned=%s, "
                        "passed_filters=%s, offers_posted=%s, dms_sent=%s, details=%s::jsonb "
                        "WHERE run_date=%s",
                        (report["scanned"], report["passed_filters"], offers_posted,
                         report.get("dms_sent", 0), json.dumps(report, ensure_ascii=False, default=str),
                         msk_now().date()))
        except Exception:  # noqa: BLE001
            logging.warning("task checkin: run report write failed", exc_info=True)
    logging.info("task checkin: scanned=%s passed=%s offers=%s dms=%s dry=%s",
                 report["scanned"], report["passed_filters"], offers_posted,
                 report.get("dms_sent", 0), dry_run)
    return report


def _loop() -> None:
    time.sleep(180)  # let the app boot
    while True:
        try:
            now = msk_now()
            if (checkin_enabled() and is_working_day(now)
                    and now.hour == _CHECKIN_HOUR and now.minute < 5):
                run_checkin()
        except Exception:  # noqa: BLE001
            logging.exception("task checkin: loop tick failed")
        time.sleep(120)


if os.getenv("B24_TASK_CHECKIN", "1").strip() != "0":
    threading.Thread(target=_loop, daemon=True, name="task-checkin").start()
