"""Pure datetime and period helpers used by the Flask backend.

The functions in this module intentionally avoid Flask, database, and network
state so they can be tested and reused without importing the large app module.
"""
from __future__ import annotations

from datetime import date, datetime, time as dt_time
from typing import Any
from zoneinfo import ZoneInfo

MSK_TZ = ZoneInfo("Europe/Moscow")
LOCAL_TZ = MSK_TZ


def parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())

    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    candidates = [text]
    if " " in text and "T" not in text:
        candidates.append(text.replace(" ", "T"))

    for candidate in candidates:
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue

    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%Y %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def make_aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=LOCAL_TZ)
    return dt


def format_datetime_ru(value: Any) -> str:
    if value in (None, ""):
        return "-"
    parsed = parse_datetime(value)
    if parsed is None:
        return str(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=MSK_TZ)
    else:
        parsed = parsed.astimezone(MSK_TZ)
    return parsed.strftime("%d.%m.%Y %H:%M")


def format_datetime_msk_label(value: Any) -> str:
    if value in (None, ""):
        return ""
    parsed = parse_datetime(value)
    if parsed is None:
        return str(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=MSK_TZ)
    else:
        parsed = parsed.astimezone(MSK_TZ)
    return parsed.strftime("%d.%m.%Y в %H:%M МСК")


def format_date_ru(value: Any) -> str:
    if value in (None, ""):
        return "-"
    try:
        if isinstance(value, date):
            parsed = value
        else:
            parsed = date.fromisoformat(str(value)[:10])
    except ValueError:
        return str(value)
    return parsed.strftime("%d.%m.%Y")


def period_bounds(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    start = datetime.combine(date_from, dt_time.min)
    end = datetime.combine(date_to, dt_time.max)
    start_aw = make_aware(start) or start
    end_aw = make_aware(end) or end
    return start_aw, end_aw


def is_dt_in_period(value: datetime | None, start: datetime, end: datetime) -> bool:
    if value is None:
        return False
    return start <= value <= end


def _first_present(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return None


def base_task_created_in_period(base_task: dict[str, Any], start: datetime, end: datetime) -> bool:
    created = make_aware(parse_datetime(_first_present(base_task, "created", "createdDate", "CREATED_DATE")))
    return is_dt_in_period(created, start, end)
