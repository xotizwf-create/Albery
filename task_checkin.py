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
import re
import os
import threading
import time
from datetime import timedelta
from typing import Any

from app import MSK_TZ, msk_now, pg_connect

_CHECKIN_HOUR = int(os.getenv("B24_TASK_CHECKIN_HOUR", "12"))
_OFFER_CAP = int(os.getenv("B24_TASK_CHECKIN_OFFER_CAP", "15"))
_CLASSIFY_CAP = 40   # tasks per Groq batch — plenty for this portal's volume

# Tasks the agent itself produced (its own digests / recommendation lists). Never offer help on
# our own output — matched anywhere in the text.
_SELF_GENERATED = ("итоги созвона", "рекомендации", "заполнить профиль", "анонс обучения")

# Core actions the agent physically cannot perform. Matched against the TITLE ONLY: the title
# states what the task IS. The same words inside a DESCRIPTION are almost always incidental
# context («фондовая политика согласована с Евгением», «какие налоги уже были оплачены») and used
# to kill perfectly good data tasks — on 2026-07-13 that wrongly dropped 19 of 43 open tasks.
_HARD_STOP_TITLE = (
    "оплат", "пополн", "выплат", "платеж", "платёж",
    "замер", "отгруз", "погруз", "упаков", "привезти", "забор груза", "приемк", "приёмк",
    "позвонить", "переговор", "созвонит",
)

# Real data work in the task (tables, comparisons, analytics, exports). Owner's rule: wherever
# there are tables/comparisons — offer help. A data signal OVERRIDES a hard stop-word; the
# classifier then judges whether the agent can actually do a useful part of it.
_DATA_SIGNALS = (
    "таблиц", "[table]", "docs.google", "spreadsheet", "выгрузк", "отчёт", "отчет", "сводк",
    "анализ", "проанализ", "сравн", "реестр", "спецификац", "динамик", "статистик",
    "мониторинг", "дашборд", "конверси", "воронк", "остатк", "карточк",
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
        if any(w in blob for w in _SELF_GENERATED):
            stats["stop_word"] += 1
            continue
        title = t["title"].lower()
        if (any(w in title for w in _HARD_STOP_TITLE)
                and not any(s in blob for s in _DATA_SIGNALS)):
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
    def _line(t: dict[str, Any]) -> str:
        # Judging on the title alone made the classifier blind: give it a real slice of the
        # description plus what is already attached to the task.
        extra = ""
        try:
            from task_offers import _task_context
            c = _task_context(t["id"])
            bits = []
            if c["comments"]:
                bits.append(f"комментариев {len(c['comments'])}")
            if c["attach_count"]:
                bits.append(f"вложений {c['attach_count']}")
            if c["checklist"]:
                bits.append(f"пунктов чек-листа {len(c['checklist'])}")
            if bits:
                extra = " | В ЗАДАЧЕ УЖЕ ЕСТЬ: " + ", ".join(bits)
        except Exception:  # noqa: BLE001
            logging.debug("task checkin: context for classifier failed id=%s", t.get("id"), exc_info=True)
        desc = re.sub(r"\s+", " ", str(t.get("description") or ""))[:700]
        return f"- id={t['id']} | {t['title'][:120]} | {desc}{extra}"

    listing = "\n".join(_line(t) for t in tasks[:_CLASSIFY_CAP])
    prompt = (
        "Ты отбираешь задачи Bitrix24, в которых корпоративный ИИ-агент может РЕАЛЬНО ускорить "
        "работу, взяв на себя существенную часть. Его реальные возможности: собрать/сверить/"
        "актуализировать данные и Google-таблицы; подготовить анализ, отчёт, сводку, список, "
        "спецификацию; работать с CRM-воронками, сделками и задачами Bitrix; написать/"
        "отредактировать тексты (отзывы, шаблоны, письма, анонсы); подготовить документ Word; "
        "найти информацию в интернете и в базе знаний компании; проанализировать комментарии, "
        "вложения и СКРИНЫ задачи; прочитать ТРАНСКРИПТ созвона, если он уже записан в нашей "
        "системе. Он НЕ может: смотреть/слушать видео и аудио (только готовый транскрипт из "
        "системы); платить, ездить, звонить, измерять и трогать физический мир; заходить во внешние "
        "системы без доступа; принимать решения за руководителя.\n\n"
        "Задачи:\n" + listing + "\n\n"
        "Ответь СТРОГО JSON: {\"tasks\": [{\"id\": <id>, \"help\": true|false, "
        "\"reason\": \"<по-русски, одна строка: что именно агент сделает>\"}]}.\n\n"
        "КАК ОТБИРАТЬ (правило владельца): не занижай — раньше отбор был слишком жёстким. Если в "
        "задаче есть ТАБЛИЦА, выгрузка, сравнение, анализ, отчёт, сводка, реестр, спецификация, ТЗ, "
        "подготовка текста или документа, поиск информации, работа с карточками товаров или CRM — "
        "ставь help=true, даже если часть работы человек всё равно сделает сам: агенту достаточно "
        "взять на себя ОДИН ощутимый кусок.\n"
        "ОБЯЗАТЕЛЬНАЯ ПРОВЕРКА перед help=true: в reason ты должен назвать КОНКРЕТНОЕ действие "
        "агента — «свести данные в таблицу», «сравнить карточки по цене и конверсии», «подготовить "
        "черновик документа», «найти информацию», «проанализировать отзывы». Если конкретное "
        "действие назвать не получается и reason выходит пересказом названия задачи — это НЕ "
        "попадание, ставь help=false.\n"
        "help=false ОБЯЗАТЕЛЕН, когда работа: физическая (собрать, скомплектовать, упаковать, "
        "отгрузить, принять, измерить, привезти товар), денежная операция (провести оплату, выдать "
        "премию), живой разговор (позвонить, договориться лично), согласование отпуска или "
        "отсутствия, личное управленческое решение руководителя."
    )
    from task_offers import _codex_chat, _extract_json

    def _parse(raw: str) -> list[dict[str, Any]]:
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

    # Engine chain like the offer composer: Groq (free, one batch call a day) with a couple of
    # spaced retries for a transient 429, then Codex once (reliable) if Groq is exhausted. Only
    # a total failure of both returns [] («post nothing» — safe but skips the day).
    last_exc = None
    for attempt in range(3):
        try:
            return _parse(_groq_chat(prompt))
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < 2:
                time.sleep(int(os.getenv("B24_CHECKIN_CLASSIFY_BACKOFF_S", "25")))
    logging.warning("task checkin: Groq classify failed (%s), trying Codex", repr(last_exc)[:120])
    try:
        return _parse(_codex_chat(prompt))
    except Exception as exc:  # noqa: BLE001
        logging.warning("task checkin: classification failed on both engines: %s", repr(exc)[:160])
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
# Nudge for people with access who simply don't use the agent (no offers today, no turns in 30d).
# Owner: «тем кто просто не работает с агентом — другой текст, типо давайте работать уже»,
# not every day — at most once in 3 days, and only when there is a reason.
_NUDGE_DM = (
    "{name}, я корпоративный ИИ-агент Албери — вижу, мы с вами ещё не работали вместе. "
    "Давайте уже попробуем!\n\n"
    "Напишите мне прямо здесь любой рабочий вопрос или поручите рутину: собрать данные или "
    "таблицу, сделать отчёт или анализ, найти информацию, поставить задачи, разобрать документ "
    "или скрин. Также меня можно позвать в любой задаче — напишите «Албери» в комментарии, "
    "я вижу контекст задачи и помогу прямо там."
)
_NUDGE_EVERY_H = int(os.getenv("B24_CHECKIN_NUDGE_HOURS", "72"))  # раз в 3 дня


def _send_dms(offers_by_user: dict[int, list[dict[str, Any]]], main_bot_id: Any) -> list[str]:
    """Offer-DMs; returns the recipients' names (for the owner's report)."""
    import b24bot
    directory = b24bot._b24_portal_user_directory()
    sent: list[str] = []
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
                sent.append(directory.get(uid, {}).get("name") or str(uid))
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


def _send_nudges(exclude_uids: set[int], main_bot_id: Any) -> list[str]:
    """DM the people who have agent access but DON'T use the agent at all (turns_30d = 0):
    «давайте уже работать». At most once per _NUDGE_EVERY_H (72h = раз в 3 дня), never on the
    same day as an offer-DM, never to bots/the owner account. Returns nudged names."""
    import b24bot
    nudged: list[str] = []
    try:
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT bitrix_user_id, full_name FROM employee_agent_dossier "
                    "WHERE agent_access AND coalesce(turns_30d, 0) = 0 "
                    "AND (last_dm_at IS NULL OR last_dm_at < now() - %s * interval '1 hour')",
                    (_NUDGE_EVERY_H,))
                rows = [dict(r) for r in cur.fetchall()]
    except Exception:  # noqa: BLE001
        logging.warning("task checkin: nudge candidates load failed", exc_info=True)
        return nudged
    bot_uids = {b24bot.to_int(t.get("bot_id")) for t in b24bot._b24_task_targets()}
    for r in rows:
        uid = int(r["bitrix_user_id"])
        if uid in exclude_uids or uid in bot_uids or uid in b24bot._b24_task_bot_author_ids():
            continue
        name = (r.get("full_name") or "").split()[0] or "Коллега"
        ok, err = b24bot._albery_bitrix_notify(_NUDGE_DM.format(name=name),
                                               dialog_id=str(uid), bot_id=main_bot_id)
        if ok:
            nudged.append(r.get("full_name") or str(uid))
            try:
                with pg_connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE employee_agent_dossier SET last_dm_at = now(), "
                            "first_dm_at = coalesce(first_dm_at, now()), updated_at = now() "
                            "WHERE bitrix_user_id = %s", (uid,))
            except Exception:  # noqa: BLE001
                logging.warning("task checkin: nudge stamp failed uid=%s", uid, exc_info=True)
        else:
            logging.warning("task checkin: nudge to %s failed: %s", uid, err)
    return nudged


def _report_to_owner(report: dict[str, Any], offers_by_user: dict[int, list[dict[str, Any]]],
                     dm_names: list[str], nudged: list[str], main_bot_id: Any) -> bool:
    """The owner's DM after every live run: which tasks got recommendations, who was DMed —
    the log the owner asked for («и всё это у нас должно логироваться»)."""
    import b24bot
    from mcp.context_server import _task_deep_link
    target = os.getenv("B24_CHECKIN_REPORT_TO", "22").strip()
    if not target:
        return False
    lines = ["[b]🤖 Ежедневный обход задач — отчёт[/b]"]
    if offers_by_user:
        lines.append("Рекомендации отправлены в задачах:")
        for uid, tasks in offers_by_user.items():
            for t in tasks:
                lines.append(f"— [URL={_task_deep_link(t['id'])}]{t['title'][:70]}[/URL]")
    else:
        lines.append("Подходящих задач для рекомендаций сегодня не нашлось.")
    if dm_names:
        lines.append("Отписался в ЛС: " + ", ".join(dm_names))
    if nudged:
        lines.append("Напомнил о себе (пока не работают с агентом): " + ", ".join(nudged))
    st = report.get("filter_stats") or {}
    lines.append(f"Статистика: задач {report.get('scanned', 0)}, прошли фильтры "
                 f"{report.get('passed_filters', 0)}, отобрано {report.get('offers_posted', 0)}; "
                 f"отсеяно: без доступа {st.get('no_access', 0)}, стоп-слова {st.get('stop_word', 0)}, "
                 f"массовые {st.get('mass', 0)}, с оффером {st.get('offered', 0)}.")
    ok, err = b24bot._albery_bitrix_notify("\n".join(lines), dialog_id=target, bot_id=main_bot_id)
    if not ok:
        logging.warning("task checkin: owner report failed: %s", err)
    return ok


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
        dm_names = _send_dms(offers_by_user, main_bot)
        report["dms_sent"] = len(dm_names)
        report["dm_names"] = dm_names
        # «Давайте уже работать» for access-holders who don't use the agent (≤ раз в 3 дня).
        nudged = _send_nudges(set(offers_by_user), main_bot)
        report["nudged"] = nudged
        # Owner's log-DM: which tasks got recommendations, who was written to.
        report["owner_report_sent"] = _report_to_owner(
            {**report, "offers_posted": offers_posted}, offers_by_user, dm_names, nudged, main_bot)
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
