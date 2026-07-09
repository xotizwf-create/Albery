"""Working-hours deadline rules for auto-created tasks (zoom dispatch, owner recommendations).

Owner's rules (2026-07-09): the business day is 9:00-18:00 MSK, Mon-Fri.
- Zoom lead card: deadline 18:00 the same day, but when fewer than 3 hours remain before 18:00
  (or it is already evening / a day off) — next working day 11:00.
- Owner recommendations task: 12:00 of the next working day after the report date.
All thresholds are env-tunable so they can be changed without code edits.
"""
from __future__ import annotations

import os
from datetime import date, datetime, time as dt_time, timedelta

from config import MSK_TZ


def _env_number(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


def workday_end_hour() -> int:
    return int(_env_number("WORKDAY_END_HOUR", 18))


def is_working_day(day: date) -> bool:
    return day.weekday() < 5  # Mon-Fri


def next_working_day(day: date) -> date:
    nxt = day + timedelta(days=1)
    while not is_working_day(nxt):
        nxt += timedelta(days=1)
    return nxt


def msk_now() -> datetime:
    return datetime.now(MSK_TZ)


def zoom_lead_deadline_at(now: datetime | None = None) -> datetime:
    """Deadline for the aggregated zoom lead card, computed from DISPATCH time:
    today 18:00 MSK; if fewer than ZOOM_LEAD_MIN_GAP_HOURS (3) remain before 18:00,
    or 'now' falls on a day off — next working day 11:00."""
    now = (now or msk_now()).astimezone(MSK_TZ)
    min_gap = _env_number("ZOOM_LEAD_MIN_GAP_HOURS", 3)
    end_of_day = now.replace(hour=workday_end_hour(), minute=0, second=0, microsecond=0)
    if is_working_day(now.date()) and now + timedelta(hours=min_gap) <= end_of_day:
        return end_of_day
    morning_hour = int(_env_number("ZOOM_LEAD_NEXT_DAY_HOUR", 11))
    return datetime.combine(next_working_day(now.date()), dt_time(morning_hour, 0), tzinfo=MSK_TZ)


def recommendations_deadline_at(anchor: date) -> datetime:
    """Deadline for the owner recommendations task: 12:00 MSK of the next working day
    after the report date (a Friday report is due Monday 12:00, not Saturday)."""
    hour = int(_env_number("RECOMMENDATIONS_DEADLINE_HOUR", 12))
    return datetime.combine(next_working_day(anchor), dt_time(hour, 0), tzinfo=MSK_TZ)


def format_deadline_msk(deadline_at: datetime) -> str:
    return f"{deadline_at.astimezone(MSK_TZ).strftime('%d.%m.%Y %H:%M')} МСК"
