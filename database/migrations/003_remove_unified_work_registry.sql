BEGIN;

DROP VIEW IF EXISTS v_work_items_full;

DROP INDEX IF EXISTS idx_ara_work_item;
ALTER TABLE IF EXISTS ai_request_artifacts
    DROP COLUMN IF EXISTS work_item_id;

ALTER TABLE IF EXISTS user_goals
    DROP CONSTRAINT IF EXISTS fk_user_goals_source_artifact,
    DROP COLUMN IF EXISTS source_artifact_id;

DROP TABLE IF EXISTS work_item_scores;
DROP TABLE IF EXISTS work_items;
DROP TABLE IF EXISTS source_artifacts;

CREATE TABLE IF NOT EXISTS chat_report_items (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chat_daily_report_id     UUID NOT NULL REFERENCES chat_daily_reports(id) ON DELETE CASCADE,
    chat_id                  UUID NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    report_date              DATE NOT NULL,
    item_type                TEXT NOT NULL CHECK (item_type IN
                                ('decision','risk','blocker','commitment',
                                 'question','unanswered_question','goal',
                                 'action','bitrix_reference','result',
                                 'next_step','note','previous_day_task')),
    item_text                TEXT NOT NULL,
    user_id                  UUID REFERENCES users(id) ON DELETE SET NULL,
    bitrix_task_id           UUID REFERENCES bitrix_tasks(id) ON DELETE SET NULL,
    confidence               NUMERIC(4,3) CHECK (confidence BETWEEN 0 AND 1),
    evidence_message_ids     BIGINT[],
    raw_json                 JSONB,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cri_report ON chat_report_items(chat_daily_report_id);
CREATE INDEX IF NOT EXISTS idx_cri_chat_date ON chat_report_items(chat_id, report_date DESC);
CREATE INDEX IF NOT EXISTS idx_cri_type ON chat_report_items(item_type);
CREATE INDEX IF NOT EXISTS idx_cri_user ON chat_report_items(user_id) WHERE user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cri_bitrix_task ON chat_report_items(bitrix_task_id) WHERE bitrix_task_id IS NOT NULL;

COMMIT;
