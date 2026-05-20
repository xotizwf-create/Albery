CREATE TABLE IF NOT EXISTS goal_progress_events (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    goal_id                  UUID NOT NULL REFERENCES user_goals(id) ON DELETE CASCADE,
    chat_daily_report_id     UUID REFERENCES chat_daily_reports(id) ON DELETE CASCADE,
    chat_id                  UUID REFERENCES chats(id) ON DELETE CASCADE,
    report_date              DATE NOT NULL,
    status_before            TEXT CHECK (status_before IN
                              ('draft','active','done','cancelled','archived')),
    status_after             TEXT CHECK (status_after IN
                              ('draft','active','done','cancelled','archived')),
    progress_text            TEXT NOT NULL,
    progress_percent         NUMERIC(5,2) CHECK (progress_percent BETWEEN 0 AND 100),
    metric_value             TEXT,
    risk_level               TEXT CHECK (risk_level IN ('low','medium','high','unknown')),
    confidence               NUMERIC(4,3) CHECK (confidence BETWEEN 0 AND 1),
    evidence_message_ids     BIGINT[],
    raw_json                 JSONB,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_gpe_goal_date
    ON goal_progress_events(goal_id, report_date DESC, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_gpe_chat_report
    ON goal_progress_events(chat_daily_report_id)
    WHERE chat_daily_report_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_gpe_chat_date
    ON goal_progress_events(chat_id, report_date DESC)
    WHERE chat_id IS NOT NULL;
