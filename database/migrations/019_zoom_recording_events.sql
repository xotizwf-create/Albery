CREATE TABLE IF NOT EXISTS zoom_recording_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    event_name text NOT NULL,
    zoom_uuid text NOT NULL,
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','processing','done','error','ignored')),
    attempts int NOT NULL DEFAULT 0,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_text text NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_zoom_recording_events_status_received
    ON zoom_recording_events(status, received_at);

CREATE INDEX IF NOT EXISTS idx_zoom_recording_events_uuid_received
    ON zoom_recording_events(zoom_uuid, received_at DESC);
