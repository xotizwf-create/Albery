CREATE TABLE IF NOT EXISTS company_drive_sources (
    google_file_id      TEXT PRIMARY KEY,
    folder_id           UUID NOT NULL UNIQUE REFERENCES company_folders(id) ON DELETE CASCADE,
    name                TEXT NOT NULL,
    mime_type           TEXT NOT NULL,
    source_url          TEXT,
    google_updated_at   TIMESTAMPTZ,
    raw_json            JSONB NOT NULL DEFAULT '{}'::jsonb,
    content_hash        TEXT NOT NULL DEFAULT '',
    last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_company_drive_sources_folder
    ON company_drive_sources(folder_id);
