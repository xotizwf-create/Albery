-- Custom operator workspace for inbound customer conversations.
--
-- The transport (Telegram today, other sources later), operator UI and AI runtime
-- deliberately share one durable journal.  The tables below are append-friendly
-- and all externally delivered data has an idempotency key.

CREATE TABLE IF NOT EXISTS funnel_workspace_sources (
    source_key       text PRIMARY KEY,
    source_type      text NOT NULL,
    display_name     text NOT NULL,
    is_enabled       boolean NOT NULL DEFAULT true,
    public_config    jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

INSERT INTO funnel_workspace_sources (source_key, source_type, display_name)
VALUES ('telegram', 'telegram_business', 'Telegram')
ON CONFLICT (source_key) DO NOTHING;

CREATE TABLE IF NOT EXISTS funnel_workspace_conversations (
    id                       bigserial PRIMARY KEY,
    source_key               text NOT NULL REFERENCES funnel_workspace_sources(source_key),
    external_chat_id         text NOT NULL,
    external_user_id         bigint,
    business_connection_id   text NOT NULL DEFAULT '',
    username                 text,
    display_name             text,
    avatar_url               text,
    deal_id                  bigint,
    funnel_id                integer,
    stage_id                 text,
    status                   text NOT NULL DEFAULT 'new'
                             CHECK (status IN ('new', 'open', 'waiting', 'closed', 'spam', 'expired')),
    control_mode             text NOT NULL DEFAULT 'ai'
                             CHECK (control_mode IN ('ai', 'human', 'paused')),
    resume_at                timestamptz,
    assigned_to              text,
    unread_count             integer NOT NULL DEFAULT 0 CHECK (unread_count >= 0),
    last_read_message_id     bigint NOT NULL DEFAULT 0 CHECK (last_read_message_id >= 0),
    state_version            bigint NOT NULL DEFAULT 1 CHECK (state_version > 0),
    reply_deadline_at        timestamptz,
    last_message_id          bigint,
    last_message_at          timestamptz,
    last_message_text        text,
    last_author_type         text
                             CHECK (last_author_type IS NULL OR last_author_type IN ('client', 'agent', 'operator', 'system')),
    metadata                 jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at               timestamptz NOT NULL DEFAULT now(),
    updated_at               timestamptz NOT NULL DEFAULT now(),
    closed_at                timestamptz,
    UNIQUE (source_key, business_connection_id, external_chat_id)
);

ALTER TABLE funnel_workspace_conversations
    ADD COLUMN IF NOT EXISTS last_read_message_id bigint NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_fwc_queue
    ON funnel_workspace_conversations (status, last_message_at DESC NULLS LAST, id DESC);
CREATE INDEX IF NOT EXISTS idx_fwc_source
    ON funnel_workspace_conversations (source_key, last_message_at DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_fwc_external_user
    ON funnel_workspace_conversations (source_key, external_user_id);
CREATE INDEX IF NOT EXISTS idx_fwc_control_resume
    ON funnel_workspace_conversations (control_mode, resume_at)
    WHERE resume_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_fwc_reply_deadline
    ON funnel_workspace_conversations (reply_deadline_at)
    WHERE reply_deadline_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_fwc_deal
    ON funnel_workspace_conversations (deal_id)
    WHERE deal_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_fwc_last_message
    ON funnel_workspace_conversations (last_message_id)
    WHERE last_message_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS funnel_workspace_messages (
    id                       bigserial PRIMARY KEY,
    conversation_id          bigint NOT NULL
                             REFERENCES funnel_workspace_conversations(id) ON DELETE CASCADE,
    external_message_id      text,
    provider_update_id       bigint,
    provider_message_id      text,
    idempotency_key          text,
    author_type              text NOT NULL
                             CHECK (author_type IN ('client', 'agent', 'operator', 'system')),
    author_name              text,
    direction                text NOT NULL
                             CHECK (direction IN ('inbound', 'outbound', 'system')),
    text                     text NOT NULL DEFAULT '',
    delivery_status          text NOT NULL DEFAULT 'sent'
                             CHECK (delivery_status IN ('pending', 'sent', 'failed', 'unknown', 'cancelled')),
    error_code               text,
    error_detail             text,
    metadata                 jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurred_at              timestamptz NOT NULL DEFAULT now(),
    created_at               timestamptz NOT NULL DEFAULT now(),
    sent_at                  timestamptz
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_fwm_external_message
    ON funnel_workspace_messages (conversation_id, external_message_id)
    WHERE external_message_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_fwm_idempotency
    ON funnel_workspace_messages (idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_fwm_conversation
    ON funnel_workspace_messages (conversation_id, id);
CREATE INDEX IF NOT EXISTS idx_fwm_occurred
    ON funnel_workspace_messages (occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_fwm_author
    ON funnel_workspace_messages (author_type, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_fwm_text_trgm
    ON funnel_workspace_messages
    USING gin (text gin_trgm_ops);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname = 'fk_fwc_last_message'
           AND conrelid = 'funnel_workspace_conversations'::regclass
    ) THEN
        ALTER TABLE funnel_workspace_conversations
            ADD CONSTRAINT fk_fwc_last_message
            FOREIGN KEY (last_message_id)
            REFERENCES funnel_workspace_messages(id) ON DELETE SET NULL;
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS funnel_workspace_control_events (
    id                       bigserial PRIMARY KEY,
    conversation_id          bigint NOT NULL
                             REFERENCES funnel_workspace_conversations(id) ON DELETE CASCADE,
    from_mode                text
                             CHECK (from_mode IS NULL OR from_mode IN ('ai', 'human', 'paused')),
    to_mode                  text NOT NULL
                             CHECK (to_mode IN ('ai', 'human', 'paused')),
    actor_type               text NOT NULL
                             CHECK (actor_type IN ('agent', 'operator', 'system')),
    actor_name               text,
    reason                   text,
    from_version             bigint,
    to_version               bigint NOT NULL,
    created_at               timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_fwce_conversation
    ON funnel_workspace_control_events (conversation_id, id);

-- Raw updates are captured before any business logic.  A lease can be reclaimed
-- after a worker crash; the unique provider update id makes polling replays safe.
CREATE TABLE IF NOT EXISTS funnel_workspace_updates (
    id                       bigserial PRIMARY KEY,
    source_key               text NOT NULL REFERENCES funnel_workspace_sources(source_key),
    external_update_id       text NOT NULL,
    payload                  jsonb NOT NULL,
    processing_status        text NOT NULL DEFAULT 'pending'
                             CHECK (processing_status IN ('pending', 'processing', 'retry', 'done', 'dead_letter')),
    attempts                 integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at             timestamptz NOT NULL DEFAULT now(),
    locked_at                timestamptz,
    locked_until             timestamptz,
    locked_by                text,
    last_error               text,
    conversation_id          bigint
                             REFERENCES funnel_workspace_conversations(id) ON DELETE SET NULL,
    message_id               bigint
                             REFERENCES funnel_workspace_messages(id) ON DELETE SET NULL,
    received_at              timestamptz NOT NULL DEFAULT now(),
    completed_at             timestamptz,
    updated_at               timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_key, external_update_id)
);

CREATE INDEX IF NOT EXISTS idx_fwu_claim
    ON funnel_workspace_updates (processing_status, available_at, id);
CREATE INDEX IF NOT EXISTS idx_fwu_business_head
    ON funnel_workspace_updates (source_key, id)
    WHERE processing_status IN ('pending', 'processing', 'retry')
      AND NOT (payload ? 'message');
CREATE INDEX IF NOT EXISTS idx_fwu_bot_head
    ON funnel_workspace_updates (source_key, id)
    WHERE processing_status IN ('pending', 'processing', 'retry')
      AND (payload ? 'message');
CREATE INDEX IF NOT EXISTS idx_fwu_expired_lease
    ON funnel_workspace_updates (locked_until)
    WHERE processing_status = 'processing';
CREATE INDEX IF NOT EXISTS idx_fwu_conversation
    ON funnel_workspace_updates (conversation_id)
    WHERE conversation_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_fwu_message
    ON funnel_workspace_updates (message_id)
    WHERE message_id IS NOT NULL;

-- Debounced AI work is separate from raw update processing.  A pending job is
-- coalesced per conversation; a new client message invalidates a leased job by
-- bumping state_version and creates the next pending job.
CREATE TABLE IF NOT EXISTS funnel_workspace_ai_jobs (
    id                       bigserial PRIMARY KEY,
    conversation_id          bigint NOT NULL
                             REFERENCES funnel_workspace_conversations(id) ON DELETE CASCADE,
    trigger_message_id       bigint
                             REFERENCES funnel_workspace_messages(id) ON DELETE SET NULL,
    expected_version         bigint NOT NULL,
    processing_status        text NOT NULL DEFAULT 'pending'
                             CHECK (processing_status IN ('pending', 'leased', 'done', 'failed', 'cancelled')),
    attempts                 integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at             timestamptz NOT NULL DEFAULT now(),
    locked_at                timestamptz,
    locked_until             timestamptz,
    locked_by                text,
    cancel_requested         boolean NOT NULL DEFAULT false,
    last_error               text,
    outbox_id                bigint,
    created_at               timestamptz NOT NULL DEFAULT now(),
    updated_at               timestamptz NOT NULL DEFAULT now(),
    completed_at             timestamptz
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_fwaj_pending_conversation
    ON funnel_workspace_ai_jobs (conversation_id)
    WHERE processing_status = 'pending';
CREATE INDEX IF NOT EXISTS idx_fwaj_claim
    ON funnel_workspace_ai_jobs (processing_status, available_at, id);
CREATE INDEX IF NOT EXISTS idx_fwaj_expired_lease
    ON funnel_workspace_ai_jobs (locked_until)
    WHERE processing_status = 'leased';
CREATE INDEX IF NOT EXISTS idx_fwaj_conversation
    ON funnel_workspace_ai_jobs (conversation_id, id);
CREATE INDEX IF NOT EXISTS idx_fwaj_trigger_message
    ON funnel_workspace_ai_jobs (trigger_message_id)
    WHERE trigger_message_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_fwaj_outbox
    ON funnel_workspace_ai_jobs (outbox_id)
    WHERE outbox_id IS NOT NULL;

-- A durable outbox prevents a committed UI/AI answer from disappearing between
-- the database transaction and Telegram.  An expired reservation is retryable;
-- once the worker enters `sending`, an expired lease becomes `unknown`: Telegram
-- has no client idempotency key and retrying could duplicate a visible message.
CREATE TABLE IF NOT EXISTS funnel_workspace_outbox (
    id                       bigserial PRIMARY KEY,
    conversation_id          bigint NOT NULL
                             REFERENCES funnel_workspace_conversations(id) ON DELETE CASCADE,
    message_id               bigint NOT NULL UNIQUE
                             REFERENCES funnel_workspace_messages(id) ON DELETE CASCADE,
    source_key               text NOT NULL REFERENCES funnel_workspace_sources(source_key),
    external_chat_id         text NOT NULL,
    business_connection_id   text NOT NULL DEFAULT '',
    author_type              text NOT NULL CHECK (author_type IN ('agent', 'operator')),
    text                     text NOT NULL,
    payload                  jsonb NOT NULL DEFAULT '{}'::jsonb,
    idempotency_key          text NOT NULL UNIQUE,
    conversation_version     bigint NOT NULL,
    delivery_status          text NOT NULL DEFAULT 'pending'
                             CHECK (delivery_status IN ('pending', 'leased', 'sending', 'sent', 'failed', 'unknown', 'cancelled')),
    attempts                 integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at             timestamptz NOT NULL DEFAULT now(),
    locked_at                timestamptz,
    locked_until             timestamptz,
    locked_by                text,
    cancel_requested         boolean NOT NULL DEFAULT false,
    provider_message_id      text,
    last_error               text,
    created_at               timestamptz NOT NULL DEFAULT now(),
    updated_at               timestamptz NOT NULL DEFAULT now(),
    sent_at                  timestamptz
);

-- Upgrade an early workspace installation whose generated delivery-status
-- CHECK predates the pre-call/sending boundary.  A fresh table already has the
-- new definition, so repeated migration runs do not churn the constraint.
DO $$
DECLARE
    old_check record;
BEGIN
    FOR old_check IN
        SELECT conname
          FROM pg_constraint
         WHERE conrelid = 'funnel_workspace_outbox'::regclass
           AND contype = 'c'
           AND lower(pg_get_constraintdef(oid)) LIKE '%delivery_status%'
           AND lower(pg_get_constraintdef(oid)) NOT LIKE '%sending%'
           AND lower(pg_get_constraintdef(oid)) LIKE '%pending%'
           AND lower(pg_get_constraintdef(oid)) LIKE '%leased%'
           AND lower(pg_get_constraintdef(oid)) LIKE '%sent%'
           AND lower(pg_get_constraintdef(oid)) LIKE '%failed%'
           AND lower(pg_get_constraintdef(oid)) LIKE '%unknown%'
           AND lower(pg_get_constraintdef(oid)) LIKE '%cancelled%'
    LOOP
        EXECUTE format(
            'ALTER TABLE funnel_workspace_outbox DROP CONSTRAINT %I',
            old_check.conname
        );
        EXECUTE format(
            'ALTER TABLE funnel_workspace_outbox ADD CONSTRAINT %I '
            || 'CHECK (delivery_status IN '
            || '(''pending'', ''leased'', ''sending'', ''sent'', ''failed'', '
            || '''unknown'', ''cancelled'')) NOT VALID',
            old_check.conname
        );
        EXECUTE format(
            'ALTER TABLE funnel_workspace_outbox VALIDATE CONSTRAINT %I',
            old_check.conname
        );
    END LOOP;
END
$$;

CREATE INDEX IF NOT EXISTS idx_fwo_claim
    ON funnel_workspace_outbox (delivery_status, available_at, id);
CREATE INDEX IF NOT EXISTS idx_fwo_conversation
    ON funnel_workspace_outbox (conversation_id, id);
-- ``leased`` means a worker has reserved the row but has not crossed the provider
-- side-effect boundary yet.  ``sending`` is written immediately before the Bot
-- API call.  This distinction makes a crash before the call retryable while a
-- crash during the call remains explicitly ambiguous.
CREATE INDEX IF NOT EXISTS idx_fwo_expired_lease
    ON funnel_workspace_outbox (locked_until)
    WHERE delivery_status IN ('leased', 'sending');

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname = 'fk_fwaj_outbox'
           AND conrelid = 'funnel_workspace_ai_jobs'::regclass
    ) THEN
        ALTER TABLE funnel_workspace_ai_jobs
            ADD CONSTRAINT fk_fwaj_outbox
            FOREIGN KEY (outbox_id)
            REFERENCES funnel_workspace_outbox(id) ON DELETE SET NULL;
    END IF;
END
$$;

-- Follow-up side effects never run inline in Telegram update routing.  The same
-- bounded queue covers CRM linking, confirmed-delivery local facts/escalation,
-- and post-delivery stage changes.  A retried stage action first reads the
-- current Bitrix stage before issuing another idempotent desired-state SET.
CREATE TABLE IF NOT EXISTS funnel_workspace_crm_actions (
    id                       bigserial PRIMARY KEY,
    conversation_id          bigint NOT NULL
                             REFERENCES funnel_workspace_conversations(id) ON DELETE CASCADE,
    message_id               bigint NOT NULL
                             REFERENCES funnel_workspace_messages(id) ON DELETE CASCADE,
    outbox_id                bigint
                             REFERENCES funnel_workspace_outbox(id) ON DELETE CASCADE,
    action_type              text NOT NULL
                             CHECK (action_type IN ('ensure_deal', 'delivery_effects', 'move_stage')),
    target_stage             text,
    payload                  jsonb NOT NULL DEFAULT '{}'::jsonb,
    idempotency_key          text NOT NULL UNIQUE,
    processing_status        text NOT NULL DEFAULT 'pending'
                             CHECK (processing_status IN ('pending', 'leased', 'retry', 'done', 'dead_letter')),
    attempts                 integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts             integer NOT NULL DEFAULT 8
                             CHECK (max_attempts BETWEEN 1 AND 20),
    available_at             timestamptz NOT NULL DEFAULT now(),
    locked_at                timestamptz,
    locked_until             timestamptz,
    locked_by                text,
    last_error               text,
    result                   jsonb,
    created_at               timestamptz NOT NULL DEFAULT now(),
    updated_at               timestamptz NOT NULL DEFAULT now(),
    completed_at             timestamptz,
    UNIQUE (outbox_id, action_type),
    CHECK (
        (
            action_type = 'ensure_deal'
            AND outbox_id IS NULL
            AND target_stage IS NULL
        )
        OR (
            action_type = 'delivery_effects'
            AND outbox_id IS NOT NULL
            AND target_stage IS NULL
        )
        OR (
            action_type = 'move_stage'
            AND outbox_id IS NOT NULL
            AND target_stage IS NOT NULL
            AND char_length(btrim(target_stage)) BETWEEN 1 AND 200
        )
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_fwca_ensure_conversation
    ON funnel_workspace_crm_actions (conversation_id, action_type)
    WHERE action_type = 'ensure_deal';
CREATE INDEX IF NOT EXISTS idx_fwca_claim
    ON funnel_workspace_crm_actions (processing_status, available_at, id)
    WHERE processing_status IN ('pending', 'retry');
CREATE INDEX IF NOT EXISTS idx_fwca_expired_lease
    ON funnel_workspace_crm_actions (locked_until)
    WHERE processing_status = 'leased';
CREATE INDEX IF NOT EXISTS idx_fwca_conversation
    ON funnel_workspace_crm_actions (conversation_id, id);
CREATE INDEX IF NOT EXISTS idx_fwca_message
    ON funnel_workspace_crm_actions (message_id)
    WHERE message_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS funnel_workspace_settings (
    setting_key              text PRIMARY KEY,
    setting_value            jsonb NOT NULL,
    updated_at               timestamptz NOT NULL DEFAULT now()
);

INSERT INTO funnel_workspace_settings (setting_key, setting_value)
VALUES
    ('human_lease_seconds', '120'::jsonb),
    ('retention_days', '90'::jsonb)
ON CONFLICT (setting_key) DO NOTHING;
