-- 029: chunked company knowledge for RAG-style retrieval (stage A: lexical over chunks).
--
-- Why: search_company_knowledge returned WHOLE documents (up to 50 rows, avg ~24 KB,
-- max ~413 KB) — a single search could dump hundreds of KB into the model context and
-- burn the Codex limit. We split each company_folders document into ~400-token chunks
-- and let search return only the few most relevant PASSAGES, not whole files.
--
-- Stage A ranks chunks with the same Russian FTS + pg_trgm hybrid already used in 026.
-- Stage B (later) adds an embedding column for semantic recall; the table is shaped so
-- that only an ALTER ... ADD COLUMN is needed then.
--
-- Chunks are (re)built in Python (shared/knowledge_chunks.py); the search tool lazily
-- reconciles changed documents via the signature in company_knowledge_meta, so the
-- index stays fresh automatically when Drive sync or a manual edit changes a document.

CREATE TABLE IF NOT EXISTS company_knowledge_chunks (
    id            bigserial PRIMARY KEY,
    folder_id     uuid NOT NULL REFERENCES company_folders(id) ON DELETE CASCADE,
    chunk_index   integer NOT NULL,
    name          text NOT NULL,
    path          text,
    content       text NOT NULL,
    content_tsv   tsvector GENERATED ALWAYS AS (
        to_tsvector('russian', coalesce(name, '') || ' ' || coalesce(content, ''))
    ) STORED,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (folder_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_tsv
    ON company_knowledge_chunks USING gin (content_tsv);
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_content_trgm
    ON company_knowledge_chunks USING gin (content gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_name_trgm
    ON company_knowledge_chunks USING gin (name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_folder
    ON company_knowledge_chunks (folder_id);

-- Per-folder content signature, so a re-chunk only touches documents that changed.
CREATE TABLE IF NOT EXISTS company_knowledge_chunk_state (
    folder_id     uuid PRIMARY KEY REFERENCES company_folders(id) ON DELETE CASCADE,
    content_hash  text NOT NULL,
    chunk_count   integer NOT NULL DEFAULT 0,
    updated_at    timestamptz NOT NULL DEFAULT now()
);

-- Single-row corpus signature for a cheap "did anything change?" check before any work.
CREATE TABLE IF NOT EXISTS company_knowledge_meta (
    key   text PRIMARY KEY,
    value text NOT NULL
);
