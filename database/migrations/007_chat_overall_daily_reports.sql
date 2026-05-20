CREATE TABLE IF NOT EXISTS chat_overall_daily_reports (
    id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_date                DATE NOT NULL,
    version                    INT NOT NULL DEFAULT 1,
    is_current                 BOOLEAN NOT NULL DEFAULT TRUE,
    generated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    chats_count                INT NOT NULL DEFAULT 0,
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
    raw_json                   JSONB,
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (report_date, version)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_codr_current
    ON chat_overall_daily_reports(report_date)
    WHERE is_current = TRUE;

CREATE INDEX IF NOT EXISTS idx_codr_date
    ON chat_overall_daily_reports(report_date DESC);

CREATE OR REPLACE FUNCTION trg_switch_is_current_chat_overall_daily() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.is_current THEN
        UPDATE chat_overall_daily_reports
            SET is_current = FALSE
            WHERE report_date = NEW.report_date
              AND id <> NEW.id
              AND is_current = TRUE;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_codr_switch_current ON chat_overall_daily_reports;
CREATE TRIGGER trg_codr_switch_current
    BEFORE INSERT OR UPDATE OF is_current ON chat_overall_daily_reports
    FOR EACH ROW WHEN (NEW.is_current = TRUE)
    EXECUTE FUNCTION trg_switch_is_current_chat_overall_daily();

DROP TRIGGER IF EXISTS trg_chat_overall_daily_reports_updated_at ON chat_overall_daily_reports;
CREATE TRIGGER trg_chat_overall_daily_reports_updated_at
    BEFORE UPDATE ON chat_overall_daily_reports
    FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();
