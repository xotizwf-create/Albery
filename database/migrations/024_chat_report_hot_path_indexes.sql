CREATE INDEX IF NOT EXISTS idx_cdr_chat_date
    ON chat_daily_reports(chat_id, report_date DESC);

CREATE INDEX IF NOT EXISTS idx_cmf_chat_day
    ON chat_message_files(chat_id, message_day);
