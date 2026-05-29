"""Register the Friday owner-weekly Hermes automation on prod and adjust owner-daily.

- owner-weekly: new cron job, Friday 18:00 (Europe/Moscow), prompt =
  scripts/hermes_owner_weekly_prompt.txt, same Telegram delivery as owner-daily.
- owner-daily: skip Friday (schedule -> "0 18 * * 0-4,6") and refresh prompt
  from scripts/hermes_owner_daily_prompt.txt (Evgeniy removed from recipients).

Patches /root/.hermes/cron/jobs.json (with .bak backup) and restarts
hermes-gateway. Reads root_password from local .env (paramiko, no SSH keys).
Idempotent: re-running updates in place, does not duplicate the weekly job.

Run once (after deploying the MCP tool via update_server.sh):
    python scripts/register_hermes_owner_weekly.py
"""
from __future__ import annotations

import json
import pathlib
import secrets
import sys
from datetime import datetime, timezone, timedelta

import paramiko

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / ".env"
WEEKLY_PROMPT_PATH = REPO_ROOT / "scripts" / "hermes_owner_weekly_prompt.txt"
DAILY_PROMPT_PATH = REPO_ROOT / "scripts" / "hermes_owner_daily_prompt.txt"
PROD_HOST = "186.246.7.32"
PROD_JOBS_JSON = "/root/.hermes/cron/jobs.json"

WEEKLY_NAME = "owner-weekly"
DAILY_NAME = "owner-daily"
WEEKLY_EXPR = "0 18 * * 5"          # Friday 18:00
DAILY_EXPR = "0 18 * * 0-4,6"        # every day except Friday, 18:00


def read_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def msk_now_iso() -> str:
    return datetime.now(timezone(timedelta(hours=3))).isoformat()


def main() -> None:
    weekly_prompt = WEEKLY_PROMPT_PATH.read_text(encoding="utf-8").strip()
    daily_prompt = DAILY_PROMPT_PATH.read_text(encoding="utf-8").strip()
    if not weekly_prompt or not daily_prompt:
        print("ERROR: a prompt file is empty", file=sys.stderr)
        sys.exit(2)

    env = read_env()
    password = env.get("root_password") or env.get("ROOT_PASSWORD")
    if not password:
        print("ERROR: root_password missing from .env", file=sys.stderr)
        sys.exit(2)

    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(PROD_HOST, username="root", password=password,
                look_for_keys=False, allow_agent=False, timeout=20)

    sftp = cli.open_sftp()
    with sftp.file(PROD_JOBS_JSON, "rb") as f:
        raw = f.read().decode("utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict) or "jobs" not in data or not isinstance(data["jobs"], list):
        print("ERROR: unexpected jobs.json shape", file=sys.stderr)
        sys.exit(3)
    jobs = data["jobs"]

    daily = next((j for j in jobs if isinstance(j, dict) and j.get("name") == DAILY_NAME), None)
    if daily is None:
        print(f"ERROR: '{DAILY_NAME}' job not found", file=sys.stderr)
        sys.exit(3)

    # 1) owner-daily: skip Friday + refresh prompt. Reset next_run_at so the
    # scheduler recomputes from the new expr (otherwise the cached Friday run
    # would still fire).
    daily["schedule"] = {"kind": "cron", "expr": DAILY_EXPR, "display": DAILY_EXPR}
    daily["schedule_display"] = DAILY_EXPR
    daily["prompt"] = daily_prompt
    daily["next_run_at"] = None

    # 2) owner-weekly: create or update in place
    weekly = next((j for j in jobs if isinstance(j, dict) and j.get("name") == WEEKLY_NAME), None)
    if weekly is None:
        weekly = dict(daily)  # mirror owner-daily structure
        weekly["id"] = secrets.token_hex(6)
        weekly["name"] = WEEKLY_NAME
        weekly["created_at"] = msk_now_iso()
        weekly["repeat"] = {"times": None, "completed": 0}
        for k in ("last_run_at", "last_status", "last_error", "last_delivery_error",
                  "paused_at", "paused_reason", "next_run_at"):
            weekly[k] = None
        weekly["state"] = "scheduled"
        weekly["enabled"] = True
        jobs.append(weekly)
        action = "created"
    else:
        action = "updated"
    weekly["prompt"] = weekly_prompt
    weekly["schedule"] = {"kind": "cron", "expr": WEEKLY_EXPR, "display": WEEKLY_EXPR}
    weekly["schedule_display"] = WEEKLY_EXPR
    weekly["deliver"] = daily.get("deliver")  # same Telegram chat as owner-daily
    weekly["enabled"] = True

    data["updated_at"] = msk_now_iso()

    backup_path = PROD_JOBS_JSON + ".bak"
    with sftp.file(backup_path, "wb") as f:
        f.write(raw.encode("utf-8"))
    new_raw = json.dumps(data, ensure_ascii=False, indent=2)
    with sftp.file(PROD_JOBS_JSON, "wb") as f:
        f.write(new_raw.encode("utf-8"))
    sftp.chmod(PROD_JOBS_JSON, 0o600)
    sftp.close()
    print(f"owner-daily -> '{DAILY_EXPR}', prompt refreshed")
    print(f"owner-weekly {action} -> '{WEEKLY_EXPR}', deliver={weekly.get('deliver')}")

    print("Restarting hermes-gateway")
    _, out, err = cli.exec_command("systemctl restart hermes-gateway && systemctl is-active hermes-gateway", timeout=30)
    o = out.read().decode("utf-8", "replace"); e = err.read().decode("utf-8", "replace")
    print(o.rstrip())
    if e.strip():
        print("[stderr]", e.rstrip(), file=sys.stderr)
    cli.close()
    print("Done.")


if __name__ == "__main__":
    main()
