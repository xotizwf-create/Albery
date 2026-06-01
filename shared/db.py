from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

try:
    from psycopg_pool import ConnectionPool
except ImportError:  # pragma: no cover - fallback for environments before deps are refreshed.
    ConnectionPool = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
_pool: Any | None = None


def normalize_postgres_url(database_url: str) -> str:
    normalized = database_url.strip()
    for prefix in ("postgresql+psycopg2://", "postgresql+psycopg://"):
        if normalized.startswith(prefix):
            return "postgresql://" + normalized[len(prefix):]
    return normalized


def load_env_value(key: str) -> str:
    value = os.getenv(key, "").strip()
    if value:
        return value
    if ENV_PATH.exists():
        for raw_line in ENV_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            raw_key, raw_value = line.split("=", 1)
            if raw_key.strip() == key:
                return raw_value.strip().strip('"').strip("'")
    return ""


def database_url() -> str:
    value = load_env_value("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL is not set. The project is PostgreSQL-only.")
    return normalize_postgres_url(value)


def _pool_enabled() -> bool:
    return os.getenv("POSTGRES_POOL_ENABLED", "1").strip().lower() not in {"0", "false", "no"}


def _session_options() -> str:
    """libpq `options` string applied to every connection.

    Without these, a statement that hits a row/table lock waits forever; a single
    stuck call then holds its pool connection until the MCP client times out (120s)
    and repeated retries exhaust the pool, taking the whole MCP server down. These
    timeouts make any blocked/abandoned query fail fast instead of poisoning the pool.
    All values are in milliseconds; 0 disables a given timeout and is overridable via env.
    """
    statement_ms = os.getenv("POSTGRES_STATEMENT_TIMEOUT_MS", "120000").strip() or "0"
    lock_ms = os.getenv("POSTGRES_LOCK_TIMEOUT_MS", "15000").strip() or "0"
    idle_tx_ms = os.getenv("POSTGRES_IDLE_IN_TX_TIMEOUT_MS", "120000").strip() or "0"
    parts = [
        f"-c statement_timeout={statement_ms}",
        f"-c lock_timeout={lock_ms}",
        f"-c idle_in_transaction_session_timeout={idle_tx_ms}",
    ]
    return " ".join(parts)


def get_pool() -> Any | None:
    global _pool
    if ConnectionPool is None or not _pool_enabled():
        return None
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=database_url(),
            min_size=int(os.getenv("POSTGRES_POOL_MIN_SIZE", "1") or "1"),
            max_size=int(os.getenv("POSTGRES_POOL_MAX_SIZE", "10") or "10"),
            kwargs={"row_factory": dict_row, "options": _session_options()},
            open=False,
        )
        _pool.open()
    return _pool


@contextmanager
def connect() -> Iterator[psycopg.Connection]:
    pool = get_pool()
    if pool is not None:
        with pool.connection() as conn:
            yield conn
        return
    with psycopg.connect(database_url(), row_factory=dict_row, options=_session_options()) as conn:
        yield conn


def assert_tables_exist(table_names: tuple[str, ...] | list[str], hint: str = "Apply database migrations before starting.") -> None:
    missing: list[str] = []
    with connect() as conn:
        with conn.cursor() as cur:
            for table_name in table_names:
                cur.execute("SELECT to_regclass(%s) IS NOT NULL AS exists", (f"public.{table_name}",))
                row = cur.fetchone()
                if not row or not row["exists"]:
                    missing.append(table_name)
    if missing:
        raise RuntimeError(f"Missing PostgreSQL tables: {', '.join(missing)}. {hint}")
