"""Upsert one Albery AI instruction from a local markdown file via the MCP
endpoint on prod. Idempotent — re-runs replace the content.

Usage:
    python scripts/upsert_albery_ai_instruction.py "Cron автоматизации/Zoom задачи — ответ ставь" scripts/ai_instruction_zoom_approval.md
"""
from __future__ import annotations
import json, pathlib, sys, paramiko


def read_env(path: pathlib.Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def main() -> None:
    if len(sys.argv) < 3:
        print("usage: upsert_albery_ai_instruction.py <path> <local_file>", file=sys.stderr)
        sys.exit(2)

    instruction_path = sys.argv[1]
    local_file = pathlib.Path(sys.argv[2])
    if not local_file.is_absolute():
        local_file = pathlib.Path(__file__).resolve().parent.parent / local_file
    content = local_file.read_text(encoding="utf-8")

    pw = read_env(pathlib.Path(__file__).resolve().parent.parent / ".env")["root_password"]
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect("186.246.7.32", username="root", password=pw,
                look_for_keys=False, allow_agent=False, timeout=20)

    # Upload content to a temp file on prod (avoids JSON escape issues for long markdown)
    sftp = cli.open_sftp()
    remote_tmp = "/tmp/ai_instruction_payload.json"
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "upsert_ai_instruction",
            "arguments": {
                "path": instruction_path,
                "content": content,
            },
        },
    }
    with sftp.file(remote_tmp, "wb") as f:
        f.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    sftp.chmod(remote_tmp, 0o600)
    sftp.close()

    cmd = (
        "secret=$(awk -F= '/^MCP_SHARED_SECRET=/{sub(/^[^=]*=/,\"\"); print; exit}' /var/www/albery/.env); "
        f"curl -sS -X POST http://127.0.0.1:5002/mcp/$secret "
        f"-H 'Content-Type: application/json' "
        f"-d @{remote_tmp}; "
        f"rm -f {remote_tmp}"
    )
    print(f"Upsert AI instruction: path={instruction_path}, content len={len(content)}")
    _, out, err = cli.exec_command(cmd, timeout=60)
    o = out.read().decode("utf-8", "replace")
    e = err.read().decode("utf-8", "replace")
    try:
        d = json.loads(o)
        if "error" in d:
            print("MCP error:", d["error"])
            sys.exit(3)
        result = d.get("result", {}).get("structuredContent") or d.get("result")
        print(json.dumps(result, ensure_ascii=False, indent=2)[:1500])
    except Exception:
        print("Raw response:", o[:1500])
    if e.strip():
        print("[stderr]", e.strip(), file=sys.stderr)
    cli.close()


if __name__ == "__main__":
    main()
