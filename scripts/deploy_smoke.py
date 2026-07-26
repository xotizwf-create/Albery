#!/usr/bin/env python3
"""Post-deploy smoke for Albery — catches wiring breaks the login-check can't see.

Run on the server after EVERY deploy/restart:
    cd /var/www/albery && .venv/bin/python scripts/deploy_smoke.py

Checks:
1. Every workflow name referenced by mcp/context_server.py via app_workflow_function("...")
   actually resolves. (2026-07-02 incident: a move-only refactor step relocated
   bitrix_method_call out of app.py and silently broke task creation for a day.)
2. Core MCP endpoints answer tools/list with sane tool counts.
3. The dedicated customer connector is active and exposes exactly zero tools.
4. The site and standalone funnel workspace routes are wired.
5. When the workspace is enabled, its password, Telegram right and rollout flags are coherent.

Exit code 0 = safe to walk away; 1 = do not leave the deploy like this.
"""
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from dotenv import load_dotenv  # noqa: E402
from shared.db import assert_tables_exist, connect  # noqa: E402

load_dotenv(BASE / ".env")

APP_URL = "http://127.0.0.1:5002"
MIN_TOOLS = {"/mcp": 60, "/mcp-ops": 55, "/mcp-faq": 10, "/mcp-core": 20, "/mcp-ops-core": 20}
TOKEN_ENV = {
    "/mcp": "MCP_SHARED_SECRET",
    "/mcp-ops": "MCP_OPS_SHARED_SECRET",
    "/mcp-faq": "MCP_FAQ_SHARED_SECRET",
    "/mcp-core": "MCP_SHARED_SECRET",
    "/mcp-ops-core": "MCP_OPS_SHARED_SECRET",
}

failures: list[str] = []
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"", "0", "false", "no", "off"}

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


def get_json(url: str) -> tuple[int, dict, str]:
    req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, json.loads(resp.read().decode()), resp.geturl()


def env_flag(name: str, default: str = "0") -> bool:
    raw = os.getenv(name, default).strip().lower()
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False
    failures.append(f"{name}: некорректное boolean-значение")
    return False


def validate_ai_allowlist(raw: str) -> bool:
    """Reject typos instead of silently widening or unexpectedly emptying rollout."""

    value = raw.strip()
    if not value or value == "*":
        return True
    parts = re.split(r"[,;]", value)
    if not parts or any(not part.strip() for part in parts):
        return False
    for part in parts:
        item = part.strip()
        if not item.isdigit():
            return False
        number = int(item)
        if number <= 0 or number > 9_223_372_036_854_775_807:
            return False
    return True


def looks_like_password_hash(value: object) -> bool:
    text = str(value or "").strip()
    return text.startswith(("scrypt:", "pbkdf2:"))


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

# 2b. Two-stage tools on the core connectors must actually work.
def call_mcp(path: str, tool: str, arguments: dict) -> dict:
    token = os.getenv(TOKEN_ENV[path], "").strip()
    _status, body = post_json(
        f"{APP_URL}{path}",
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": tool, "arguments": arguments}},
        {"Authorization": f"Bearer {token}"},
    )
    return body


try:
    body = call_mcp("/mcp-core", "find_tool", {"query": "delete task"})
    text = json.dumps(body, ensure_ascii=False)
    if "delete_bitrix_task" not in text:
        failures.append(f"/mcp-core find_tool('delete task') не нашёл delete_bitrix_task: {text[:200]}")
    else:
        print("/mcp-core find_tool: OK")
    body = call_mcp("/mcp-ops-core", "call_tool", {"name": "health", "arguments": {}})
    text = json.dumps(body, ensure_ascii=False)
    if "error" in body or "ok" not in text.lower():
        failures.append(f"/mcp-ops-core call_tool(health) не прошёл: {text[:200]}")
    else:
        print("/mcp-ops-core call_tool: OK")
except Exception as exc:  # noqa: BLE001
    failures.append(f"core meta-tools: {exc}")

# 3. Customer text must reach a valid connector with no callable tools.
customer_slug = (
    os.getenv("FUNNEL_WORKSPACE_CUSTOMER_TOOLSET_SLUG", "iu-customer-runtime").strip()
    or "iu-customer-runtime"
)
try:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT mcp_token, is_active, bitrix_bot_id, telegram_bot_token
                  FROM agents
                 WHERE slug = %s
                """,
                (customer_slug,),
            )
            customer_agent = cur.fetchone()
    if not customer_agent:
        failures.append(f"agent-{customer_slug}: DB row отсутствует")
    elif not customer_agent["is_active"]:
        failures.append(f"agent-{customer_slug}: выключен")
    elif customer_agent.get("bitrix_bot_id") or customer_agent.get("telegram_bot_token"):
        failures.append(f"agent-{customer_slug}: не должен иметь Bitrix/Telegram bridge")
    elif not str(customer_agent.get("mcp_token") or "").strip():
        failures.append(f"agent-{customer_slug}: connector token отсутствует")
    else:
        connector_token = str(customer_agent["mcp_token"]).strip()
        config_path = Path(
            os.getenv("HERMES_CONFIG", "/root/.hermes/config.yaml")
        ).expanduser()
        try:
            config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            connector = (config.get("mcp_servers") or {}).get(
                f"agent-{customer_slug}"
            )
            if not isinstance(connector, dict):
                failures.append(
                    f"agent-{customer_slug}: connector отсутствует в Hermes config"
                )
            else:
                connector_url = str(connector.get("url") or "").strip()
                parsed = urllib.parse.urlparse(connector_url)
                expected_path = f"/mcp-agent/{customer_slug}/{connector_token}"
                if connector.get("enabled") is not True:
                    failures.append(
                        f"agent-{customer_slug}: connector выключен в Hermes config"
                    )
                elif (
                    parsed.scheme not in {"http", "https"}
                    or not parsed.netloc
                    or parsed.path.rstrip("/") != expected_path
                    or parsed.query
                    or parsed.fragment
                ):
                    failures.append(
                        f"agent-{customer_slug}: connector URL не совпадает с DB token"
                    )
                else:
                    print(
                        f"agent-{customer_slug}: Hermes config OK "
                        "(URL/token скрыты)"
                    )
        except Exception as exc:  # noqa: BLE001
            # The config contains connector secrets; report only the exception class.
            failures.append(
                f"agent-{customer_slug}: Hermes config не читается "
                f"({type(exc).__name__})"
            )
        try:
            status, body = post_json(
                (
                    f"{APP_URL}/mcp-agent/{customer_slug}/"
                    f"{connector_token}"
                ),
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            )
            tools = (body.get("result") or {}).get("tools") or []
            if status != 200 or tools:
                failures.append(
                    f"agent-{customer_slug}: status={status}, tools={len(tools)} (ожидалось 0)"
                )
            else:
                print(f"agent-{customer_slug}: OK, 0 инструментов")
        except Exception as exc:  # noqa: BLE001
            # Never stringify the request URL here: it contains the connector token.
            failures.append(
                f"agent-{customer_slug}: tools/list не прошёл ({type(exc).__name__})"
            )
except Exception as exc:  # noqa: BLE001
    failures.append(f"agent-{customer_slug}: DB-проверка не прошла ({type(exc).__name__})")

# The two long-lived workers are part of the workspace data path.  Keep this
# production-only so a local/container smoke without systemd remains useful.
if Path("/run/systemd/system").is_dir():
    for service_name in ("albery-tg.service", "hermes-gateway.service"):
        try:
            check = subprocess.run(
                ["systemctl", "is-active", "--quiet", service_name],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
            if check.returncode:
                failures.append(f"{service_name}: service не active")
            else:
                print(f"{service_name}: active")
        except Exception as exc:  # noqa: BLE001
            failures.append(
                f"{service_name}: status не проверен ({type(exc).__name__})"
            )
else:
    print("systemd services: SKIP (systemd environment not detected)")

# 4. The site itself and the separate workspace route must be up.
try:
    with urllib.request.urlopen(f"{APP_URL}/login", timeout=15) as resp:
        if resp.status != 200:
            failures.append(f"/login: status={resp.status}")
        else:
            print("/login: OK")
except Exception as exc:  # noqa: BLE001
    failures.append(f"/login: {exc}")

try:
    with urllib.request.urlopen(f"{APP_URL}/agent-funnels", timeout=15) as resp:
        if resp.status != 200 or not resp.geturl().rstrip("/").endswith("/agent-funnels"):
            failures.append(
                f"/agent-funnels: status={resp.status}, final_path={urllib.parse.urlparse(resp.geturl()).path}"
            )
        else:
            print("/agent-funnels: OK")
except Exception as exc:  # noqa: BLE001
    failures.append(f"/agent-funnels: {type(exc).__name__}")

try:
    status, workspace_session, _final_url = get_json(
        f"{APP_URL}/api/funnel-workspace/session"
    )
    if status != 200 or "authenticated" not in workspace_session:
        failures.append(
            f"workspace session API: status={status}, payload shape invalid"
        )
    else:
        print("workspace session API: OK")
except Exception as exc:  # noqa: BLE001
    failures.append(f"workspace session API: {type(exc).__name__}")

try:
    assert_tables_exist(
        [
            "funnel_workspace_sources",
            "funnel_workspace_conversations",
            "funnel_workspace_messages",
            "funnel_workspace_control_events",
            "funnel_workspace_updates",
            "funnel_workspace_ai_jobs",
            "funnel_workspace_outbox",
            "funnel_workspace_crm_actions",
            "funnel_workspace_settings",
        ],
        hint="Apply migration 070_funnel_workspace.sql.",
    )
    print("workspace tables: OK")
except Exception as exc:  # noqa: BLE001
    failures.append(f"workspace tables: {exc}")

# 5. Enabling the workspace is a cutover: the legacy sender must be off and the new
# transport must actually hold the Telegram Business reply right.
workspace_enabled = env_flag("FUNNEL_WORKSPACE_ENABLED")
ai_enabled = env_flag("FUNNEL_WORKSPACE_AI_ENABLED")
allowlist_raw = os.getenv("FUNNEL_WORKSPACE_AI_ALLOW_IDS", "")
if ai_enabled and not workspace_enabled:
    failures.append(
        "FUNNEL_WORKSPACE_AI_ENABLED включён при выключенном FUNNEL_WORKSPACE_ENABLED"
    )
if not validate_ai_allowlist(allowlist_raw):
    failures.append(
        "FUNNEL_WORKSPACE_AI_ALLOW_IDS: нужны положительные Telegram ID "
        "через запятую/точку с запятой либо ровно '*'"
    )
if ai_enabled and not allowlist_raw.strip():
    failures.append(
        "AI включён, но тестовый allowlist пуст (используйте ID или явный '*')"
    )

try:
    reply_window_hours = int(
        os.getenv("FUNNEL_WORKSPACE_REPLY_WINDOW_HOURS", "24").strip()
    )
except ValueError:
    reply_window_hours = 0
if not 1 <= reply_window_hours <= 48:
    failures.append("FUNNEL_WORKSPACE_REPLY_WINDOW_HOURS должен быть целым от 1 до 48")

crm_id_field = os.getenv("FUNNEL_WORKSPACE_CRM_TELEGRAM_ID_FIELD", "").strip()
legacy_username_field = os.getenv(
    "CRM_TELEGRAM_FIELD", "UF_CRM_1784296997"
).strip()
if (
    crm_id_field
    and legacy_username_field
    and crm_id_field.casefold() == legacy_username_field.casefold()
):
    failures.append(
        "FUNNEL_WORKSPACE_CRM_TELEGRAM_ID_FIELD должен отличаться "
        "от legacy CRM_TELEGRAM_FIELD"
    )

if workspace_enabled:
    if env_flag("TG_BUSINESS_AUTOREPLY"):
        failures.append("workspace включён, но TG_BUSINESS_AUTOREPLY всё ещё включён")
    if env_flag("OPENLINE_AGENT_ENABLED"):
        failures.append("workspace включён, но OPENLINE_AGENT_ENABLED всё ещё включён")

    password_hash = os.getenv("FUNNEL_WORKSPACE_PASSWORD_HASH", "").strip()
    if not password_hash:
        try:
            with connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT setting_value
                          FROM funnel_workspace_settings
                         WHERE setting_key = 'password_hash'
                        """
                    )
                    password_row = cur.fetchone()
                    password_hash = (
                        password_row.get("setting_value")
                        if password_row
                        else ""
                    )
        except Exception:  # noqa: BLE001
            password_hash = ""
    if not looks_like_password_hash(password_hash):
        failures.append("workspace включён без отдельного password hash")

    try:
        state = json.loads((BASE / ".tg_agent_state.json").read_text(encoding="utf-8"))
        connected = any(
            info
            and info.get("enabled") is True
            and info.get("can_reply") is True
            for info in (state.get("business") or {}).values()
        )
    except (OSError, ValueError):
        connected = False
    if not connected:
        failures.append("workspace включён, но Telegram Business не может отвечать")

if failures:
    print("SMOKE FAILED:")
    for item in failures:
        print(" -", item)
    sys.exit(1)
print("SMOKE OK")
