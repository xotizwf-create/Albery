#!/usr/bin/env python3
"""Post-deploy smoke for Albery — catches wiring breaks the login-check can't see.

Run on the server after EVERY deploy/restart:
    cd /var/www/albery && .venv/bin/python scripts/deploy_smoke.py

Checks:
1. Every workflow name referenced by mcp/context_server.py via app_workflow_function("...")
   actually resolves. (2026-07-02 incident: a move-only refactor step relocated
   bitrix_method_call out of app.py and silently broke task creation for a day.)
2. All three MCP endpoints answer tools/list with sane tool counts.
3. The web app answers /login with 200.

Exit code 0 = safe to walk away; 1 = do not leave the deploy like this.
"""
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BASE / ".env")

APP_URL = "http://127.0.0.1:5002"
MIN_TOOLS = {"/mcp": 60, "/mcp-ops": 55, "/mcp-faq": 10}
TOKEN_ENV = {
    "/mcp": "MCP_SHARED_SECRET",
    "/mcp-ops": "MCP_OPS_SHARED_SECRET",
    "/mcp-faq": "MCP_FAQ_SHARED_SECRET",
}

failures: list[str] = []

# 1. Every app_workflow_function("...") reference must resolve.
from mcp.context_server import app_workflow_function  # noqa: E402

source = (BASE / "mcp" / "context_server.py").read_text(encoding="utf-8")
names = sorted(set(re.findall(r'app_workflow_function\(\s*"([A-Za-z0-9_]+)"', source)))
bad = 0
for name in names:
    try:
        app_workflow_function(name)
    except Exception as exc:  # noqa: BLE001
        bad += 1
        failures.append(f"workflow '{name}' не резолвится: {exc}")
print(f"workflow-имена: {len(names)} проверено, битых {bad}")


def post_json(url: str, payload: dict, headers: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, json.loads(resp.read().decode())


# 2. MCP endpoints must list their tools.
for path, min_tools in MIN_TOOLS.items():
    token = os.getenv(TOKEN_ENV[path], "").strip()
    if not token:
        failures.append(f"{path}: секрет {TOKEN_ENV[path]} не найден в env")
        continue
    try:
        status, body = post_json(
            f"{APP_URL}{path}", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            {"Authorization": f"Bearer {token}"},
        )
        tools = (body.get("result") or {}).get("tools") or []
        if status != 200 or len(tools) < min_tools:
            failures.append(f"{path}: status={status}, tools={len(tools)} (ожидалось >={min_tools})")
        else:
            print(f"{path}: OK, {len(tools)} инструментов")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"{path}: {exc}")

# 3. The site itself must be up.
try:
    with urllib.request.urlopen(f"{APP_URL}/login", timeout=15) as resp:
        if resp.status != 200:
            failures.append(f"/login: status={resp.status}")
        else:
            print("/login: OK")
except Exception as exc:  # noqa: BLE001
    failures.append(f"/login: {exc}")

if failures:
    print("SMOKE FAILED:")
    for item in failures:
        print(" -", item)
    sys.exit(1)
print("SMOKE OK")
