-- 051_bitrix_bot_message_seen.sql
-- Idempotent. Dedup of inbound imbot chat messages: Bitrix delivers ONIMBOTMESSAGEADD
-- webhooks at-least-once, and without a first-sight claim a re-delivered message is
-- answered twice (incident 2026-07-13: Alexander got a duplicate reply). The handler
-- claims message_id here before any side effect; a duplicate INSERT is a no-op.
CREATE TABLE IF NOT EXISTS bitrix_bot_message_seen (
    message_id   bigint PRIMARY KEY,
    bot_id       bigint,
    dialog_id    text,
    from_user_id bigint,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_bbms_created ON bitrix_bot_message_seen (created_at);
