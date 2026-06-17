-- 034: per-user access tier for the Bitrix chat-bot, managed live from the web UI
-- (/admin/access). The bot resolves a user's tier from this table (with the env owner id as
-- an un-removable bootstrap admin). Idempotent; in ensure_postgres REQUIRED_TABLE_MIGRATIONS.

CREATE TABLE IF NOT EXISTS agent_access (
    bitrix_user_id BIGINT PRIMARY KEY,
    tier TEXT NOT NULL DEFAULT 'faq' CHECK (tier IN ('admin', 'ops', 'faq')),
    display_name TEXT,
    note TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Seed the current state so the UI reflects reality on day one:
-- 16 = Александр (admin, also the code bootstrap), 14 = the legacy env "full" user (ops).
INSERT INTO agent_access (bitrix_user_id, tier, display_name) VALUES
    (16, 'admin', 'Александр Никитенко'),
    (14, 'ops', NULL)
ON CONFLICT (bitrix_user_id) DO NOTHING;
