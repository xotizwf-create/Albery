CREATE TABLE IF NOT EXISTS chat_day_syncs (
    chat_id uuid NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    sync_date date NOT NULL,
    messages_count integer NOT NULL DEFAULT 0,
    synced_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (chat_id, sync_date)
);

CREATE INDEX IF NOT EXISTS idx_chat_day_syncs_date
    ON chat_day_syncs(sync_date);
