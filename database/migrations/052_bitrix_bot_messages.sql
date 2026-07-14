-- 052_bitrix_bot_messages.sql
-- Idempotent. FULL message journal of every agent<->human message.
-- Why: the UI built conversations from bitrix_bot_interactions, which logs a TURN (question+answer)
-- and only AFTER the agent answered. So (a) proactive outbound messages with no question — DMs from
-- the daily task check-in, task offer comments, notification-channel posts, digests, owner reports —
-- were invisible to the owner, and (b) an employee's message appeared only once the agent replied.
-- This table records each message on arrival/send, so the owner sees absolutely everything, live.
CREATE TABLE IF NOT EXISTS bitrix_bot_messages (
    id                bigserial PRIMARY KEY,
    created_at        timestamptz NOT NULL DEFAULT now(),
    agent_slug        text,               -- NULL = main agent
    bot_id            bigint,
    dialog_id         text NOT NULL,      -- user id (DM) | chatNNN (channel) | task-<id>
    bitrix_user_id    bigint,
    direction         text NOT NULL,      -- 'in' | 'out'
    kind              text NOT NULL DEFAULT 'chat',  -- chat | notification | task_comment | system
    text              text NOT NULL DEFAULT '',
    bitrix_message_id bigint,
    meta              jsonb
);
CREATE INDEX IF NOT EXISTS idx_bbm_dialog  ON bitrix_bot_messages (dialog_id, id);
CREATE INDEX IF NOT EXISTS idx_bbm_agent   ON bitrix_bot_messages (agent_slug, dialog_id, id);
CREATE INDEX IF NOT EXISTS idx_bbm_created ON bitrix_bot_messages (created_at DESC);
