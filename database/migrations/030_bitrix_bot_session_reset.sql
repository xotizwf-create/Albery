-- 030: manual session reset for the Bitrix chat-bot ("new session" button/command).
-- The bot's memory is injected from bitrix_bot_interactions by dialog_id (not by epoch),
-- so bumping the epoch alone would NOT clear context. history_floor_id records the max
-- interaction id at reset time; _b24_recent_history only injects rows above the floor, so
-- a reset truly starts a clean conversation. Idempotent; in ensure_postgres ALWAYS_APPLY.

ALTER TABLE bitrix_bot_sessions
    ADD COLUMN IF NOT EXISTS history_floor_id BIGINT NOT NULL DEFAULT 0;
