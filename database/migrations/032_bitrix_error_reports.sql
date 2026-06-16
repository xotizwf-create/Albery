-- 032: user-submitted error reports from the Bitrix24 Hermes-brain chat-bot.
-- One row per "⚠️ Сообщить об ошибке" submission: who reported, in which dialog, the text,
-- and whether the Telegram delivery to the Albery notifications group succeeded.
-- Idempotent (CREATE ... IF NOT EXISTS); registered in ensure_postgres REQUIRED_TABLE_MIGRATIONS.

CREATE TABLE IF NOT EXISTS bitrix_error_reports (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    dialog_id TEXT,
    bitrix_user_id BIGINT,
    reporter_name TEXT,
    report_text TEXT NOT NULL,
    delivered BOOLEAN NOT NULL DEFAULT FALSE,
    delivery_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_ber_created ON bitrix_error_reports (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ber_user ON bitrix_error_reports (bitrix_user_id);
