-- 047: (1) Recurring tasks show up in the agent's «Автоматизации» tab, so every registry row
-- needs to know WHICH agent created it (agent_slug; legacy NULL rows are backfilled to 'main' —
-- they were all created through the main agent's chat).
-- (2) Files attached to TASK COMMENTS are downloaded + recognized once and cached in the
-- attachment store; source_disk_file_id lets a repeated get_task_comments call reuse the stored
-- text instead of re-downloading and re-OCRing the same Bitrix disk file.
-- Strictly additive & idempotent (ADD COLUMN IF NOT EXISTS / CREATE INDEX IF NOT EXISTS;
-- the backfill only touches rows that are still NULL). Registered in ALWAYS_APPLY_MIGRATIONS.

ALTER TABLE bitrix_recurring_tasks ADD COLUMN IF NOT EXISTS agent_slug text;
UPDATE bitrix_recurring_tasks SET agent_slug = 'main' WHERE agent_slug IS NULL;

ALTER TABLE bitrix_bot_attachments ADD COLUMN IF NOT EXISTS source_disk_file_id bigint;
CREATE INDEX IF NOT EXISTS idx_attachments_source_disk_file
    ON bitrix_bot_attachments (source_disk_file_id) WHERE source_disk_file_id IS NOT NULL;
