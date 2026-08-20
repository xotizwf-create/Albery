from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

from scripts.workspace_queue_health import inspect_workspace_queue_health


NOW = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)


def _connect_with_row(row):
    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, params):
            assert "funnel_workspace_updates" in sql
            assert len(params) == 12

        def fetchone(self):
            return row

    class Connection:
        def cursor(self):
            return Cursor()

    @contextmanager
    def connect():
        yield Connection()

    return connect


def test_healthy_workspace_queues_are_silent():
    assert inspect_workspace_queue_health(
        connect_factory=_connect_with_row({}),
        now=NOW,
    ) == []


def test_queue_probe_reports_counts_without_business_payloads():
    problems = inspect_workspace_queue_health(
        connect_factory=_connect_with_row(
            {"outbox_unknown": 2, "crm_overdue": 1, "manager_expired": 3}
        ),
        now=NOW,
    )

    assert problems == [
        "Telegram deliveries have ambiguous outcome: 2",
        "Telegram CRM actions overdue: 1",
        "IU manager alert leases expired: 3",
    ]
