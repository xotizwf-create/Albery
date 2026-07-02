"""App-wide configuration: paths, env, timezone and MSK-day helpers.

Moved verbatim out of app.py (2026-07-02 refactor, step Sh2.2 — move-only).
`.env` is loaded here so every module that imports config sees the same env,
no matter which process (Flask app, MCP server, scripts) imports it first.
"""
from __future__ import annotations

import os

from datetime import date
from datetime import datetime
from datetime import timedelta
from dotenv import load_dotenv
from pathlib import Path
from zoneinfo import ZoneInfo

load_dotenv()


APP_ROOT = Path(__file__).resolve().parent
EXPORT_DIR = APP_ROOT / "exports"
ATTACHMENTS_DIR = APP_ROOT / "attachments_cache"
FRONTEND_DIST = APP_ROOT / "Интерфейс" / "dist"
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
MSK_TZ = ZoneInfo("Europe/Moscow")
LOCAL_TZ = MSK_TZ
def msk_now() -> datetime:
    return datetime.now(MSK_TZ)
def msk_today() -> date:
    return msk_now().date()
def is_future_day(value: date) -> bool:
    return value > msk_today()
def can_finalize_chat_day(value: date) -> bool:
    return value <= msk_today()
def chat_day_finalization_error(value: date) -> str | None:
    if is_future_day(value):
        return "Нельзя формировать результаты за будущую дату."
    return None
def latest_visible_report_date() -> date:
    return msk_today()
def previous_week_bounds_for_report(value: date) -> tuple[date, date]:
    week_start = value - timedelta(days=value.weekday())
    previous_end = week_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=6)
    return previous_start, previous_end
