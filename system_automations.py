"""System automation rows: write-through control + run sync (owner 2026-07-17).

kind='system' rows in agent_automations mirror real executors named by system_key
(migration 057). This module makes the mirror live in both directions:

  - a sync thread pulls last runs / schedule / paused-state from `hermes cron list`
    into the hermes:* rows every few minutes, so the tab shows real activity;
  - edit_system()/run_system() push UI edits BACK to the executor: `hermes cron
    edit/pause/resume/run` for hermes:* rows, an in-place rewrite of the cron line in
    /etc/cron.d/<file> for crond:* rows (the app runs as root). app:* rows are read by
    their own thread (task_checkin consults its row), so the row itself is the truth.

cron.d scripts report their runs via shared.automation_registry.mark_system_run.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
import time

from datetime import datetime
from typing import Any

log = logging.getLogger("system_automations")

_HERMES_TIMEOUT_S = 45
_SYNC_INTERVAL_S = int(os.getenv("SYSTEM_AUTOMATION_SYNC_INTERVAL_S", "300"))

# crond system_key -> (cron.d file, substring that identifies the job's line)
CROND_LINES: dict[str, tuple[str, str]] = {
    "crond:albery-funnel-control:check": ("albery-funnel-control", "funnel_control.py check"),
    "crond:albery-funnel-control:summary": ("albery-funnel-control", "funnel_control.py summary"),
    "crond:albery-novinki-watch:main": ("albery-novinki-watch", "novinki_watch.py"),
}
_OFF_PREFIX = "#OFF "


def _hermes_bin() -> str:
    return shutil.which("hermes") or "/usr/local/bin/hermes"


def _run_hermes(*args: str) -> tuple[bool, str]:
    try:
        proc = subprocess.run([_hermes_bin(), "cron", *args], capture_output=True, text=True,
                              timeout=_HERMES_TIMEOUT_S)
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode == 0, out.strip()
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def hermes_snapshot() -> dict[str, dict[str, Any]]:
    """Parse `hermes cron list` into {job_name: {job_id, active, schedule, last_run, last_status, last_error}}."""
    ok, out = _run_hermes("list")
    if not ok:
        raise RuntimeError(f"hermes cron list failed: {out[:200]}")
    jobs: dict[str, dict[str, Any]] = {}
    current: dict[str, Any] | None = None
    for raw in out.splitlines():
        line = raw.strip()
        m = re.match(r"^([0-9a-f]{8,16})\s+\[(active|paused)\]$", line)
        if m:
            current = {"job_id": m.group(1), "active": m.group(2) == "active",
                       "schedule": None, "last_run": None, "last_status": None, "last_error": None}
            continue
        if current is None:
            continue
        if line.startswith("Name:"):
            jobs[line.split(":", 1)[1].strip()] = current
        elif line.startswith("Schedule:"):
            current["schedule"] = line.split(":", 1)[1].strip()
        elif line.startswith("Last run:"):
            rest = line.split(":", 1)[1].strip()
            # "2026-07-17T17:25:33.284026+03:00  ok" | "...  error: <message>"
            parts = rest.split(None, 1)
            if parts:
                try:
                    current["last_run"] = datetime.fromisoformat(parts[0])
                except ValueError:
                    current["last_run"] = None
            tail = parts[1].strip() if len(parts) > 1 else ""
            if tail.startswith("error"):
                current["last_status"] = "error"
                current["last_error"] = tail.split(":", 1)[1].strip()[:500] if ":" in tail else tail[:500]
            elif tail:
                current["last_status"] = tail.split()[0][:20]
    return jobs


# --- Sync loop: hermes truth -> registry rows -------------------------------------------------

def _sync_once() -> None:
    from app import pg_connect
    snapshot = hermes_snapshot()
    with pg_connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SELECT id, system_key, schedule, is_active, last_run_at, last_status "
                            "FROM agent_automations WHERE kind = 'system' AND system_key LIKE 'hermes:%'")
                for row in cur.fetchall():
                    job = snapshot.get(row["system_key"].split(":", 1)[1])
                    if not job:
                        continue
                    cur.execute(
                        "UPDATE agent_automations SET last_run_at = COALESCE(%s, last_run_at), "
                        "last_status = COALESCE(%s, last_status), last_error = %s, "
                        "schedule = COALESCE(%s, schedule), is_active = %s, updated_at = now() "
                        "WHERE id = %s AND (last_run_at IS DISTINCT FROM %s OR is_active <> %s "
                        "OR schedule IS DISTINCT FROM %s OR last_error IS DISTINCT FROM %s)",
                        (job["last_run"], job["last_status"], job["last_error"], job["schedule"],
                         job["active"], row["id"],
                         job["last_run"], job["active"], job["schedule"], job["last_error"]),
                    )


def _sync_loop() -> None:
    time.sleep(150)  # after boot; offset from the agent-automations scheduler
    while True:
        try:
            _sync_once()
        except Exception:  # noqa: BLE001
            log.warning("hermes cron sync failed", exc_info=True)
        time.sleep(_SYNC_INTERVAL_S)


# --- Write-through editing --------------------------------------------------------------------

def _crond_path(fname: str) -> str:
    return os.path.join("/etc/cron.d", fname)


def _rewrite_crond(system_key: str, schedule: str | None, is_active: bool | None) -> str | None:
    fname, marker = CROND_LINES[system_key]
    path = _crond_path(fname)
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError as exc:
        return f"cron-файл {path} недоступен: {exc}"
    hit = False
    for i, line in enumerate(lines):
        body = line[len(_OFF_PREFIX):] if line.startswith(_OFF_PREFIX) else line
        if body.lstrip().startswith("#") or marker not in body:
            continue
        fields = body.split()
        if len(fields) < 7:
            continue
        hit = True
        if schedule is not None:
            body = " ".join(schedule.split() + fields[5:]) + "\n"
        disabled = line.startswith(_OFF_PREFIX)
        if is_active is None:
            disabled_next = disabled
        else:
            disabled_next = not is_active
        lines[i] = (_OFF_PREFIX + body) if disabled_next else body
        break
    if not hit:
        return f"строка задания не найдена в {path} (маркер «{marker}»)"
    tmp = path + ".tmp-albery"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.writelines(lines)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
    return None


def edit_system(row: dict[str, Any], schedule: str | None, is_active: bool | None) -> str | None:
    """Push schedule/enabled to the real executor. Returns an error string or None."""
    key = row.get("system_key") or ""
    if key.startswith("hermes:"):
        job_name = key.split(":", 1)[1]
        try:
            job = hermes_snapshot().get(job_name)
        except Exception as exc:  # noqa: BLE001
            return f"hermes cron недоступен: {str(exc)[:200]}"
        if not job:
            return f"задание «{job_name}» не найдено в hermes cron"
        if schedule is not None and schedule != job["schedule"]:
            ok, out = _run_hermes("edit", job["job_id"], "--schedule", schedule)
            if not ok:
                return f"hermes cron edit: {out[:300]}"
        if is_active is not None and is_active != job["active"]:
            ok, out = _run_hermes("resume" if is_active else "pause", job["job_id"])
            if not ok:
                return f"hermes cron {'resume' if is_active else 'pause'}: {out[:300]}"
        return None
    if key in CROND_LINES:
        return _rewrite_crond(key, schedule, is_active)
    if key.startswith("app:"):
        return None  # the in-app thread reads its registry row — the row IS the truth
    return "у этой системной автоматизации не указан исполнитель (system_key) — правится только на сервере"


def run_system(row: dict[str, Any]) -> str | None:
    """Fire the executor once, out of schedule. Returns an error string or None."""
    key = row.get("system_key") or ""
    if key.startswith("hermes:"):
        job_name = key.split(":", 1)[1]
        try:
            job = hermes_snapshot().get(job_name)
        except Exception as exc:  # noqa: BLE001
            return f"hermes cron недоступен: {str(exc)[:200]}"
        if not job:
            return f"задание «{job_name}» не найдено в hermes cron"
        ok, out = _run_hermes("run", job["job_id"])
        return None if ok else f"hermes cron run: {out[:300]}"
    if key in CROND_LINES:
        fname, marker = CROND_LINES[key]
        try:
            with open(_crond_path(fname), encoding="utf-8") as f:
                for line in f:
                    body = line[len(_OFF_PREFIX):] if line.startswith(_OFF_PREFIX) else line
                    if marker in body and not body.lstrip().startswith("#"):
                        fields = body.split()
                        cmd = " ".join(fields[6:])  # after 5 cron fields + user
                        subprocess.Popen(["/bin/bash", "-lc", cmd], start_new_session=True,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        return None
        except OSError as exc:
            return f"cron-файл недоступен: {exc}"
        return "строка задания не найдена в cron-файле"
    if key.startswith("app:"):
        return "эта автоматизация выполняется внутри приложения строго по расписанию"
    return "у этой системной автоматизации не указан исполнитель (system_key)"


def start_sync_thread() -> None:
    if os.getenv("SYSTEM_AUTOMATION_SYNC", "1").strip() != "0":
        threading.Thread(target=_sync_loop, daemon=True, name="system-automations-sync").start()
