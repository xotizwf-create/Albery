"""Shared pytest fixtures.

Most tests run with no PostgreSQL and no external credentials:
  - pure-logic tests call functions directly;
  - MCP contract/consistency tests inspect the in-process tool registry and the
    `handle_request` dispatch core (DB-free for initialize/tools/list);
  - integration tests mock `requests`/HTTP and the `pg_connect()` layer via the
    `fake_pg` factory below.

Tests marked `db` require a real PostgreSQL and are skipped unless DATABASE_URL
is set (CI provides a service container).
"""
from __future__ import annotations

import os
import contextlib
from typing import Any, Callable

import pytest

# Stable env defaults so importing app.py / context_server is deterministic and
# never blocks on missing secrets. Set before the modules are imported.
os.environ.setdefault("FLASK_SECRET_KEY", "test-secret-key")
os.environ.setdefault("MCP_SHARED_SECRET", "test-mcp-secret")
os.environ.setdefault("MCP_FAQ_SHARED_SECRET", "test-faq-secret")


@pytest.fixture(scope="session")
def ctx():
    """The MCP context server module (full tool registry + dispatch)."""
    import mcp.context_server as context_server

    return context_server


@pytest.fixture(scope="session")
def app_module():
    """The Flask backend module."""
    import app as app_module

    return app_module


@pytest.fixture(scope="session")
def bitrix_module():
    """The Bitrix integration module (extracted from app.py 2026-07-02).

    Sync orchestration lives here now, and it holds its own bindings of
    BitrixClient/pg_connect — patch THIS module, not app, to affect it."""
    import bitrix as bitrix_module

    return bitrix_module


@pytest.fixture()
def client(app_module):
    """Flask test client."""
    app_module.app.config.update(TESTING=True)
    return app_module.app.test_client()


# --------------------------------------------------------------------------- #
# Fake PostgreSQL layer for integration tests (no real DB).
# --------------------------------------------------------------------------- #
class FakeCursor:
    """Records executed SQL and returns programmable fetch results.

    `responder(sql, params) -> Any` lets a test decide what fetchone/fetchall
    return for a given query (matched however the test likes, e.g. by substring).
    Default: fetchone -> {"id": 1}, fetchall -> [].
    """

    def __init__(self, responder: Callable[[str, Any], Any] | None = None):
        self.executed: list[tuple[str, Any]] = []
        self._responder = responder
        self._last: Any = None
        self.rowcount = 0

    def execute(self, sql: str, params: Any = None):
        self.executed.append((sql, params))
        self._last = self._responder(sql, params) if self._responder else None

    def executemany(self, sql: str, seq: Any = None):
        self.executed.append((sql, seq))

    def fetchone(self):
        if self._last is None:
            return {"id": 1}
        if isinstance(self._last, list):
            return self._last[0] if self._last else None
        return self._last

    def fetchall(self):
        if self._last is None:
            return []
        return self._last if isinstance(self._last, list) else [self._last]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConn:
    def __init__(self, cursor: FakeCursor):
        self._cursor = cursor

    def cursor(self, *args, **kwargs):
        return self._cursor

    @contextlib.contextmanager
    def transaction(self):
        yield self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def commit(self):
        pass

    def close(self):
        pass


@pytest.fixture()
def fake_pg(monkeypatch):
    """Patch `pg_connect` on a module so DB-touching code runs without a DB.

    Usage:
        cur = fake_pg(app_module, responder=lambda sql, p: {"id": 7})
        app_module.some_sync(...)
        assert any("INSERT INTO bitrix_tasks" in sql for sql, _ in cur.executed)
    """

    def _install(module, responder: Callable[[str, Any], Any] | None = None) -> FakeCursor:
        cursor = FakeCursor(responder)
        conn = FakeConn(cursor)
        monkeypatch.setattr(module, "pg_connect", lambda *a, **k: conn, raising=False)
        return cursor

    return _install


def pytest_collection_modifyitems(config, items):
    """Skip `db`-marked tests unless a PostgreSQL DATABASE_URL is configured."""
    if os.getenv("DATABASE_URL"):
        return
    skip_db = pytest.mark.skip(reason="no DATABASE_URL; PostgreSQL tests run in CI")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip_db)
