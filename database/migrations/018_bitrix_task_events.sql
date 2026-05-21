CREATE TABLE IF NOT EXISTS bitrix_task_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    event_name text NOT NULL,
    bitrix_task_id bigint NOT NULL,
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','processing','done','error','ignored')),
    attempts int NOT NULL DEFAULT 0,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_text text NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_bitrix_task_events_status_received
    ON bitrix_task_events(status, received_at);

CREATE INDEX IF NOT EXISTS idx_bitrix_task_events_task_received
    ON bitrix_task_events(bitrix_task_id, received_at DESC);
