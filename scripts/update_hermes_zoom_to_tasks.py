"""Deploy updated zoom-to-tasks prompt + watchdog wrapper to prod.

Usage:
    python scripts/update_hermes_zoom_to_tasks.py
    python scripts/update_hermes_zoom_to_tasks.py --reset-and-run zoom_call_id_uuid
        # additionally: delete the saved report for that zoom call via MCP,
        # clear the cooldown fingerprint, and run the watchdog once.
"""
from __future__ import annotations
import argparse, pathlib, shlex, sys, paramiko

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / ".env"
LOCAL_PROMPT = REPO_ROOT / "scripts" / "hermes_zoom_to_tasks_prompt.txt"
LOCAL_WATCHDOG = REPO_ROOT / "scripts" / "hermes_zoom_watchdog.sh"
PROD_HOST = "186.246.7.32"
PROD_PROMPT = "/root/.hermes/scripts/hermes_zoom_to_tasks_prompt.txt"
PROD_WATCHDOG = "/root/.hermes/scripts/zoom_watchdog.sh"
PROD_STATE = "/root/.hermes/state/zoom_watchdog.last"


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
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset-and-run", metavar="ZOOM_CALL_ID",
                        help="Delete saved report for this zoom_call_id and run watchdog once")
    args = parser.parse_args()

    pw = read_env()["root_password"]
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(PROD_HOST, username="root", password=pw,
                look_for_keys=False, allow_agent=False, timeout=20)

    sftp = cli.open_sftp()

    # Backup old watchdog
    print(f"Backup {PROD_WATCHDOG} -> {PROD_WATCHDOG}.bak")
    try:
        with sftp.file(PROD_WATCHDOG, "rb") as src, sftp.file(PROD_WATCHDOG + ".bak", "wb") as dst:
            dst.write(src.read())
    except IOError:
        print("  (no existing watchdog to back up)")

    # Upload prompt + watchdog
    for local, remote in [(LOCAL_PROMPT, PROD_PROMPT), (LOCAL_WATCHDOG, PROD_WATCHDOG)]:
        print(f"Upload {local.name} -> {remote}")
        with sftp.file(remote, "wb") as f:
            # Write with LF endings (strip any \r introduced by Windows tooling)
            f.write(local.read_text(encoding="utf-8").replace("\r\n", "\n").encode("utf-8"))
    sftp.chmod(PROD_WATCHDOG, 0o700)
    sftp.chmod(PROD_PROMPT, 0o600)
    sftp.close()

    if args.reset_and_run:
        call_id = args.reset_and_run
        # 1. Delete report via MCP
        del_cmd = (
            "secret=$(awk -F= '/^MCP_SHARED_SECRET=/{sub(/^[^=]*=/,\"\"); print; exit}' /var/www/albery/.env); "
            "curl -sS -X POST http://127.0.0.1:5002/mcp/$secret "
            "-H 'Content-Type: application/json' "
            f"-d '{{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{{\"name\":\"delete_zoom_call_report\",\"arguments\":{{\"call_id\":\"{call_id}\",\"confirm\":true}}}}}}'"
        )
        print(f"\nDelete saved report for {call_id} via MCP")
        _, out, err = cli.exec_command(del_cmd, timeout=60)
        print(out.read().decode("utf-8", "replace").rstrip())
        e = err.read().decode("utf-8", "replace")
        if e.strip():
            print("[stderr]", e.rstrip())

        # 2. Clear cooldown
        print(f"\nClear cooldown: rm -f {PROD_STATE}")
        _, out, _ = cli.exec_command(f"rm -f {PROD_STATE} && ls {PROD_STATE} 2>&1; echo done", timeout=10)
        print(out.read().decode("utf-8", "replace").rstrip())

        # 3. Run watchdog once, streaming
        print(f"\nRun watchdog manually (may take 3-10 minutes for LLM):")
        chan = cli.get_transport().open_session()
        chan.get_pty()
        chan.exec_command(f"bash {shlex.quote(PROD_WATCHDOG)} 2>&1")
        while True:
            if chan.recv_ready():
                sys.stdout.write(chan.recv(4096).decode("utf-8", "replace"))
                sys.stdout.flush()
            if chan.exit_status_ready() and not chan.recv_ready():
                break
        while chan.recv_ready():
            sys.stdout.write(chan.recv(4096).decode("utf-8", "replace"))
        ec = chan.recv_exit_status()
        print(f"\n[watchdog exit {ec}]")

    cli.close()
    print("Done.")


if __name__ == "__main__":
    main()
