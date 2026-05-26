"""Database liveness: connectivity, MCP health, schema presence, CRUD round-trip.

Marked `db`: runs only when DATABASE_URL is set (CI provides a PostgreSQL
service with the schema applied via scripts/ensure_postgres.py); skipped locally.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.db

CORE_TABLES = {
    "bitrix_tasks",
    "chats",
    "chat_messages",
    "zoom_calls",
    "owner_daily_reports",
    "ai_instruction_folders",
    "company_profile",
}


def test_pg_connect_responds(app_module):
    with app_module.pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 AS one")
            assert cur.fetchone()["one"] == 1


def test_mcp_health_reports_ok(ctx):
    result = ctx.tool_health({})
    assert result["status"] == "ok"
    assert result.get("database")


def test_core_tables_exist(app_module):
    with app_module.pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            )
            present = {row["table_name"] for row in cur.fetchall()}
    missing = CORE_TABLES - present
    assert not missing, f"schema missing core tables: {sorted(missing)}"


def test_temp_table_crud_roundtrip(app_module):
    # TEMP tables are session-local, so this is safe even against a real database.
    with app_module.pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TEMP TABLE _crud_check (id int PRIMARY KEY, name text)")
            cur.execute("INSERT INTO _crud_check VALUES (1, 'a'), (2, 'b')")
            cur.execute("SELECT count(*) AS c FROM _crud_check")
            assert cur.fetchone()["c"] == 2
            cur.execute("UPDATE _crud_check SET name = 'z' WHERE id = 1")
            cur.execute("SELECT name FROM _crud_check WHERE id = 1")
            assert cur.fetchone()["name"] == "z"
            cur.execute("DELETE FROM _crud_check WHERE id = 1")
            cur.execute("SELECT count(*) AS c FROM _crud_check")
            assert cur.fetchone()["c"] == 1
