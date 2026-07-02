"""Generic parsing/formatting helpers shared by every domain: payload navigation
(nested_get/pick/extract_collection/flatten_request_payload), datetime parsing
and MSK formatting, person-name normalization, task status labels, period math.

Moved verbatim out of app.py (2026-07-02 refactor, step Sh2.3 — move-only).
Depends only on config (MSK_TZ).
"""
from __future__ import annotations

from config import LOCAL_TZ
from config import MSK_TZ
from datetime import date
from datetime import datetime
from datetime import time as dt_time
from flask import request
from typing import Any



def flatten_request_payload() -> dict[str, Any]:
    json_payload = request.get_json(silent=True)
    if isinstance(json_payload, dict):
        return json_payload
    payload: dict[str, Any] = {}
    for key in request.form:
        values = request.form.getlist(key)
        payload[key] = values if len(values) > 1 else (values[0] if values else "")
    for key in request.args:
        if key not in payload:
            values = request.args.getlist(key)
            payload[key] = values if len(values) > 1 else (values[0] if values else "")
    return payload
def split_bitrix_user_name(raw: dict[str, Any], fallback_name: str | None) -> tuple[str | None, str | None, str | None]:
    first_name = first_non_empty(raw.get("NAME"), raw.get("name"))
    last_name = first_non_empty(raw.get("LAST_NAME"), raw.get("lastName"), raw.get("last_name"))
    second_name = first_non_empty(raw.get("SECOND_NAME"), raw.get("secondName"), raw.get("second_name"))
    if first_name or last_name or second_name:
        return first_name, last_name, second_name
    parts = str(fallback_name or "").split()
    return (
        parts[0] if len(parts) > 0 else None,
        parts[1] if len(parts) > 1 else None,
        " ".join(parts[2:]) if len(parts) > 2 else None,
    )
def iso_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)
STATUS_LABELS = {
    1: "Новая",
    2: "Ждет выполнения",
    3: "В работе",
    4: "Ждет контроля",
    5: "Завершена",
    6: "Отложена",
    7: "Отклонена",
}
STATUS_LABELS_V3 = {
    "pending": "Ждет выполнения",
    "inProgress": "В работе",
    "in_progress": "В работе",
    "supposedlyCompleted": "Ждет контроля",
    "supposedly_completed": "Ждет контроля",
    "completed": "Завершена",
    "deferred": "Отложена",
    "declined": "Отклонена",
    "new": "Новая",
}
def first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None
def nested_get(data: dict[str, Any], dotted_key: str) -> Any:
    current: Any = data
    for key in dotted_key.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(key)
        if current is None:
            return None
    return current
def pick(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
        if "." in key:
            value = nested_get(data, key)
            if value not in (None, ""):
                return value
    return None
def to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
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
def is_rate_limit_error(exc: Exception) -> bool:
    message = str(exc).lower()
    markers = (
        "query_limit_exceeded",
        "too many requests",
        "service temporarily unavailable",
        "429",
        "503",
    )
    return any(marker in message for marker in markers)
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
def normalize_status(status_code: Any) -> dict[str, Any]:
    if isinstance(status_code, str):
        cleaned = status_code.strip()
        if cleaned:
            return {
                "code": cleaned,
                "label": STATUS_LABELS_V3.get(cleaned, cleaned),
            }
    code = to_int(status_code)
    return {
        "code": code if code is not None else status_code,
        "label": STATUS_LABELS.get(code, "Неизвестно"),
    }
def is_fallback_person_name(name: Any) -> bool:
    if not name:
        return True
    text = str(name).strip().lower()
    return text.startswith("user ") or text.startswith("пользователь ")
def format_person_name(profile: dict[str, Any], fallback_name: Any = None) -> str | None:
    first = first_non_empty(pick(profile, "NAME", "name"))
    last = first_non_empty(pick(profile, "LAST_NAME", "lastName"))
    second = first_non_empty(pick(profile, "SECOND_NAME", "secondName"))
    full_from_profile = " ".join(str(x).strip() for x in (first, second, last) if x).strip()
    if full_from_profile:
        return full_from_profile

    fallback_text = str(fallback_name).strip() if fallback_name is not None else ""
    if fallback_text and not is_fallback_person_name(fallback_text):
        return fallback_text
    return None
def extract_collection(data: dict[str, Any], *keys: str) -> list[Any]:
    result = data.get("result", data)
    if isinstance(result, list):
        return result
    if not isinstance(result, dict):
        return []
    for key in keys:
        value = result.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            dict_values = list(value.values())
            if all(isinstance(item, dict) for item in dict_values):
                return dict_values
    return []


RU_MONTH_NAMES = {
    1: ("Январь", "января"),
    2: ("Февраль", "февраля"),
    3: ("Март", "марта"),
    4: ("Апрель", "апреля"),
    5: ("Май", "мая"),
    6: ("Июнь", "июня"),
    7: ("Июль", "июля"),
    8: ("Август", "августа"),
    9: ("Сентябрь", "сентября"),
    10: ("Октябрь", "октября"),
    11: ("Ноябрь", "ноября"),
    12: ("Декабрь", "декабря"),
}
def sentence_case_ru(value: Any) -> str:
    text = str(value or "").strip().rstrip(".")
    return text[:1].upper() + text[1:] if text else text
def first_text_value(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""
def safe_parse_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None
