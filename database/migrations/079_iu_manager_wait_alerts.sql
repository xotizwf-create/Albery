-- Durable 10/30/60-minute and next-morning alerts for unanswered IU leads.
CREATE TABLE IF NOT EXISTS iu_manager_wait_alerts (
    id                  bigserial PRIMARY KEY,
    conversation_id     bigint NOT NULL
        REFERENCES funnel_workspace_conversations(id) ON DELETE CASCADE,
    anchor_message_id   bigint NOT NULL
        REFERENCES funnel_workspace_messages(id) ON DELETE CASCADE,
    anchor_occurred_at  timestamptz NOT NULL,
    kind                text NOT NULL
        CHECK (kind IN ('10m', '30m', '60m', 'morning')),
    due_at              timestamptz NOT NULL,
    status              text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'leased', 'sent', 'cancelled')),
    locked_by           text,
    locked_until        timestamptz,
    attempts            integer NOT NULL DEFAULT 0,
    last_error          text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, anchor_message_id, kind)
);

CREATE INDEX IF NOT EXISTS idx_iu_manager_wait_alerts_due
    ON iu_manager_wait_alerts (due_at, anchor_occurred_at)
    WHERE status = 'pending';

