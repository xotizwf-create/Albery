from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

from scripts import provider_connector_health as health


NOW = datetime(2026, 8, 14, 13, 0, tzinfo=timezone.utc)


def _token(expires: datetime) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": int(expires.timestamp())}).encode("utf-8")
    ).decode("ascii").rstrip("=")
    return f"header.{payload}.signature"


class Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.current = None

    def execute(self, sql):
        if "integration_sync_status" in sql:
            self.current = self.rows["zoom"]
        elif "company_drive_sources" in sql:
            self.current = self.rows["drive"]
        elif "wb_sync_log" in sql:
            self.current = self.rows["wb"]
        elif "zoom_recording_events" in sql:
            self.current = self.rows["queue"]
        elif "novinki_processing_runs" in sql:
            self.current = self.rows["novinki"]
        else:
            raise AssertionError(sql)

    def fetchone(self):
        return self.current

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class Connection:
    def __init__(self, rows):
        self.cursor_instance = Cursor(rows)

    def cursor(self):
        return self.cursor_instance

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_provider_health_is_clean_for_fresh_durable_state(monkeypatch):
    fresh = NOW - timedelta(minutes=30)
    rows = {
        "zoom": {"last_success_at": fresh},
        "drive": {"latest": fresh},
        "wb": {"latest_ok": fresh, "recent_errors": 0},
        "queue": {"exhausted": 0, "stale_processing": 0},
        "novinki": {"review": 0, "ambiguous_task": 0, "stale_cleanup": 0},
    }
    monkeypatch.setenv("WB_ANALYTICS_TOKEN", _token(NOW + timedelta(days=100)))
    monkeypatch.setattr(health, "connect", lambda: Connection(rows))

    assert health.inspect_provider_connector_health(NOW) == []


def test_provider_health_reports_expiry_staleness_and_stuck_queue(monkeypatch):
    stale = NOW - timedelta(hours=4)
    rows = {
        "zoom": {"last_success_at": stale},
        "drive": {"latest": stale},
        "wb": {"latest_ok": stale, "recent_errors": 2},
        "queue": {"exhausted": 1, "stale_processing": 3},
        "novinki": {"review": 1, "ambiguous_task": 2, "stale_cleanup": 1},
    }
    monkeypatch.setenv("WB_ANALYTICS_TOKEN", _token(NOW + timedelta(days=5)))
    monkeypatch.setattr(health, "connect", lambda: Connection(rows))

    problems = health.inspect_provider_connector_health(NOW)

    assert any("истекает" in problem for problem in problems)
    assert any("Zoom sync" in problem for problem in problems)
    assert any("Google Drive sync" in problem for problem in problems)
    assert any("WB sync" in problem for problem in problems)
    assert any("исчерпали" in problem for problem in problems)
    assert any("зависли" in problem for problem in problems)
    assert any("Novinki" in problem for problem in problems)
