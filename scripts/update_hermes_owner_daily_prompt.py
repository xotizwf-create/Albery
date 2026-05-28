"""Patch /root/.hermes/cron/jobs.json on prod: replace the prompt of the
`owner-daily` cron job with scripts/hermes_owner_daily_prompt.txt and restart
hermes-gateway. Reads root_password from local .env (paramiko, no SSH keys).

Run once:
    python scripts/update_hermes_owner_daily_prompt.py
"""
from __future__ import annotations

import json
import pathlib
import sys
import paramiko

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / ".env"
PROMPT_PATH = REPO_ROOT / "scripts" / "hermes_owner_daily_prompt.txt"
PROD_HOST = "186.246.7.32"
PROD_JOBS_JSON = "/root/.hermes/cron/jobs.json"
JOB_NAME = "owner-daily"


def read_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def main() -> None:
    new_prompt = PROMPT_PATH.read_text(encoding="utf-8").strip()
    if not new_prompt:
        print("ERROR: prompt file is empty", file=sys.stderr)
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

    jobs = data if isinstance(data, list) else data.get("jobs", data)
    items = jobs if isinstance(jobs, list) else list(jobs.values())

    target = None
    for j in items:
        if isinstance(j, dict) and j.get("name") == JOB_NAME:
            target = j
            break
    if target is None:
        print(f"ERROR: cron job '{JOB_NAME}' not found in {PROD_JOBS_JSON}", file=sys.stderr)
        sys.exit(3)

    old_prompt = target.get("prompt") or ""
    if old_prompt.strip() == new_prompt.strip():
        print("Prompt is already up to date. Nothing to do.")
        cli.close()
        return

    backup_path = PROD_JOBS_JSON + ".bak"
    print(f"Writing backup to {backup_path}")
    with sftp.file(backup_path, "wb") as f:
        f.write(raw.encode("utf-8"))

    target["prompt"] = new_prompt
    new_raw = json.dumps(data, ensure_ascii=False, indent=2)
    print(f"Patching {PROD_JOBS_JSON} (prompt len {len(old_prompt)} -> {len(new_prompt)})")
    with sftp.file(PROD_JOBS_JSON, "wb") as f:
        f.write(new_raw.encode("utf-8"))
    sftp.chmod(PROD_JOBS_JSON, 0o600)
    sftp.close()

    print("Restarting hermes-gateway")
    _, out, err = cli.exec_command("systemctl restart hermes-gateway && systemctl is-active hermes-gateway",
                                    timeout=30)
    o = out.read().decode("utf-8", "replace")
    e = err.read().decode("utf-8", "replace")
    print(o.rstrip())
    if e.strip():
        print("[stderr]", e.rstrip(), file=sys.stderr)
    cli.close()
    print("Done.")


if __name__ == "__main__":
    main()
