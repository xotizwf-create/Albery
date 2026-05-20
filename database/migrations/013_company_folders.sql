CREATE TABLE IF NOT EXISTS company_folders (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_id uuid REFERENCES company_folders(id) ON DELETE CASCADE,
    name text NOT NULL,
    content text NOT NULL DEFAULT '',
    sort_order int NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (btrim(name) <> '')
);

CREATE INDEX IF NOT EXISTS idx_company_folders_parent
    ON company_folders(parent_id, sort_order, name);

WITH source_profile AS (
    SELECT COALESCE(content, '') AS content
    FROM company_profile
    WHERE profile_key = 'main'
)
INSERT INTO company_folders (parent_id, name, content, sort_order)
SELECT NULL, 'О компании', COALESCE((SELECT content FROM source_profile), ''), 0
WHERE NOT EXISTS (
    SELECT 1
    FROM company_folders
    WHERE parent_id IS NULL
      AND lower(name) = lower('О компании')
);
