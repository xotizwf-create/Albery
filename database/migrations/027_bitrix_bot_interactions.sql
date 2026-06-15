-- 027: interaction log for the Bitrix24 Hermes-brain chat-bot.
-- One row per user message → bot answer: who asked, in which dialog, access tier,
-- the question/answer, latency and status. Backs analytics and the usage digest.
-- Idempotent (CREATE ... IF NOT EXISTS); registered in ensure_postgres REQUIRED_TABLE_MIGRATIONS.

CREATE TABLE IF NOT EXISTS bitrix_bot_interactions (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    dialog_id TEXT,
    bitrix_user_id BIGINT,
    tier TEXT NOT NULL DEFAULT 'faq',
    session_name TEXT,
    question TEXT,
    answer TEXT,
    latency_ms INTEGER,
    status TEXT NOT NULL DEFAULT 'ok',
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_bbi_created ON bitrix_bot_interactions (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_bbi_user ON bitrix_bot_interactions (bitrix_user_id);
