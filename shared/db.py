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


def get_pool() -> Any | None:
    global _pool
    if ConnectionPool is None or not _pool_enabled():
        return None
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=database_url(),
            min_size=int(os.getenv("POSTGRES_POOL_MIN_SIZE", "1") or "1"),
            max_size=int(os.getenv("POSTGRES_POOL_MAX_SIZE", "10") or "10"),
            kwargs={"row_factory": dict_row},
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
    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
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
