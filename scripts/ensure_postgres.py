from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
from dotenv import load_dotenv
from psycopg import sql


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "database" / "postgres_schema_v1.sql"
MIGRATIONS_DIR = ROOT / "database" / "migrations"
REQUIRED_TABLE_MIGRATIONS = {
    "wb_finance_details": "053_wb_analytics.sql",
    "goal_progress_events": "006_goal_progress_events.sql",
    "chat_overall_daily_reports": "007_chat_overall_daily_reports.sql",
    "chat_overall_weekly_reports": "008_chat_overall_weekly_reports.sql",
    "chat_weekly_reports": "009_chat_weekly_reports.sql",
    "company_profile": "012_company_profile.sql",
    "company_folders": "013_company_folders.sql",
    "company_drive_sources": "014_company_drive_sources.sql",
    "ai_instruction_folders": "015_ai_instruction_folders.sql",
    "chat_day_syncs": "016_chat_day_syncs.sql",
    "bitrix_task_events": "018_bitrix_task_events.sql",
    "zoom_recording_events": "019_zoom_recording_events.sql",
    "owner_recommendation_events": "020_recommendation_feedback_events.sql",
    "company_drive_folders": "021_company_drive_folders.sql",
    "integration_sync_status": "023_integration_sync_status.sql",
    "bitrix_bot_interactions": "027_bitrix_bot_interactions.sql",
    "bitrix_bot_sessions": "028_bitrix_bot_sessions.sql",
    "company_knowledge_chunks": "029_company_knowledge_chunks.sql",
    "ai_agent_capabilities": "031_ai_agent_capabilities.sql",
    "bitrix_error_reports": "032_bitrix_error_reports.sql",
    "agent_access": "034_agent_access.sql",
    "access_requests": "036_access_requests.sql",
}

REQUIRED_FUNCTION_MIGRATIONS = {
    "ensure_chat_messages_partition": "017_fix_chat_message_partition_privileges.sql",
}

ALWAYS_APPLY_MIGRATIONS = [
    # Idempotent WB resumable sync state. 055 also covers catalogue columns
    # that older, already-created WB schemas could otherwise silently miss.
    "054_wb_sync_state_v2.sql",
    "055_wb_async_reports.sql",
    "056_wb_finance_pagination.sql",
    # Idempotent: system_key on kind='system' automation rows (executor mapping).
    "057_system_automation_keys.sql",
    # Idempotent: self-hosted CRM lead questionnaires (/form/<token> -> deal in a funnel).
    "058_crm_lead_forms.sql",
    "022_chats_personal_dialog_types.sql",
    "024_chat_report_hot_path_indexes.sql",
    "025_mcp_search_indexes.sql",
    "026_company_folders_fts.sql",
    "030_bitrix_bot_session_reset.sql",
    "033_error_report_context_views.sql",
    "035_agent_access_none_tier.sql",
    # Fully idempotent (IF NOT EXISTS everywhere) — covers both the subagent
    # tables and the agent_slug column on bitrix_bot_interactions.
    "037_agents.sql",
    # Idempotent: per-agent tool/instruction/skill config (tools_customized flag
    # + agent_knowledge_links link table).
    "038_agent_config.sql",
    # Idempotent: add the 'developer' access level (drop/recreate tier CHECK).
    "039_agent_developer_level.sql",
    # Idempotent: agent job title (position) synced with Bitrix WORK_POSITION.
    "040_agent_position.sql",
    # Idempotent: per-agent scheduled automations + seed of the legacy Hermes crons.
    "041_agent_automations.sql",
    # Idempotent: agent_slug on bitrix_error_reports (per-agent monitoring feed).
    "042_error_report_agent.sql",
    # Idempotent: durable in-flight turn registry (restart/crash recovery net).
    "043_inflight_turns.sql",
    # Idempotent: attachment store (full-text + re-upload) + task-comment mention dedupe.
    "044_task_tools_attachments.sql",
    # Idempotent: registry of recurring (regular) Bitrix tasks created via the agent.
    "045_recurring_tasks.sql",
    # Idempotent: recurring tasks fired by the agent's own scheduler (not Bitrix REPLICATE) —
    # adds scheduling state (next_run_at/last_*) + a jsonb spec to reproduce the one-off task.
    "046_recurring_tasks_scheduler.sql",
    # Idempotent: agent_slug on recurring tasks (shown in the agent's «Автоматизации» tab)
    # + source_disk_file_id cache key on the attachment store (task-comment files).
    "047_recurring_agent_slug_attachment_source.sql",
    # Idempotent: stored weekly TG news digests (news agent reuses the latest for ad-hoc questions).
    "048_tg_news_digests.sql",
    # Idempotent: offer-to-help comments on agent-created tasks (reply routing without mentions).
    "049_task_agent_offers.sql",
    # Idempotent: daily task check-in (12:00) + per-employee agent dossier.
    "050_task_checkin_dossier.sql",
    # Idempotent: dedup table for inbound imbot chat messages (at-least-once delivery).
    "051_bitrix_bot_message_seen.sql",
    # Idempotent: full agent<->human message journal (what the owner sees in the UI).
    "052_bitrix_bot_messages.sql",
]


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


def create_database_if_missing(database_url: str, admin_url: str | None = None) -> None:
    db_name = database_name_from_url(database_url)
    connect_url = admin_url or maintenance_url(database_url)
    with psycopg.connect(connect_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
            if cur.fetchone():
                print(f"PostgreSQL database exists: {db_name}")
                return
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
            print(f"PostgreSQL database created: {db_name}")


def schema_is_initialized(database_url: str) -> bool:
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.users')")
            return cur.fetchone()[0] is not None


def apply_schema(database_url: str) -> None:
    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(f"Schema file not found: {SCHEMA_PATH}")
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with psycopg.connect(database_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(schema_sql)
    print("PostgreSQL schema applied.")


def apply_required_migrations(database_url: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            for table_name, migration_name in REQUIRED_TABLE_MIGRATIONS.items():
                cur.execute("SELECT to_regclass(%s)", (f"public.{table_name}",))
                if cur.fetchone()[0] is not None:
                    continue
                migration_path = MIGRATIONS_DIR / migration_name
                if not migration_path.exists():
                    raise FileNotFoundError(f"Migration file not found: {migration_path}")
                cur.execute(migration_path.read_text(encoding="utf-8"))
                print(f"PostgreSQL migration applied: {migration_name}")
            for function_name, migration_name in REQUIRED_FUNCTION_MIGRATIONS.items():
                cur.execute(
                    """
                    SELECT p.prosecdef
                    FROM pg_proc p
                    JOIN pg_namespace n ON n.oid = p.pronamespace
                    WHERE n.nspname = 'public' AND p.proname = %s
                    """,
                    (function_name,),
                )
                row = cur.fetchone()
                if row and row[0]:
                    continue
                migration_path = MIGRATIONS_DIR / migration_name
                if not migration_path.exists():
                    raise FileNotFoundError(f"Migration file not found: {migration_path}")
                cur.execute(migration_path.read_text(encoding="utf-8"))
                print(f"PostgreSQL migration applied: {migration_name}")
            for migration_name in ALWAYS_APPLY_MIGRATIONS:
                migration_path = MIGRATIONS_DIR / migration_name
                if not migration_path.exists():
                    raise FileNotFoundError(f"Migration file not found: {migration_path}")
                cur.execute(migration_path.read_text(encoding="utf-8"))
                print(f"PostgreSQL migration applied: {migration_name}")


def main() -> int:
    load_dotenv(ROOT / ".env")
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        print("DATABASE_URL is not set in .env", file=sys.stderr)
        return 1

    database_url = normalize_postgres_url(database_url)
    admin_url = normalize_postgres_url(os.getenv("DATABASE_ADMIN_URL", "").strip()) or None

    create_database_if_missing(database_url, admin_url=admin_url)
    if schema_is_initialized(database_url):
        print("PostgreSQL schema already initialized.")
        apply_required_migrations(database_url)
        return 0

    apply_schema(database_url)
    apply_required_migrations(database_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
