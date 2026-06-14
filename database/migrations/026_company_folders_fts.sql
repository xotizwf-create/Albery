-- 026: Russian full-text search for company knowledge (hybrid search, stage 1).
-- Adds a stored generated tsvector over (name + content) using the Russian dictionary
-- so search_company_knowledge can match by word stems (отчётность ↔ отчёты), not just
-- substrings. Combined at query time with the existing pg_trgm indexes (fuzzy/typos)
-- and ILIKE (exact substring). Idempotent — listed in ensure_postgres ALWAYS_APPLY.

ALTER TABLE company_folders
    ADD COLUMN IF NOT EXISTS content_tsv tsvector
    GENERATED ALWAYS AS (
        to_tsvector('russian', coalesce(name, '') || ' ' || coalesce(content, ''))
    ) STORED;

CREATE INDEX IF NOT EXISTS idx_company_folders_content_tsv
    ON company_folders USING gin (content_tsv);
