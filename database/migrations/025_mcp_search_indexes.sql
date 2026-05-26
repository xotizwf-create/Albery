CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS idx_company_folders_name_trgm
    ON company_folders USING gin (name gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_company_folders_content_trgm
    ON company_folders USING gin (content gin_trgm_ops)
    WHERE content IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_bitrix_tasks_title_trgm
    ON bitrix_tasks USING gin (title gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_bitrix_tasks_description_trgm
    ON bitrix_tasks USING gin (description gin_trgm_ops)
    WHERE description IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_bitrix_tasks_updated_expr
    ON bitrix_tasks ((COALESCE(updated_at_bitrix, created_at_bitrix, deadline_at, created_at)) DESC);

CREATE INDEX IF NOT EXISTS idx_bitrix_tasks_responsible_updated
    ON bitrix_tasks (
        responsible_bitrix_user_id,
        (COALESCE(updated_at_bitrix, created_at_bitrix, deadline_at, created_at)) DESC
    );

CREATE INDEX IF NOT EXISTS idx_chat_messages_text_trgm
    ON chat_messages USING gin (message_text gin_trgm_ops)
    WHERE message_text IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_chat_messages_day_date_msg
    ON chat_messages (message_day, message_date, bitrix_message_id);

CREATE INDEX IF NOT EXISTS idx_chat_file_ocr_success_text_trgm
    ON chat_file_ocr USING gin (ocr_text gin_trgm_ops)
    WHERE ocr_status = 'success' AND ocr_text IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_zoom_transcript_segments_text_trgm
    ON zoom_call_transcript_segments USING gin (text gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_zoom_transcript_segments_call_cue
    ON zoom_call_transcript_segments (call_id, cue_index);

CREATE INDEX IF NOT EXISTS idx_zoom_calls_topic_trgm
    ON zoom_calls USING gin (topic gin_trgm_ops)
    WHERE topic IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_zoom_calls_technical_topic_trgm
    ON zoom_calls USING gin (technical_topic gin_trgm_ops)
    WHERE technical_topic IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_zoom_calls_transcript_text_trgm
    ON zoom_calls USING gin (transcript_text gin_trgm_ops)
    WHERE transcript_text IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_zoom_calls_date_start
    ON zoom_calls (call_date DESC, start_time_msk DESC);
