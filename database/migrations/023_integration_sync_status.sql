CREATE TABLE IF NOT EXISTS integration_sync_status (
    sync_key text PRIMARY KEY,
    last_success_at timestamptz NULL,
    last_attempt_at timestamptz NOT NULL DEFAULT now(),
    status text NOT NULL DEFAULT 'unknown',
    raw_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now()
);

