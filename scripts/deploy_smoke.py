#!/usr/bin/env python3
"""Post-deploy smoke for Albery — catches wiring breaks the login-check can't see.

Run on the server after EVERY deploy/restart:
    cd /var/www/albery && .venv/bin/python scripts/deploy_smoke.py

Checks:
1. Every workflow name referenced by mcp/context_server.py via app_workflow_function("...")
   actually resolves. (2026-07-02 incident: a move-only refactor step relocated
   bitrix_method_call out of app.py and silently broke task creation for a day.)
2. Every active agent connector is loopback-only, header-authenticated and exposes exactly its
   database/manifest-derived tool set.
3. Shared, path-token, forwarded and public MCP access is closed.
4. The site and standalone funnel workspace routes are wired.
5. When the workspace is enabled, its password, at least one Telegram transport and
   rollout flags are coherent.
6. On production, VPN policy routing is effective and the Hermes Telegram platform is connected.

Exit code 0 = safe to walk away; 1 = do not leave the deploy like this.
"""
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

import yaml

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from dotenv import load_dotenv  # noqa: E402
from shared.db import assert_tables_exist, connect  # noqa: E402

load_dotenv(BASE / ".env")

APP_URL = "http://127.0.0.1:5002"
MCP_APP_URL = os.getenv("MCP_INTERNAL_BASE_URL", "http://127.0.0.1:5004").rstrip("/")
PUBLIC_MCP_BASE = os.getenv("MCP_PUBLIC_PROBE_BASE", "https://mcp.m4s.ru").rstrip("/")
RETIRED_CONNECTORS = {"albery", "albery-faq", "albery-ops", "albery-core", "albery-ops-core"}
RETIRED_PATHS = ("/mcp", "/mcp-faq", "/mcp-ops", "/mcp-core", "/mcp-ops-core", "/sse", "/sse-faq", "/sse-ops")

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


# 2. Every active agent must have one exact private connector.
config_path = Path(os.getenv("HERMES_CONFIG", "/root/.hermes/config.yaml")).expanduser()
active_agents: list[dict] = []
try:
    config_mode = config_path.stat().st_mode & 0o777
    if config_mode != 0o600:
        failures.append(f"Hermes config mode={oct(config_mode)}, ожидалось 0o600")
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    mcp_servers = config.get("mcp_servers") or {}
    stale = sorted(RETIRED_CONNECTORS & set(mcp_servers))
    if stale:
        failures.append(f"Hermes config содержит retired connectors: {', '.join(stale)}")
except Exception as exc:  # noqa: BLE001
    config = {}
    mcp_servers = {}
    failures.append(f"Hermes config не читается ({type(exc).__name__})")

try:
    from agent_center import _agent_by_slug, _agent_self_tool_names, _agent_tool_names

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT slug, mcp_token FROM agents WHERE is_active ORDER BY slug")
            active_agents = [dict(row) for row in cur.fetchall()]
    for row in active_agents:
        slug = str(row["slug"])
        token = str(row.get("mcp_token") or "").strip()
        connector = mcp_servers.get(f"agent-{slug}")
        automation_connector = mcp_servers.get(f"automation-agent-{slug}")
        if not token:
            failures.append(f"agent-{slug}: DB token отсутствует")
            continue
        if not isinstance(connector, dict):
            failures.append(f"agent-{slug}: connector отсутствует в Hermes config")
            continue
        if not isinstance(automation_connector, dict):
            failures.append(f"automation-agent-{slug}: connector отсутствует в Hermes config")
            continue
        connector_url = str(connector.get("url") or "").strip()
        parsed = urllib.parse.urlparse(connector_url)
        headers = connector.get("headers") if isinstance(connector.get("headers"), dict) else {}
        auth = str(headers.get("Authorization") or "")
        automation_url = str(automation_connector.get("url") or "").strip()
        automation_headers = (
            automation_connector.get("headers")
            if isinstance(automation_connector.get("headers"), dict) else {}
        )
        if (
            connector.get("enabled") is not True
            or parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}
            or parsed.path.rstrip("/") != f"/mcp-agent/{slug}"
            or parsed.query
            or parsed.fragment
            or token in connector_url
            or auth != f"Bearer {token}"
        ):
            failures.append(f"agent-{slug}: connector не соответствует private/header contract")
            continue
        if (
            automation_connector.get("enabled") is not True
            or automation_url != connector_url
            or str(automation_headers.get("Authorization") or "") != auth
            or str(automation_headers.get("X-Albery-Automation") or "") != "1"
        ):
            failures.append(f"automation-agent-{slug}: connector не соответствует private contract")
            continue

        agent = _agent_by_slug(slug)
        expected = _agent_tool_names(agent) | _agent_self_tool_names(agent)
        try:
            status, body = post_json(
                connector_url,
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                {"Authorization": auth},
            )
            actual = {tool.get("name") for tool in ((body.get("result") or {}).get("tools") or [])}
            if status != 200 or actual != expected:
                failures.append(
                    f"agent-{slug}: status={status}, tools={len(actual)}, ожидалось={len(expected)}"
                )
            else:
                print(f"agent-{slug}: private header auth OK, tools={len(actual)}")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"agent-{slug}: tools/list не прошёл ({type(exc).__name__})")
except Exception as exc:  # noqa: BLE001
    failures.append(f"active agent connector check: {type(exc).__name__}")


def expect_status(url: str, status: int, *, method: str = "GET", headers: dict | None = None) -> None:
    request = urllib.request.Request(url, method=method, headers=headers or {})
    if method == "POST":
        request.data = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            actual = response.status
    except urllib.error.HTTPError as exc:
        actual = exc.code
    except Exception as exc:  # noqa: BLE001
        failures.append(f"private MCP negative probe failed ({type(exc).__name__})")
        return
    if actual != status:
        failures.append(f"private MCP negative probe: status={actual}, ожидалось={status}")


# 3. Retired/shared paths do not exist even on loopback; Nginx hides all MCP paths publicly.
for path in RETIRED_PATHS:
    expect_status(f"{MCP_APP_URL}{path}", 404)
print(f"retired shared/SSE routes: checked {len(RETIRED_PATHS)}")

if active_agents:
    sample = active_agents[0]
    sample_slug = str(sample["slug"])
    sample_token = str(sample["mcp_token"])
    safe_url = f"{MCP_APP_URL}/mcp-agent/{sample_slug}"
    expect_status(f"{safe_url}/{sample_token}", 404, method="POST")
    expect_status(
        safe_url,
        404,
        method="POST",
        headers={"Authorization": f"Bearer {sample_token}", "X-Real-IP": "203.0.113.10"},
    )
    expect_status(f"{PUBLIC_MCP_BASE}/mcp-agent/{sample_slug}", 404, method="POST")
    expect_status(f"{PUBLIC_MCP_BASE}/mcp", 404, method="POST")
    expect_status(f"{PUBLIC_MCP_BASE}/healthz", 404)
    expect_status(f"{PUBLIC_MCP_BASE}/zoom/events/not-a-secret", 403)
    expect_status(f"{PUBLIC_MCP_BASE}/bitrix/imbot/not-a-secret", 403)
    print("path-token, forwarded and public MCP host access: 404; webhooks reach auth")

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
    vpn_healthcheck = Path("/usr/local/sbin/vpn-healthcheck.sh")
    if vpn_healthcheck.is_file():
        try:
            check = subprocess.run(
                [str(vpn_healthcheck)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=45,
            )
            if check.returncode:
                failures.append("VPN: effective outbound route or provider reachability is unhealthy")
            else:
                print("VPN effective route/provider reachability: OK")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"VPN healthcheck failed ({type(exc).__name__})")

    gateway_state_path = Path("/root/.hermes/gateway_state.json")
    if gateway_state_path.is_file():
        try:
            gateway_state = json.loads(gateway_state_path.read_text(encoding="utf-8"))
            telegram_state = (
                ((gateway_state.get("platforms") or {}).get("telegram") or {}).get("state")
            )
            if telegram_state != "connected":
                # Never include error_message: upstream may embed the bot token in it.
                failures.append(f"Hermes Telegram platform state={telegram_state or 'missing'}")
            else:
                print("Hermes Telegram platform: connected")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"Hermes Telegram state unreadable ({type(exc).__name__})")
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
    with urllib.request.urlopen(
        f"{APP_URL}/{urllib.parse.quote('Калькулятор')}/", timeout=15
    ) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        if resp.status != 200 or "Калькулятор расчёта ИУ" not in body:
            failures.append(
                f"/Калькулятор/: status={resp.status}, calculator marker missing"
            )
        else:
            print("/Калькулятор/: OK (public)")
except Exception as exc:  # noqa: BLE001
    failures.append(f"/Калькулятор/: {type(exc).__name__}")

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
            "iu_manager_wait_alerts",
        ],
        hint="Apply workspace migrations through 079_iu_manager_wait_alerts.sql.",
    )
    print("workspace tables: OK")
except Exception as exc:  # noqa: BLE001
    failures.append(f"workspace tables: {exc}")

# Employee Telegram is a channel adapter for the same logical agent profiles.  When the staged
# rollout flag is on, the durable ledgers must exist, bot identities must be unique/valid and an
# unconfigured access list must remain closed (reported, but not treated as a broken bot).
telegram_agents_enabled = env_flag("TG_CHANNEL_NEUTRAL_ENABLED")
if telegram_agents_enabled:
    try:
        assert_tables_exist(
            ["telegram_agent_updates", "telegram_agent_offsets", "telegram_agent_outbox"],
            hint="Apply migration 084_channel_neutral_telegram_agents.sql before cutover.",
        )
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT telegram_bot_user_id, count(*) AS n FROM agents "
                    "WHERE is_active AND telegram_bot_token IS NOT NULL "
                    "GROUP BY telegram_bot_user_id HAVING count(*) > 1"
                )
                duplicate_ids = list(cur.fetchall())
                cur.execute(
                    "SELECT telegram_bot_token, count(*) AS n FROM agents "
                    "WHERE is_active AND telegram_bot_token IS NOT NULL "
                    "GROUP BY telegram_bot_token HAVING count(*) > 1"
                )
                duplicate_tokens = list(cur.fetchall())
                cur.execute(
                    "SELECT a.slug, a.telegram_bot_token, count(t.id) AS access_count "
                    "FROM agents a LEFT JOIN telegram_bot_access t ON t.bot = a.slug AND t.is_active "
                    "WHERE a.is_active AND a.telegram_bot_token IS NOT NULL "
                    "GROUP BY a.slug, a.telegram_bot_token ORDER BY a.slug"
                )
                telegram_profiles = [dict(row) for row in cur.fetchall()]
        if duplicate_ids or duplicate_tokens:
            failures.append("employee Telegram: one bot identity is bound to multiple agent profiles")
        import tg_multi
        for profile in telegram_profiles:
            slug = str(profile["slug"])
            try:
                identity = tg_multi.describe(str(profile["telegram_bot_token"]))
                if not identity.get("bot_user_id") or not identity.get("username"):
                    failures.append(f"employee Telegram {slug}: getMe identity incomplete")
                elif int(profile["access_count"] or 0) == 0:
                    print(f"employee Telegram {slug}: identity OK, CLOSED (access list empty)")
                else:
                    print(f"employee Telegram {slug}: identity OK, access entries={profile['access_count']}")
            except Exception as exc:  # noqa: BLE001
                failures.append(f"employee Telegram {slug}: getMe failed ({type(exc).__name__})")
        print("employee Telegram durable tables: OK")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"employee Telegram rollout: {exc}")

# 5. Enabling the workspace is a cutover: the legacy sender must be off and at least
# one real transport must be available. The workspace serves both Telegram Business
# and the public IU bot, so requiring Business specifically produces a false alarm.
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

# Открытая линия Битрикса вырезана (владелец, 27.07.2026). Её остатки в окружении —
# признак незавершённой выкатки: их надо убрать, иначе следующий человек решит, что
# канал где-то ещё жив, и будет искать несуществующий выключатель.
for stale in ("OPENLINE_AGENT_ENABLED", "B24_OPENLINE_BOT_ID"):
    if os.getenv(stale) is not None:
        failures.append(
            f"{stale} остался в .env, хотя открытая линия вырезана из системы"
        )

if workspace_enabled:
    if env_flag("TG_BUSINESS_AUTOREPLY"):
        failures.append("workspace включён, но TG_BUSINESS_AUTOREPLY всё ещё включён")

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

    import funnel_telegram_gateway

    if not funnel_telegram_gateway.telegram_connected():
        failures.append("workspace включён, но ни один Telegram-транспорт не готов")
    else:
        print("workspace Telegram transport: OK")

if failures:
    print("SMOKE FAILED:")
    for item in failures:
        print(" -", item)
    sys.exit(1)
print("SMOKE OK")
