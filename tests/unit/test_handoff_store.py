from __future__ import annotations

from contextlib import contextmanager

import handoff_store as hs


class _Cursor:
    def __init__(self, rows=None, rowcount=0):
        self.rows = list(rows or [])
        self.rowcount = rowcount
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        rows, self.rows = self.rows, []
        return rows

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class _Conn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _db_for(cursor):
    @contextmanager
    def _db():
        yield _Conn(cursor)

    return _db


def test_open_event_is_atomic_idempotent_and_keeps_original_sla():
    cur = _Cursor(rows=[
        None,
        {
            "id": 17,
            "status": "pending",
            "priority": "high",
            "due_at": "original-deadline",
            "owner_id": "iu-group",
            "owner_name": "Группа «Работа с ИУ»",
        },
        {
            "id": 51,
            "customer_delivery_status": "pending",
            "internal_delivery_status": "pending",
        },
    ])

    row = hs.open_handoff_event(
        _db_for(cur),
        bot="albery-ai-bot",
        dialog_id=123,
        event_key="evt-123",
        reason_code="model_failure",
        deal_id=78,
        source_message_id=9001,
        priority="high",
        owner_id="iu-group",
        owner_name="Группа «Работа с ИУ»",
        meta={"channel": "tg-biz"},
    )

    handoff_sql, handoff_params = cur.executed[1]
    event_sql, event_params = cur.executed[2]
    assert row["handoff_id"] == 17 and row["event_id"] == 51
    assert row["event_created"] is True
    assert "ON CONFLICT (bot, dialog_id)" in handoff_sql
    assert "due_at =" not in handoff_sql.split("DO UPDATE SET", 1)[1]
    assert handoff_params[:5] == (
        "albery-ai-bot",
        "123",
        78,
        "high",
        "model_failure",
    )
    assert "ON CONFLICT (event_key) DO NOTHING" in event_sql
    assert event_params[:4] == (17, "evt-123", 9001, "model_failure")


def test_duplicate_event_returns_existing_delivery_outcomes():
    cur = _Cursor(rows=[
        {
            "handoff_id": 17,
            "event_id": 51,
            "status": "pending",
            "priority": "normal",
            "due_at": "original-deadline",
            "owner_id": "iu-group",
            "owner_name": "Группа «Работа с ИУ»",
            "customer_delivery_status": "sent",
            "internal_delivery_status": "sent",
        },
    ])

    row = hs.open_handoff_event(
        _db_for(cur),
        bot="albery-ai-bot",
        dialog_id=123,
        event_key="evt-duplicate",
        reason_code="knowledge_gap",
    )

    assert row["event_created"] is False
    assert row["customer_delivery_status"] == "sent"
    assert row["internal_delivery_status"] == "sent"
    assert len(cur.executed) == 1
    assert "WHERE e.event_key = %s" in cur.executed[0][0]


def test_delivery_claim_is_a_compare_and_set():
    claimed = _Cursor(rows=[{"id": 51}])
    not_claimed = _Cursor(rows=[None])

    assert hs.claim_delivery(_db_for(claimed), 51, target="customer") is True
    assert hs.claim_delivery(_db_for(not_claimed), 51, target="internal") is False
    assert "status_col" not in claimed.executed[0][0]
    assert "customer_delivery_status = 'pending'" in claimed.executed[0][0]
    assert "customer_delivery_status" in claimed.executed[0][0]
    assert "internal_delivery_status" in not_claimed.executed[0][0]


def test_successful_human_relay_resolves_open_handoff():
    cur = _Cursor(rowcount=1)

    changed = hs.resolve_for_dialog(
        _db_for(cur),
        bot="albery-ai-bot",
        dialog_id=123,
        resolution_code="human_reply_delivered",
    )

    sql, params = cur.executed[0]
    assert changed == 1
    assert "status = 'resolved'" in sql
    assert params == ("human_reply_delivered", "albery-ai-bot", "123")


def test_overdue_query_has_owner_and_no_duplicated_client_text():
    cur = _Cursor(rows=[{
        "id": 5,
        "status": "pending",
        "dialog_id": "123",
        "owner_id": "iu-group",
        "owner_name": "Группа «Работа с ИУ»",
    }])

    rows = hs.overdue_handoffs(_db_for(cur), reminder_interval_seconds=600)

    sql, params = cur.executed[0]
    assert rows[0]["id"] == 5
    assert "status IN ('pending', 'accepted')" in sql
    assert "due_at <= now()" in sql
    assert "last_reminded_at" in sql
    assert "client_text" not in sql
    assert params == (600, 20)


def test_migration_registers_handoff_and_event_tables_without_message_copy():
    root = __import__("pathlib").Path(__file__).resolve().parents[2]
    migration = (root / "database" / "migrations" / "066_ai_handoffs.sql").read_text(
        encoding="utf-8"
    )
    ensure = (root / "scripts" / "ensure_postgres.py").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS ai_handoffs" in migration
    assert "CREATE TABLE IF NOT EXISTS ai_handoff_events" in migration
    assert "uq_ai_handoffs_open_dialog" in migration
    assert "event_key" in migration and "UNIQUE" in migration
    assert "owner_id" in migration and "due_at" in migration
    assert "client_text" not in migration
    assert '"ai_handoffs": "066_ai_handoffs.sql"' in ensure
