BEGIN;

ALTER TABLE owner_manager_recommendations
    DROP CONSTRAINT IF EXISTS owner_manager_recommendations_status_check;

ALTER TABLE owner_manager_recommendations
    ADD CONSTRAINT owner_manager_recommendations_status_check CHECK (status IN (
        'new',
        'draft',
        'queued',
        'sent',
        'seen',
        'acked',
        'accepted',
        'in_progress',
        'needs_clarification',
        'disagreed',
        'delegated',
        'done',
        'rejected',
        'no_response',
        'overdue',
        'requires_manager_review',
        'cancelled',
        'error'
    ));

ALTER TABLE owner_manager_recommendations
    ADD COLUMN IF NOT EXISTS expected_action text,
    ADD COLUMN IF NOT EXISTS response_due_at timestamptz,
    ADD COLUMN IF NOT EXISTS execution_due_at timestamptz,
    ADD COLUMN IF NOT EXISTS feedback_chat_id uuid REFERENCES chats(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS feedback_dialog_id text,
    ADD COLUMN IF NOT EXISTS current_interpretation jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS manager_review_required boolean NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS sent_at timestamptz,
    ADD COLUMN IF NOT EXISTS last_response_at timestamptz,
    ADD COLUMN IF NOT EXISTS closed_at timestamptz;

ALTER TABLE owner_recommendation_dispatches
    DROP CONSTRAINT IF EXISTS owner_recommendation_dispatches_channel_check;

ALTER TABLE owner_recommendation_dispatches
    ADD CONSTRAINT owner_recommendation_dispatches_channel_check CHECK (channel IN (
        'bitrix_task_comment',
        'bitrix_im',
        'bitrix_notification',
        'bitrix_task_create',
        'manual'
    ));

CREATE TABLE IF NOT EXISTS owner_recommendation_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    recommendation_id uuid NOT NULL REFERENCES owner_manager_recommendations(id) ON DELETE CASCADE,
    event_type text NOT NULL CHECK (event_type IN (
        'created',
        'sent',
        'delivered',
        'seen',
        'employee_replied',
        'ai_interpreted',
        'status_changed',
        'manager_reviewed',
        'task_created',
        'closed',
        'source_found'
    )),
    author_type text NOT NULL DEFAULT 'system' CHECK (author_type IN ('system','ai','manager','employee')),
    author_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
    author_bitrix_user_id bigint,
    chat_id uuid REFERENCES chats(id) ON DELETE SET NULL,
    dialog_id text,
    chat_message_id uuid,
    chat_message_day date,
    bitrix_message_id bigint,
    bitrix_task_id uuid REFERENCES bitrix_tasks(id) ON DELETE SET NULL,
    bitrix_task_external_id bigint,
    zoom_call_id uuid REFERENCES zoom_calls(id) ON DELETE SET NULL,
    old_status text,
    new_status text,
    event_text text,
    interpretation jsonb NOT NULL DEFAULT '{}'::jsonb,
    source_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    event_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ore_recommendation_event_at
    ON owner_recommendation_events(recommendation_id, event_at DESC);
CREATE INDEX IF NOT EXISTS idx_ore_status
    ON owner_recommendation_events(new_status, event_at DESC) WHERE new_status IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ore_chat_day
    ON owner_recommendation_events(chat_id, chat_message_day, event_at DESC)
    WHERE chat_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ore_bitrix_message
    ON owner_recommendation_events(bitrix_message_id, chat_message_day)
    WHERE bitrix_message_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_omr_feedback_chat
    ON owner_manager_recommendations(feedback_chat_id, status, created_at DESC)
    WHERE feedback_chat_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_omr_review_required
    ON owner_manager_recommendations(manager_review_required, updated_at DESC)
    WHERE manager_review_required = TRUE;
CREATE INDEX IF NOT EXISTS idx_omr_response_due
    ON owner_manager_recommendations(response_due_at)
    WHERE response_due_at IS NOT NULL;

COMMIT;
