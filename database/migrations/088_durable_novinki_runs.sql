CREATE TABLE IF NOT EXISTS novinki_processing_runs (
    id bigserial PRIMARY KEY,
    snapshot_key text NOT NULL UNIQUE,
    folder_id text NOT NULL,
    source_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    status text NOT NULL DEFAULT 'analyzing'
        CHECK (status IN ('analyzing','sheet_ready','task_sending','task_ready','cleanup','done','review')),
    source_count integer NOT NULL DEFAULT 0,
    recommendation_count integer NULL,
    sheet_id text NULL,
    sheet_url text NULL,
    bitrix_task_id bigint NULL,
    last_error text NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz NULL
);

CREATE INDEX IF NOT EXISTS idx_novinki_processing_runs_status_updated
    ON novinki_processing_runs(status, updated_at);

CREATE UNIQUE INDEX IF NOT EXISTS uq_novinki_processing_runs_active_folder
    ON novinki_processing_runs(folder_id)
    WHERE status <> 'done';
