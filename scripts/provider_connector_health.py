"""Content-free freshness and recovery checks for Zoom, Google Drive and WB."""
from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from shared.db import connect


def _aware(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _stale(label: str, value: Any, now: datetime, max_age: timedelta) -> str | None:
    timestamp = _aware(value)
    if timestamp is None:
        return f"{label}: нет подтверждённого успешного состояния"
    age = now - timestamp.astimezone(timezone.utc)
    if age > max_age:
        return f"{label}: успешное состояние старше {int(age.total_seconds() // 60)} мин"
    return None


def _wb_token_expiry_problem(now: datetime) -> str | None:
    token = os.getenv("WB_ANALYTICS_TOKEN", "").strip()
    if not token:
        return "WB: токен не настроен"
    try:
        payload = token.split(".")[1]
        payload += "=" * ((4 - len(payload) % 4) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
        expires = datetime.fromtimestamp(float(claims["exp"]), tz=timezone.utc)
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return "WB: не удалось проверить срок токена"
    remaining = expires - now
    if remaining <= timedelta(0):
        return "WB: токен истёк"
    if remaining <= timedelta(days=14):
        return f"WB: токен истекает через {max(0, remaining.days)} дн"
    return None


def inspect_provider_connector_health(now: datetime | None = None) -> list[str]:
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    problems: list[str] = []
    token_problem = _wb_token_expiry_problem(now_utc)
    if token_problem:
        problems.append(token_problem)
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT last_success_at FROM integration_sync_status WHERE sync_key = 'zoom_api_calls'"
            )
            row = cur.fetchone()
            problem = _stale("Zoom sync", row["last_success_at"] if row else None, now_utc, timedelta(hours=3))
            if problem:
                problems.append(problem)

            cur.execute("SELECT max(last_seen_at) AS latest FROM company_drive_sources")
            row = cur.fetchone()
            problem = _stale("Google Drive sync", row["latest"] if row else None, now_utc, timedelta(hours=3))
            if problem:
                problems.append(problem)

            cur.execute(
                """
                SELECT max(finished_at) FILTER (WHERE ok) AS latest_ok,
                       count(*) FILTER (WHERE NOT ok AND started_at > now() - interval '3 hours') AS recent_errors
                FROM wb_sync_log
                """
            )
            row = cur.fetchone() or {}
            problem = _stale("WB sync", row.get("latest_ok"), now_utc, timedelta(hours=3))
            if problem:
                problems.append(problem)
            if int(row.get("recent_errors") or 0):
                problems.append(f"WB sync: ошибок за 3 часа: {int(row['recent_errors'])}")

            cur.execute(
                """
                SELECT count(*) FILTER (WHERE status = 'error' AND attempts >= 5) AS exhausted,
                       count(*) FILTER (
                           WHERE status = 'processing' AND updated_at < now() - interval '35 minutes'
                       ) AS stale_processing
                FROM zoom_recording_events
                """
            )
            row = cur.fetchone() or {}
            if int(row.get("exhausted") or 0):
                problems.append(f"Zoom queue: исчерпали повторы: {int(row['exhausted'])}")
            if int(row.get("stale_processing") or 0):
                problems.append(f"Zoom queue: зависли processing: {int(row['stale_processing'])}")

            cur.execute(
                """
                SELECT count(*) FILTER (WHERE status = 'review') AS review,
                       count(*) FILTER (
                           WHERE status = 'task_sending' AND updated_at < now() - interval '30 minutes'
                       ) AS ambiguous_task,
                       count(*) FILTER (
                           WHERE status = 'cleanup' AND updated_at < now() - interval '3 hours'
                       ) AS stale_cleanup
                FROM novinki_processing_runs
                """
            )
            row = cur.fetchone() or {}
            if int(row.get("review") or 0):
                problems.append(f"Novinki: запусков в ручной проверке: {int(row['review'])}")
            if int(row.get("ambiguous_task") or 0):
                problems.append(f"Novinki: неоднозначных отправок задачи: {int(row['ambiguous_task'])}")
            if int(row.get("stale_cleanup") or 0):
                problems.append(f"Novinki: зависла очистка источников: {int(row['stale_cleanup'])}")
    return problems
