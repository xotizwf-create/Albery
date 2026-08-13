#!/usr/bin/env python3
"""Self-check for the Albery box: scans for silent degradations and alerts the Bitrix
«Уведомления» group (chat730) the moment something crosses a threshold. Telegram is only a
fallback for when Bitrix itself is unreachable, so an alert is never lost.

Signals:
- HTTP 500 on /mcp* endpoints (tools broken while the site looks healthy);
- bot-turn failures in the journal (timeouts, failed/empty hermes runs, queue overflow);
- bitrix_bot_interactions rows with status<>'ok' or latency >= 300s in the last hour;
- available RAM below 150 MB (the box has 2 GB and has been OOM-killed before);
- батч-синк run_daily_sync: последний run_finished старше 2 часов или упал;
- systemd failed units и заполнение диска / от 85% (>=92% — КРИТИЧНО, без анти-спам-паузы);
- КРИТИЧНО: критичные сервисы не active (albery, albery-tg, albery-web, albery-mcp,
  hermes-gateway, nginx, postgresql);
- КРИТИЧНО: роль web или mcp не отвечает на /healthz либо не видит базу (проверка идёт через
  реальное соединение с БД, а не через «слушает ли порт» — см. разбор простоя 07.08.2026);
- КРИТИЧНО: PostgreSQL не отвечает на SELECT 1;
- КРИТИЧНО: локальная/offsite цепочка резервных копий PostgreSQL устарела, неполна
  или не прошла проверку pg_restore/SHA-256;
- MCP-серверы gateway, которые не подключаются (agent-*);
- дрейф совместимой restart-политики hermes-gateway;
- внешний доступ (раз в час): Bitrix OAuth, Google OAuth (Drive/Sheets), Zoom OAuth;
- hermes-cron джобы с упавшим последним прогоном (дедуп по сигнатуре).

Installed as systemd albery-selfcheck.timer (каждые 5 минут — «мгновенные» алерты).
Silent when everything is fine. Одинаковый набор НЕ-критичных проблем не спамит: повторный
алерт не чаще раза в 6 часов. Критичные (диск>=92%, RAM<100MB, сервис/PG down) — не чаще
раза в 30 минут, пока не устранены.
"""
import hashlib
import json
import logging
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
load_dotenv(BASE / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

STATE_PATH = Path("/var/log/albery/selfcheck_state.json")
SYNC_LOG = Path(os.getenv("ALBERY_DAILY_SYNC_LOG", "/var/log/albery/daily-sync.log"))
HERMES = "/usr/local/bin/hermes" if Path("/usr/local/bin/hermes").exists() else "hermes"
CRITICAL_SERVICES = ["albery", "albery-tg", "albery-web", "albery-mcp",
                     "hermes-gateway", "nginx", "postgresql"]

# Роли, разделённые 07.08.2026, и их порты. Проверяются НЕ на «слушает ли порт», а на
# /healthz — он реально берёт соединение с базой. Именно эта разница стоила простоя Центра
# Агента: службы поднялись, порт отвечал, /login отдавал 200, а пул соединений был сломан
# (gunicorn --preload создавал его до раздвоения процесса), и всё легло на первом же
# обращении к данным. «Процесс жив» и «процесс умеет работать» — разные вещи.
ROLE_ENDPOINTS = {"web": 5003, "mcp": 5004}
HEALTHZ_TIMEOUT_S = 20
EXTERNAL_CHECK_EVERY_S = 3600  # внешние доступы (Google/Zoom/Bitrix) — раз в час, чтобы не ловить лимиты


def sh(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout or ""
    except Exception as exc:  # noqa: BLE001
        logging.error("selfcheck command failed %s: %s", cmd[0], exc)
        return ""


def is_active(service: str) -> bool:
    return sh(["systemctl", "is-active", service]).strip() == "active"


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


def _telegram_fallback(text: str) -> None:
    token = tg_token()
    chat_id = os.getenv("ALBERY_ERROR_REPORT_TG_CHAT", "-5283789593").strip()
    if not token or not chat_id:
        logging.error("selfcheck: telegram fallback not configured")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=20,
        )
    except Exception as exc:  # noqa: BLE001
        logging.error("selfcheck: telegram fallback failed: %s", exc)


# Режим проверки самого монитора: считает всё как обычно, но НЕ шлёт людям и не трогает
# состояние антиспама. Появился 07.08.2026: монитор, который нельзя прогнать не потревожив
# команду, никто и не прогоняет — а молчащий монитор ничем не отличается от сломанного,
# пока не случится авария. Теперь «поймает ли он поломку» проверяется до того, как она нужна.
DRY_RUN = "--dry-run" in sys.argv


def notify(text: str) -> None:
    """Алерт → Bitrix-группа «Уведомления» (chat730). Telegram — резерв на случай, когда сам
    Bitrix недоступен (его мы тоже мониторим), чтобы сообщение об этом не потерялось."""
    if DRY_RUN:
        print("--- ТАК ВЫГЛЯДЕЛ БЫ АЛЕРТ (dry-run, никому не отправлено) ---")
        print(text)
        print("--- конец алерта ---")
        return
    bitrix_ok = False
    try:
        sys.path.insert(0, str(BASE / "scripts"))
        import b24_chat_notify
        bitrix_ok, err = b24_chat_notify.notify(text)
        if not bitrix_ok:
            logging.error("selfcheck: Bitrix delivery failed: %s", err)
    except Exception as exc:  # noqa: BLE001
        logging.error("selfcheck: Bitrix delivery crashed: %s", exc)
    if not bitrix_ok:
        _telegram_fallback(text)


# --- Пробы внешних доступов (лёгкие, без импорта app) ------------------------------------
def check_bitrix() -> str | None:
    try:
        sys.path.insert(0, str(BASE / "scripts"))
        import b24_chat_notify
        endpoint, token = b24_chat_notify._access_token()
        if endpoint and token:
            return None
        return "доступ к Bitrix: OAuth-токен не получен (imbot/REST недоступен)"
    except Exception as exc:  # noqa: BLE001
        return f"доступ к Bitrix: ошибка ({str(exc)[:90]})"


def check_google() -> str | None:
    last = "файл OAuth-токена не найден"
    for path in ("/root/.hermes/secure/google_oauth_token.json", "/root/.hermes/google_token.json"):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except OSError:
            continue
        try:
            resp = requests.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": data.get("client_id", ""),
                    "client_secret": data.get("client_secret", ""),
                    "refresh_token": data.get("refresh_token", ""),
                },
                timeout=25,
            )
            if resp.ok and resp.json().get("access_token"):
                return None
            return f"доступ к Google: refresh не удался (HTTP {resp.status_code} {resp.text[:80]})"
        except Exception as exc:  # noqa: BLE001
            return f"доступ к Google: ошибка ({str(exc)[:90]})"
    return f"доступ к Google: {last}"


def check_zoom() -> str | None:
    account_id = os.getenv("ZOOM_ACC2_ACCOUNT_ID", "").strip()
    client_id = os.getenv("ZOOM_ACC2_CLIENT_ID", "").strip()
    client_secret = os.getenv("ZOOM_ACC2_CLIENT_SECRET", "").strip()
    if not (account_id and client_id and client_secret):
        return "доступ к Zoom: не заданы ZOOM_ACC2_* в .env"
    try:
        resp = requests.post(
            "https://zoom.us/oauth/token",
            params={"grant_type": "account_credentials", "account_id": account_id},
            auth=(client_id, client_secret),
            timeout=25,
        )
        if resp.ok and resp.json().get("access_token"):
            return None
        return f"доступ к Zoom: OAuth {resp.status_code} {resp.text[:80]}"
    except Exception as exc:  # noqa: BLE001
        return f"доступ к Zoom: ошибка ({str(exc)[:90]})"


try:
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
except Exception:  # noqa: BLE001
    state = {}

problems: list[str] = []
critical = False  # диск>=92%, RAM<100MB, сервис/PG down — эскалируем мимо 6ч-паузы

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

# --- Критичные сервисы живы --------------------------------------------------------------
inactive = [s for s in CRITICAL_SERVICES if not is_active(s)]
if inactive:
    problems.append(f"КРИТИЧНО: сервисы не работают: {', '.join(inactive)}")
    critical = True

# --- Роли реально доходят до базы --------------------------------------------------------
# Проверяем не «слушает ли порт», а /healthz: он берёт соединение из пула и выполняет запрос.
# Служба со сломанным пулом слушает порт и отдаёт 200 на страницах, не требующих данных —
# снаружи выглядит здоровой, а любая работа с данными падает через 30 секунд (07.08.2026).
for role_name, role_port in ROLE_ENDPOINTS.items():
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{role_port}/healthz", timeout=HEALTHZ_TIMEOUT_S
        ) as response:
            health = json.loads(response.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        problems.append(f"КРИТИЧНО: роль {role_name} не отвечает на /healthz ({type(exc).__name__})")
        critical = True
        continue
    if health.get("database") != "ok":
        problems.append(f"КРИТИЧНО: роль {role_name} не видит базу (/healthz: {health.get('database')})")
        critical = True
    elif health.get("role") != role_name:
        # Неверная роль = второй комплект фоновых расписаний = двойные уведомления людям.
        problems.append(
            f"роль на порту {role_port} объявлена как {health.get('role')!r}, ожидалась {role_name!r}"
        )

# --- PostgreSQL принимает подключения ----------------------------------------------------
pg_answer = sh(["sudo", "-u", "postgres", "psql", "albery", "-tAc", "SELECT 1"]).strip()
if pg_answer != "1":
    problems.append("КРИТИЧНО: PostgreSQL не отвечает на SELECT 1")
    critical = True
else:
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

# --- PostgreSQL backup chain: local archive -> SHA-256 -> verified offsite copy ---------
# This check is deliberately fast: the daily backup jobs hash every byte and validate the
# archives. The five-minute self-check verifies freshness, modes and their signed status,
# avoiding a 256+ MB disk read on every monitoring tick.
try:
    from scripts.postgres_backup_health import inspect_backup_health

    backup_problems = inspect_backup_health()
except Exception as exc:  # noqa: BLE001
    backup_problems = [f"PostgreSQL backup monitor failed ({type(exc).__name__})"]
if backup_problems:
    problems.extend(f"КРИТИЧНО: {message}" for message in backup_problems)
    critical = True

# --- Durable Telegram/IU queues -----------------------------------------------------------
# This probe reads counters and timestamps only; client text and attachment payloads never
# enter monitoring state or alert messages.
try:
    from scripts.workspace_queue_health import inspect_workspace_queue_health

    workspace_queue_problems = inspect_workspace_queue_health()
except Exception as exc:  # noqa: BLE001
    workspace_queue_problems = [
        f"Telegram/IU queue monitor failed ({type(exc).__name__})"
    ]
if workspace_queue_problems:
    problems.extend(f"КРИТИЧНО: {message}" for message in workspace_queue_problems)
    critical = True

# --- Оперативная память ------------------------------------------------------------------
# --- Versioned MCP capability caps ---------------------------------------------------------
# Missing/malformed manifests remove power, but that safe failure is still an outage for an
# operational profile and therefore needs an alert rather than silent zero-tool answers.
try:
    from agent_knowledge import AGENTS_DIR, load_manifest
    from mcp.tool_policy import REVIEWED_TOOL_NAMES, ZERO_TOOL_AGENT_SLUGS

    active_slugs = [
        value.strip()
        for value in sh([
            "sudo", "-u", "postgres", "psql", "albery", "-tAc",
            "SELECT slug FROM agents WHERE is_active ORDER BY slug",
        ]).splitlines()
        if value.strip()
    ]
    for slug in active_slugs:
        path = AGENTS_DIR / f"{slug}.yaml"
        source = path.read_text(encoding="utf-8") if path.is_file() else ""
        if not source or "\ntools:" not in "\n" + source:
            problems.append(f"КРИТИЧНО: у активного агента {slug} нет versioned MCP cap")
            critical = True
            continue
        cap = set(load_manifest(slug).get("tools") or [])
        if cap - set(REVIEWED_TOOL_NAMES):
            problems.append(f"КРИТИЧНО: MCP cap агента {slug} содержит непроверенные инструменты")
            critical = True
        elif not cap and slug not in ZERO_TOOL_AGENT_SLUGS:
            problems.append(f"КРИТИЧНО: рабочий агент {slug} остался без MCP-инструментов")
            critical = True
except Exception as exc:  # noqa: BLE001
    problems.append(f"КРИТИЧНО: не удалось проверить versioned MCP caps ({type(exc).__name__})")
    critical = True

for line in sh(["free", "-m"]).splitlines():
    if line.startswith("Mem:"):
        available_mb = int(line.split()[-1])
        if available_mb < 100:
            problems.append(f"КРИТИЧНО: почти нет памяти: {available_mb} MB available")
            critical = True
        elif available_mb < 150:
            problems.append(f"мало свободной памяти: {available_mb} MB available")

# --- MCP-серверы gateway, которые не подключаются ---------------------------------------
gw_journal = sh(["journalctl", "-u", "hermes-gateway", "--since", "-15min", "--no-pager"])
mcp_down: set[str] = set()
gw_lines = gw_journal.splitlines()
scheduler_marker = "Albery scheduler-only gateway: MCP discovery skipped"
marker_indexes = [index for index, line in enumerate(gw_lines) if scheduler_marker in line]
if marker_indexes:
    # After Telegram retirement this process is a no-agent scheduler. Any later connector attempt
    # means the scheduler-only guard failed; old pre-restart warnings must not generate alerts.
    post_marker = gw_lines[marker_indexes[-1] + 1:]
    if any("MCP server '" in line for line in post_marker):
        problems.append("Hermes scheduler-only gateway unexpectedly opened MCP connectors")
    gw_lines = []
for line in gw_lines:
    if "still down after" in line or "failed initial connection after" in line:
        marker = "MCP server '"
        idx = line.find(marker)
        if idx != -1:
            name = line[idx + len(marker):].split("'", 1)[0]
            if name:
                mcp_down.add(name)
if mcp_down:
    problems.append(f"MCP-серверы не подключаются: {', '.join(sorted(mcp_down))}")

# --- Совместимая restart-политика Hermes -------------------------------------------------
restart_policy = sh([
    "systemctl", "show", "hermes-gateway",
    "-p", "RestartUSec",
    "-p", "StartLimitIntervalUSec",
    "-p", "StartLimitBurst",
])
restart_values = dict(
    line.split("=", 1) for line in restart_policy.splitlines() if "=" in line
)
if (
    restart_values.get("RestartUSec") != "30s"
    or restart_values.get("StartLimitIntervalUSec") != "5min"
    or restart_values.get("StartLimitBurst") != "5"
):
    problems.append("КРИТИЧНО: restart-политика hermes-gateway отсутствует или изменилась")
    critical = True
unit_text = sh(["systemctl", "cat", "hermes-gateway.service"])
if "RestartMaxDelaySec=" in unit_text or "RestartSteps=" in unit_text:
    problems.append("КРИТИЧНО: hermes-gateway снова содержит несовместимые systemd-директивы")
    critical = True

# --- Свежесть батч-синка (run_daily_sync, /etc/cron.d/albery-daily-sync) ----------------
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
        if use_pct >= 92:
            problems.append(f"КРИТИЧНО: диск / заполнен на {use_pct}% — сервис под угрозой")
            critical = True
        elif use_pct >= 85:
            problems.append(f"диск / заполнен на {use_pct}%")
    except (IndexError, ValueError):
        pass

# --- Внешний доступ: Bitrix / Google / Zoom (раз в час) ---------------------------------
last_ext = float(state.get("last_external_ts", 0) or 0)
if time.time() - last_ext > EXTERNAL_CHECK_EVERY_S - 60:
    for probe in (check_bitrix, check_google, check_zoom):
        try:
            msg = probe()
        except Exception as exc:  # noqa: BLE001
            msg = f"{probe.__name__}: непредвиденная ошибка ({str(exc)[:80]})"
        if msg:
            problems.append(msg)
    state["last_external_ts"] = time.time()

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

# --- Отправка с антиспамом ---------------------------------------------------------------
# Не-критичные: тот же набор — не чаще раза в 6 часов. Критичные (диск/RAM/сервис/PG) —
# не чаще раза в 30 минут, пока проблема висит: их нельзя «замолчать» на полдня.
digest = hashlib.sha256("\n".join(problems).encode("utf-8")).hexdigest() if problems else ""
last_digest = str(state.get("last_digest", ""))
last_alert_ts = float(state.get("last_alert_ts", 0) or 0)
repeat_pause = 30 * 60 if critical else 6 * 3600
should_notify = bool(problems) and (
    digest != last_digest or time.time() - last_alert_ts > repeat_pause)

if problems:
    if should_notify:
        head = "🚨 Albery — КРИТИЧНО:" if critical else "🩺 Albery selfcheck — есть проблемы:"
        text = head + "\n" + "\n".join(f"- {p}" for p in problems) + \
            "\n\nДетали: journalctl -u albery (сервер 186)."
        notify(text)
        if not DRY_RUN:
            # В dry-run состояние антиспама не трогаем: проверка монитора не должна
            # заглушить настоящий алерт, который придёт через минуту.
            state["last_digest"] = digest
            state["last_alert_ts"] = time.time()
        logging.warning("selfcheck: %s problem(s) reported (critical=%s)", len(problems), critical)
    else:
        logging.warning("selfcheck: %s problem(s), alert suppressed (unchanged)", len(problems))
else:
    logging.info("selfcheck: clean")
    state["last_digest"] = ""

try:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
except OSError as exc:
    logging.error("selfcheck: state save failed: %s", exc)
