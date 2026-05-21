from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
LOG_DIR = Path(os.getenv("ALBERY_LOG_DIR", "/var/log/albery"))
LOG_PATH = Path(os.getenv("ALBERY_DAILY_SYNC_LOG", str(LOG_DIR / "daily-sync.log")))


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def log_event(event: dict[str, Any]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"ts": now_iso(), **event}
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    print(json.dumps(payload, ensure_ascii=False, default=str), flush=True)


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on"}


def env_date(name: str, default: date) -> date:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return default


def result_preview(value: Any) -> Any:
    if isinstance(value, dict):
        preview: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"tasks", "chats", "team", "reports", "segments"}:
                if isinstance(item, list):
                    preview[f"{key}_count"] = len(item)
                continue
            if isinstance(item, (str, int, float, bool)) or item is None:
                preview[key] = item
            elif isinstance(item, dict):
                preview[key] = result_preview(item)
        return preview
    return value


def run_step(name: str, func: Callable[[], Any]) -> bool:
    log_event({"level": "info", "step": name, "status": "started"})
    started = datetime.now()
    try:
        result = func()
    except Exception as exc:  # noqa: BLE001
        log_event({
            "level": "error",
            "step": name,
            "status": "failed",
            "duration_seconds": round((datetime.now() - started).total_seconds(), 3),
            "error": str(exc),
            "traceback": traceback.format_exc(),
        })
        return False
    log_event({
        "level": "info",
        "step": name,
        "status": "completed",
        "duration_seconds": round((datetime.now() - started).total_seconds(), 3),
        "result": result_preview(result),
    })
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Run daily Albery external sync jobs.")
    parser.add_argument("--continue-on-error", action="store_true", default=True)
    args = parser.parse_args()

    os.chdir(ROOT)
    load_dotenv(ROOT / ".env")

    import app  # noqa: PLC0415

    webhook_base = os.getenv("BITRIX_WEBHOOK_BASE", "").strip()
    today = app.msk_today()
    bitrix_days = max(1, min(env_int("AUTO_SYNC_BITRIX_LOOKBACK_DAYS", 30), 30))
    chat_days = max(1, min(env_int("AUTO_SYNC_CHAT_LOOKBACK_DAYS", 1), 31))
    zoom_from = env_date("AUTO_SYNC_ZOOM_FROM", date(2026, 1, 1))
    zoom_to = env_date("AUTO_SYNC_ZOOM_TO", today)
    bitrix_from = today - timedelta(days=bitrix_days - 1)
    chat_from = today - timedelta(days=chat_days - 1)
    generate_chat_reports = env_bool("AUTO_SYNC_CHAT_GENERATE_REPORTS", False)

    log_event({
        "level": "info",
        "status": "run_started",
        "bitrix_from": bitrix_from.isoformat(),
        "bitrix_to": today.isoformat(),
        "chat_from": chat_from.isoformat(),
        "chat_to": today.isoformat(),
        "chat_generate_reports": generate_chat_reports,
        "zoom_from": zoom_from.isoformat(),
        "zoom_to": zoom_to.isoformat(),
    })

    steps: list[tuple[str, Callable[[], Any]]] = []
    if webhook_base:
        steps.extend([
            ("bitrix_team", lambda: app.sync_bitrix_team(webhook_base)),
            ("bitrix_task_events", lambda: app.process_bitrix_task_event_queue(limit=100)),
            ("bitrix_tasks", lambda: app.build_period_export(bitrix_from, today, webhook_base)[0].get("meta", {})),
            ("bitrix_chat_messages", lambda: app.sync_all_chat_dialogs_for_period(chat_from, today, webhook_base, generate_reports=generate_chat_reports)),
        ])
    else:
        log_event({"level": "error", "step": "bitrix", "status": "skipped", "error": "BITRIX_WEBHOOK_BASE is not set"})

    steps.extend([
        ("zoom_api_calls", lambda: app.sync_zoom_calls(zoom_from, zoom_to)),
        ("google_drive_company_instructions", app.sync_google_drive_company_documents),
    ])
    if env_bool("AUTO_SYNC_GOOGLE_DRIVE_ZOOM_TRANSCRIPTS", True):
        steps.append(("google_drive_zoom_transcripts", app.sync_google_drive_call_transcripts))

    success = True
    for name, func in steps:
        step_success = run_step(name, func)
        success = success and step_success
        if not step_success and not args.continue_on_error:
            break

    log_event({"level": "info" if success else "error", "status": "run_finished", "success": success})
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
