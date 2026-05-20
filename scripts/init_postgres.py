from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
from dotenv import load_dotenv
from psycopg import sql


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "database" / "postgres_schema_v1.sql"


def normalize_postgres_url(database_url: str) -> str:
    normalized = database_url.strip()
    for prefix in ("postgresql+psycopg2://", "postgresql+psycopg://"):
        if normalized.startswith(prefix):
            return "postgresql://" + normalized[len(prefix):]
    return normalized


def database_name_from_url(database_url: str) -> str:
    parsed = urlsplit(database_url)
    db_name = parsed.path.lstrip("/")
    if not db_name:
        raise ValueError("DATABASE_URL must include database name")
    return db_name


def maintenance_url(database_url: str) -> str:
    parsed = urlsplit(database_url)
    return urlunsplit((parsed.scheme, parsed.netloc, "/postgres", parsed.query, parsed.fragment))


def create_database_if_missing(database_url: str) -> None:
    db_name = database_name_from_url(database_url)
    with psycopg.connect(maintenance_url(database_url), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
            if cur.fetchone():
                print(f"Database already exists: {db_name}")
                return
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
            print(f"Database created: {db_name}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply PostgreSQL v1 schema.")
    parser.add_argument(
        "--schema",
        default=str(SCHEMA_PATH),
        help="Path to SQL schema file.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read schema and print basic info without connecting to PostgreSQL.",
    )
    parser.add_argument(
        "--create-db",
        action="store_true",
        help="Create target database from DATABASE_URL if it does not exist.",
    )
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")

    schema_path = Path(args.schema)
    if not schema_path.exists():
        print(f"Schema file not found: {schema_path}", file=sys.stderr)
        return 1

    schema_sql = schema_path.read_text(encoding="utf-8")
    if args.dry_run:
        create_tables = schema_sql.count("CREATE TABLE")
        print(f"Schema: {schema_path}")
        print(f"Size: {len(schema_sql)} chars")
        print(f"CREATE TABLE statements: {create_tables}")
        return 0

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set in environment or .env", file=sys.stderr)
        return 1
    database_url = normalize_postgres_url(database_url)

    if args.create_db:
        create_database_if_missing(database_url)

    print(f"Applying schema from {schema_path}")
    with psycopg.connect(database_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(schema_sql)

    print("PostgreSQL schema applied successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
