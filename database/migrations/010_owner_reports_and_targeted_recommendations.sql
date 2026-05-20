BEGIN;

ALTER TABLE ai_requests DROP CONSTRAINT IF EXISTS ai_requests_request_type_check;
ALTER TABLE ai_requests
    ADD CONSTRAINT ai_requests_request_type_check CHECK (request_type IN (
        'image_ocr','chat_daily_analysis','chat_weekly_analysis',
        'chat_overall_weekly_analysis','chat_overall_daily_report','chat_analysis',
        'chat_weekly_report','chat_overall_weekly_report','user_daily_report',
        'owner_daily_report','owner_weekly_report',
        'weekly_report','monthly_report','quarterly_report',
        'yearly_report','memory_update','zoom_processing','image_processing'
    ));

CREATE TABLE IF NOT EXISTS owner_daily_reports (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    report_date date NOT NULL,
    version int NOT NULL DEFAULT 1,
    is_current boolean NOT NULL DEFAULT TRUE,
    ai_request_id uuid REFERENCES ai_requests(id) ON DELETE SET NULL,
    prompt_id uuid REFERENCES ai_prompts(id) ON DELETE SET NULL,
    generated_at timestamptz NOT NULL DEFAULT now(),
    summary text NULL,
    dynamics_summary text NULL,
    risks_summary text NULL,
    recommendations text NULL,
    report_text text NULL,
    raw_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (report_date, version)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_owdr_current
    ON owner_daily_reports(report_date) WHERE is_current = TRUE;
CREATE INDEX IF NOT EXISTS idx_owdr_date
    ON owner_daily_reports(report_date DESC);

CREATE TABLE IF NOT EXISTS owner_weekly_reports (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    period_start date NOT NULL,
    period_end date NOT NULL,
    version int NOT NULL DEFAULT 1,
    is_current boolean NOT NULL DEFAULT TRUE,
    ai_request_id uuid REFERENCES ai_requests(id) ON DELETE SET NULL,
    prompt_id uuid REFERENCES ai_prompts(id) ON DELETE SET NULL,
    generated_at timestamptz NOT NULL DEFAULT now(),
    summary text NULL,
    dynamics_summary text NULL,
    risks_summary text NULL,
    recommendations text NULL,
    report_text text NULL,
    raw_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (period_end >= period_start),
    UNIQUE (period_start, period_end, version)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_owwr_current
    ON owner_weekly_reports(period_start, period_end) WHERE is_current = TRUE;
CREATE INDEX IF NOT EXISTS idx_owwr_period
    ON owner_weekly_reports(period_start DESC, period_end DESC);

CREATE TABLE IF NOT EXISTS owner_manager_recommendations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_scope text NOT NULL CHECK (source_scope IN ('owner_daily','owner_weekly','owner_monthly')),
    owner_daily_report_id uuid REFERENCES owner_daily_reports(id) ON DELETE CASCADE,
    owner_weekly_report_id uuid REFERENCES owner_weekly_reports(id) ON DELETE CASCADE,
    report_date date,
    period_start date,
    period_end date,
    manager_user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    manager_bitrix_user_id bigint,
    employee_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
    employee_bitrix_user_id bigint,
    recommendation_type text NOT NULL DEFAULT 'action' CHECK (recommendation_type IN ('action','followup','risk','goal','task')),
    priority text NOT NULL DEFAULT 'medium' CHECK (priority IN ('low','medium','high','critical')),
    subject text,
    recommendation_text text NOT NULL,
    due_date date,
    bitrix_task_id uuid REFERENCES bitrix_tasks(id) ON DELETE SET NULL,
    bitrix_task_external_id bigint,
    source_chat_id uuid REFERENCES chats(id) ON DELETE SET NULL,
    source_goal_id uuid REFERENCES user_goals(id) ON DELETE SET NULL,
    source_item_id uuid REFERENCES chat_report_items(id) ON DELETE SET NULL,
    source_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'new' CHECK (status IN ('new','queued','sent','acked','done','cancelled','error')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (
        (source_scope = 'owner_daily' AND owner_daily_report_id IS NOT NULL)
        OR (source_scope = 'owner_weekly' AND owner_weekly_report_id IS NOT NULL)
        OR (source_scope = 'owner_monthly')
    )
);

CREATE INDEX IF NOT EXISTS idx_omr_manager_status
    ON owner_manager_recommendations(manager_user_id, status, priority, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_omr_daily_report
    ON owner_manager_recommendations(owner_daily_report_id) WHERE owner_daily_report_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_omr_weekly_report
    ON owner_manager_recommendations(owner_weekly_report_id) WHERE owner_weekly_report_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_omr_due_date
    ON owner_manager_recommendations(due_date) WHERE due_date IS NOT NULL;

CREATE TABLE IF NOT EXISTS owner_recommendation_dispatches (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    recommendation_id uuid NOT NULL REFERENCES owner_manager_recommendations(id) ON DELETE CASCADE,
    channel text NOT NULL CHECK (channel IN ('bitrix_task_comment','bitrix_im','bitrix_task_create','manual')),
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','sent','delivered','error','cancelled')),
    bitrix_entity_type text,
    bitrix_entity_id bigint,
    bitrix_message_id bigint,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    response_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    sent_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
    sent_at timestamptz,
    error_text text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ord_recommendation
    ON owner_recommendation_dispatches(recommendation_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ord_status
    ON owner_recommendation_dispatches(status, created_at DESC);

COMMIT;
