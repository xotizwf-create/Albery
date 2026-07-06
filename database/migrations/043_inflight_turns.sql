-- 043: durable registry of in-flight brain turns so a restart/OOM/crash can NEVER
-- leave a user hanging. A row is inserted when a turn starts and deleted when it
-- finishes; anything still here at boot was killed mid-flight -> the bot notifies
-- that user to resend instead of staying silent forever with a stuck "typing…".
CREATE TABLE IF NOT EXISTS bitrix_inflight_turns (
    id                UUID PRIMARY KEY,
    bot_id            TEXT,
    dialog_id         TEXT NOT NULL,
    agent_slug        TEXT,
    from_user_id      TEXT,
    message_id        TEXT,
    status_message_id TEXT,
    user_preview      TEXT,
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_inflight_started ON bitrix_inflight_turns (started_at);
