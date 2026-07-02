#!/usr/bin/env python3
"""Hourly self-check for the Albery box: scans the last hour for silent degradations and
alerts the Telegram notifications group only when something crossed a threshold.

Signals:
- HTTP 500 on /mcp* endpoints (tools broken while the site looks healthy);
- bot-turn failures in the journal (timeouts, failed/empty hermes runs, queue overflow);
- bitrix_bot_interactions rows with status<>'ok' or latency >= 300s in the last hour;
- available RAM below 150 MB (the box has 2 GB and has been OOM-killed before).

Installed as systemd albery-selfcheck.timer (hourly). Silent when everything is fine.
"""
import logging
import os
import subprocess
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent.parent
load_dotenv(BASE / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


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

if problems:
    text = "🩺 Albery selfcheck — за последний час есть проблемы:\n" + "\n".join(
        f"- {p}" for p in problems
    ) + "\n\nДетали: journalctl -u albery (сервер 186)."
    notify(text)
    logging.warning("selfcheck: %s problem(s) reported", len(problems))
else:
    logging.info("selfcheck: clean")
