ALTER TABLE ai_requests
    DROP CONSTRAINT IF EXISTS ai_requests_request_type_check;

ALTER TABLE ai_requests
    ADD CONSTRAINT ai_requests_request_type_check
    CHECK (request_type IN (
        'image_ocr','chat_daily_analysis','chat_overall_weekly_analysis',
        'user_daily_report','weekly_report','monthly_report',
        'quarterly_report','yearly_report','memory_update'
    ));

CREATE TABLE IF NOT EXISTS chat_overall_weekly_reports (
    id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    period_start               DATE NOT NULL,
    period_end                 DATE NOT NULL,
    version                    INT NOT NULL DEFAULT 1,
    is_current                 BOOLEAN NOT NULL DEFAULT TRUE,
    ai_request_id              UUID REFERENCES ai_requests(id) ON DELETE SET NULL,
    prompt_id                  UUID REFERENCES ai_prompts(id) ON DELETE SET NULL,
    generated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    days_count                 INT NOT NULL DEFAULT 0,
    chats_count                INT NOT NULL DEFAULT 0,
    daily_reports_count        INT NOT NULL DEFAULT 0,
    messages_count             INT NOT NULL DEFAULT 0,
    goals_created_count        INT NOT NULL DEFAULT 0,
    goal_updates_count         INT NOT NULL DEFAULT 0,
    commitments_count          INT NOT NULL DEFAULT 0,
    results_count              INT NOT NULL DEFAULT 0,
    next_steps_count           INT NOT NULL DEFAULT 0,
    risks_count                INT NOT NULL DEFAULT 0,
    blockers_count             INT NOT NULL DEFAULT 0,
    unresolved_questions_count INT NOT NULL DEFAULT 0,
    done_goal_updates_count    INT NOT NULL DEFAULT 0,
    high_risk_goal_updates_count INT NOT NULL DEFAULT 0,
    summary                    TEXT,
    dynamics_summary           TEXT,
    positives_summary          TEXT,
    problems_summary           TEXT,
    recommendations            TEXT,
    raw_json                   JSONB,
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (period_end >= period_start),
    UNIQUE (period_start, period_end, version)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_cowr_current
    ON chat_overall_weekly_reports(period_start, period_end)
    WHERE is_current = TRUE;

CREATE INDEX IF NOT EXISTS idx_cowr_period
    ON chat_overall_weekly_reports(period_start DESC, period_end DESC);

CREATE OR REPLACE FUNCTION trg_switch_is_current_chat_overall_weekly() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.is_current THEN
        UPDATE chat_overall_weekly_reports
            SET is_current = FALSE
            WHERE period_start = NEW.period_start
              AND period_end = NEW.period_end
              AND id <> NEW.id
              AND is_current = TRUE;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_cowr_switch_current ON chat_overall_weekly_reports;
CREATE TRIGGER trg_cowr_switch_current
    BEFORE INSERT OR UPDATE OF is_current ON chat_overall_weekly_reports
    FOR EACH ROW WHEN (NEW.is_current = TRUE)
    EXECUTE FUNCTION trg_switch_is_current_chat_overall_weekly();

DROP TRIGGER IF EXISTS trg_chat_overall_weekly_reports_updated_at ON chat_overall_weekly_reports;
CREATE TRIGGER trg_chat_overall_weekly_reports_updated_at
    BEFORE UPDATE ON chat_overall_weekly_reports
    FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();
