CREATE TABLE IF NOT EXISTS company_drive_folders (
    google_folder_id          TEXT PRIMARY KEY,
    folder_id                 UUID NOT NULL UNIQUE REFERENCES company_folders(id) ON DELETE CASCADE,
    parent_google_folder_id   TEXT,
    name                      TEXT NOT NULL,
    source_url                TEXT,
    drive_path                TEXT NOT NULL DEFAULT '',
    raw_json                  JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_seen_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_company_drive_folders_folder
    ON company_drive_folders(folder_id);

CREATE INDEX IF NOT EXISTS idx_company_drive_folders_parent
    ON company_drive_folders(parent_google_folder_id);

ALTER TABLE company_drive_sources
    ADD COLUMN IF NOT EXISTS parent_google_folder_id TEXT,
    ADD COLUMN IF NOT EXISTS drive_path TEXT NOT NULL DEFAULT '';
