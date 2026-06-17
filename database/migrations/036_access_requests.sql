-- 036: access-upgrade requests forwarded to the owner. When a user is refused for lack of
-- access and agrees to escalate, the bot logs the request here and notifies the owner in Telegram.
-- Idempotent; in ensure_postgres REQUIRED_TABLE_MIGRATIONS.

CREATE TABLE IF NOT EXISTS access_requests (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    dialog_id TEXT,
    bitrix_user_id BIGINT,
    requester_name TEXT,
    request_text TEXT,
    delivered BOOLEAN NOT NULL DEFAULT FALSE,
    delivery_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_access_requests_created ON access_requests (created_at DESC);
