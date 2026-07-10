-- 048: stored weekly TG news digests for the «Новостной агент».
-- The agent saves each digest here so ad-hoc questions reuse the latest one instead of
-- rebuilding it from scratch (economical). Idempotent.
CREATE TABLE IF NOT EXISTS tg_news_digests (
    id           BIGSERIAL PRIMARY KEY,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    period_days  INTEGER     NOT NULL DEFAULT 7,
    summary      TEXT        NOT NULL,
    meta         JSONB       NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_tg_news_digests_created_at ON tg_news_digests (created_at DESC);
