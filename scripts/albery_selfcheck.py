#!/usr/bin/env python3
"""Hourly self-check for the Albery box: scans the last hour for silent degradations and
alerts the Telegram notifications group only when something crossed a threshold.

Signals:
- HTTP 500 on /mcp* endpoints (tools broken while the site looks healthy);
- bot-turn failures in the journal (timeouts, failed/empty hermes runs, queue overflow);
- bitrix_bot_interactions rows with status<>'ok' or latency >= 300s in the last hour;
- available RAM below 150 MB (the box has 2 GB and has been OOM-killed before);
- батч-синк run_daily_sync: последний run_finished старше 2 часов или завершился с
  упавшим шагом (июль-2026 батч молча умирал 10 дней — теперь такое ловится за час);
- systemd failed units и заполнение диска / от 85%;
- hermes-cron джобы, у которых последний прогон упал (дедуп: одна и та же ошибка
  алертится один раз, повторно — только когда изменится).

Installed as systemd albery-selfcheck.timer (hourly). Silent when everything is fine.
Одинаковый набор проблем не спамит: повторный алерт не чаще одного раза в 6 часов.
"""
import hashlib
import json
import logging
import os
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent.parent
load_dotenv(BASE / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

STATE_PATH = Path("/var/log/albery/selfcheck_state.json")
SYNC_LOG = Path(os.getenv("ALBERY_DAILY_SYNC_LOG", "/var/log/albery/daily-sync.log"))
HERMES = "/usr/local/bin/hermes" if Path("/usr/local/bin/hermes").exists() else "hermes"


def sh(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout or ""
    except Exception as exc:  # noqa: BLE001
        logging.error("selfcheck command failed %s: %s", cmd[0], exc)
        return ""


def tg_token() -> str:
    token = os.getenv("ALBERY_TG_BOT_TOKEN", "").strip()
    if token:
        return token
    try:
        for line in Path("/root/.hermes/.env").read_text(encoding="utf-8").splitlines():
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def notify(text: str) -> None:
    token = tg_token()
    chat_id = os.getenv("ALBERY_ERROR_REPORT_TG_CHAT", "-5283789593").strip()
    if not token or not chat_id:
        logging.error("selfcheck: telegram token/chat not configured")
        return
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        timeout=20,
    )
    if not (resp.ok and resp.json().get("ok")):
        logging.error("selfcheck: telegram delivery failed: %s", resp.text[:200])


try:
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
except Exception:  # noqa: BLE001
    state = {}

problems: list[str] = []

journal = sh(["journalctl", "-u", "albery", "--since", "-65min", "--no-pager"])
mcp500 = sum(1 for line in journal.splitlines() if '" 500 -' in line and "/mcp" in line)
if mcp500:
    problems.append(f"HTTP 500 на MCP-эндпоинтах: {mcp500}")
for marker, label in (
    ("hermes timed out", "таймауты ходов бота"),
    ("hermes run failed", "падения прогона hermes"),
    ("hermes brain empty", "пустые ответы мозга"),
    ("slot wait exceeded", "переполнение очереди прогонов"),
):
    count = journal.count(marker)
    if count:
        problems.append(f"{label}: {count}")

sql = (
    "SELECT count(*) FILTER (WHERE status <> 'ok'), "
    "count(*) FILTER (WHERE latency_ms >= 300000) "
    "FROM bitrix_bot_interactions WHERE created_at > now() - interval '65 minutes'"
)
row = sh(["sudo", "-u", "postgres", "psql", "albery", "-tAc", sql]).strip()
if row and "|" in row:
    errors, slow = (int(part or 0) for part in row.split("|"))
    if errors:
        problems.append(f"ходы бота со статусом error: {errors}")
    if slow:
        problems.append(f"ходы дольше 5 минут: {slow}")

for line in sh(["free", "-m"]).splitlines():
    if line.startswith("Mem:"):
        parts = line.split()
        available_mb = int(parts[-1])
        if available_mb < 150:
            problems.append(f"мало свободной памяти: {available_mb} MB available")

# --- Свежесть батч-синка (run_daily_sync, /etc/cron.d/albery-daily-sync) ----------------
# Этим батчем едут чаты, снапшоты задач и drive-документы. Проверяем его СОБСТВЕННЫЙ
# структурный лог: должен быть run_finished не старше 2ч и success=true.
def last_run_finished(path: Path) -> dict | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for raw in reversed(lines[-500:]):
        try:
            ev = json.loads(raw)
        except ValueError:
            continue
        if ev.get("status") == "run_finished":
            return ev
    return None


fin = last_run_finished(SYNC_LOG)
if fin is None:
    problems.append("батч-синк (run_daily_sync): в логе нет ни одного run_finished — "
                    "скрипт падает до конца прогона, см. /var/log/albery/daily-sync.cron.log")
else:
    try:
        ts = datetime.fromisoformat(fin["ts"])
        age = datetime.now(ts.tzinfo) - ts
        if age > timedelta(hours=2):
            problems.append(
                f"батч-синк не завершался {int(age.total_seconds() // 3600)}ч "
                f"(последний run_finished: {fin['ts']})")
        elif not fin.get("success"):
            problems.append("батч-синк завершился с упавшим шагом — "
                            "см. /var/log/albery/daily-sync.log")
    except Exception:  # noqa: BLE001
        problems.append("батч-синк: не удалось разобрать ts последнего run_finished")

# --- systemd failed units + диск --------------------------------------------------------
failed_units = sh(["systemctl", "--failed", "--no-legend"]).strip()
if failed_units:
    names = " ".join(
        line.replace("●", "").replace("*", "").split()[0]
        for line in failed_units.splitlines()[:5] if line.strip())
    problems.append(f"systemd failed units: {names}")

df_lines = sh(["df", "-P", "/"]).splitlines()
if len(df_lines) >= 2:
    try:
        use_pct = int(df_lines[1].split()[4].rstrip("%"))
        if use_pct >= 85:
            problems.append(f"диск / заполнен на {use_pct}%")
    except (IndexError, ValueError):
        pass

# --- hermes-cron джобы с упавшим последним прогоном (дедуп по сигнатуре строки) ---------
cron_out = sh([HERMES, "cron", "list"])
seen_cron = state.get("cron_errors", {}) if isinstance(state.get("cron_errors"), dict) else {}
new_cron_state: dict[str, str] = {}
job_name = None
for raw in cron_out.splitlines():
    s = raw.strip()
    if s.startswith("Name:"):
        job_name = s.split(":", 1)[1].strip()
    elif s.startswith("Last run:") and "error" in s.lower() and job_name:
        new_cron_state[job_name] = s
        if seen_cron.get(job_name) != s:
            detail = s.split("error", 1)[-1].lstrip(":").strip()[:100]
            problems.append(f"hermes-крон «{job_name}» упал: {detail}")
state["cron_errors"] = new_cron_state

# --- Отправка с антиспамом: тот же набор проблем — не чаще раза в 6 часов ----------------
digest = hashlib.sha256("\n".join(problems).encode("utf-8")).hexdigest() if problems else ""
last_digest = str(state.get("last_digest", ""))
last_alert_ts = float(state.get("last_alert_ts", 0) or 0)
should_notify = bool(problems) and (
    digest != last_digest or time.time() - last_alert_ts > 6 * 3600)

if problems:
    if should_notify:
        text = "🩺 Albery selfcheck — за последний час есть проблемы:\n" + "\n".join(
            f"- {p}" for p in problems
        ) + "\n\nДетали: journalctl -u albery (сервер 186)."
        notify(text)
        state["last_digest"] = digest
        state["last_alert_ts"] = time.time()
        logging.warning("selfcheck: %s problem(s) reported", len(problems))
    else:
        logging.warning("selfcheck: %s problem(s), alert suppressed (unchanged, <6h)",
                        len(problems))
else:
    logging.info("selfcheck: clean")
    state["last_digest"] = ""

try:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
except OSError as exc:
    logging.error("selfcheck: state save failed: %s", exc)
