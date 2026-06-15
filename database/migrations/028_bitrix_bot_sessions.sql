-- 028: per-dialog session state for the Bitrix chat-bot.
-- Drives the session lifecycle: epoch (rotates on 8h idle or after a turn cap),
-- turn counter, carried summary (conversation-summary-buffer) and last activity.
-- Idempotent; registered in ensure_postgres REQUIRED_TABLE_MIGRATIONS.

CREATE TABLE IF NOT EXISTS bitrix_bot_sessions (
    dialog_id TEXT PRIMARY KEY,
    epoch INTEGER NOT NULL DEFAULT 1,
    turns INTEGER NOT NULL DEFAULT 0,
    summary TEXT,
    last_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
