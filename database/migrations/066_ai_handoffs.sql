-- 066_ai_handoffs.sql
-- Idempotent. Durable, owner-bound handoffs for customer-facing AI conversations.
--
-- Customer text stays canonical in telegram_bot_messages. These tables contain routing IDs,
-- bounded reason codes and delivery outcomes only, so observability does not create another
-- copy of the conversation.
CREATE TABLE IF NOT EXISTS ai_handoffs (
    id                    bigserial PRIMARY KEY,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    bot                   text NOT NULL,
    dialog_id             text NOT NULL,
    deal_id               bigint,
    status                text NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending', 'accepted', 'resolved', 'failed')),
    priority              text NOT NULL DEFAULT 'normal'
                          CHECK (priority IN ('normal', 'high', 'urgent')),
    reason_code           text NOT NULL DEFAULT 'other',
    owner_id              text NOT NULL DEFAULT 'iu-group',
    owner_name            text NOT NULL DEFAULT 'Группа «Работа с ИУ»',
    due_at                timestamptz NOT NULL DEFAULT (now() + interval '5 minutes'),
    customer_notified     boolean NOT NULL DEFAULT FALSE,
    destination           text NOT NULL DEFAULT '',
    external_message_id   text NOT NULL DEFAULT '',
    first_dispatched_at   timestamptz,
    last_reminded_at      timestamptz,
    reminder_count        integer NOT NULL DEFAULT 0,
    accepted_at           timestamptz,
    resolved_at           timestamptz,
    resolution_code       text NOT NULL DEFAULT '',
    last_error_code       text NOT NULL DEFAULT '',
    event_count           integer NOT NULL DEFAULT 0,
    meta                  jsonb NOT NULL DEFAULT '{}'::jsonb
);

-- A dialog has one actionable queue item. New messages increase urgency without resetting SLA.
CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_handoffs_open_dialog
    ON ai_handoffs (bot, dialog_id)
    WHERE status IN ('pending', 'accepted');
CREATE INDEX IF NOT EXISTS idx_ai_handoffs_due
    ON ai_handoffs (due_at, id)
    WHERE status IN ('pending', 'accepted');
CREATE INDEX IF NOT EXISTS idx_ai_handoffs_dialog
    ON ai_handoffs (bot, dialog_id, id DESC);

CREATE TABLE IF NOT EXISTS ai_handoff_events (
    id                         bigserial PRIMARY KEY,
    created_at                 timestamptz NOT NULL DEFAULT now(),
    updated_at                 timestamptz NOT NULL DEFAULT now(),
    handoff_id                 bigint NOT NULL REFERENCES ai_handoffs(id) ON DELETE CASCADE,
    event_key                  text NOT NULL UNIQUE,
    source_message_id          bigint,
    reason_code                text NOT NULL DEFAULT 'other',
    customer_delivery_status   text NOT NULL DEFAULT 'pending'
                               CHECK (customer_delivery_status IN
                                      ('pending', 'sending', 'sent', 'failed')),
    customer_delivery_attempts integer NOT NULL DEFAULT 0,
    customer_delivered_at      timestamptz,
    customer_error_code        text NOT NULL DEFAULT '',
    internal_delivery_status   text NOT NULL DEFAULT 'pending'
                               CHECK (internal_delivery_status IN
                                      ('pending', 'sending', 'sent', 'failed')),
    internal_delivery_attempts integer NOT NULL DEFAULT 0,
    internal_delivered_at      timestamptz,
    internal_destination       text NOT NULL DEFAULT '',
    internal_message_id        text NOT NULL DEFAULT '',
    internal_error_code        text NOT NULL DEFAULT '',
    meta                       jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_ai_handoff_events_handoff
    ON ai_handoff_events (handoff_id, id DESC);

