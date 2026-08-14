-- Durable capture-before-ACK queue for Bitrix bot messages and task comments.
-- Additive and idempotent: rollback can disable the worker without dropping evidence.

CREATE TABLE IF NOT EXISTS bitrix_inbound_jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    event_key text NOT NULL UNIQUE,
    event_kind text NOT NULL CHECK (event_kind IN ('chat_message', 'task_comment')),
    scope_key text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'queued' CHECK (status IN (
        'queued', 'preparing', 'brain_running', 'answer_ready', 'sending',
        'delivery_retry', 'sent', 'ignored', 'review', 'failed'
    )),
    batch_id uuid,
    attempts integer NOT NULL DEFAULT 0,
    delivery_attempts integer NOT NULL DEFAULT 0,
    available_at timestamptz NOT NULL DEFAULT now(),
    lease_owner text,
    lease_until timestamptz,
    prepared jsonb,
    answer text,
    turn_status text,
    error_text text,
    provider_message_id text,
    journaled_at timestamptz,
    received_at timestamptz NOT NULL DEFAULT now(),
    brain_started_at timestamptz,
    brain_completed_at timestamptz,
    delivery_started_at timestamptz,
    completed_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_bitrix_inbound_due
    ON bitrix_inbound_jobs (status, available_at, received_at);
CREATE INDEX IF NOT EXISTS idx_bitrix_inbound_scope
    ON bitrix_inbound_jobs (scope_key, status, received_at);
CREATE INDEX IF NOT EXISTS idx_bitrix_inbound_batch
    ON bitrix_inbound_jobs (batch_id) WHERE batch_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_bitrix_inbound_review
    ON bitrix_inbound_jobs (updated_at) WHERE status IN ('review', 'failed');

COMMENT ON TABLE bitrix_inbound_jobs IS
    'Capture-before-ACK Bitrix intake; payload excludes OAuth/application/access/refresh tokens.';
COMMENT ON COLUMN bitrix_inbound_jobs.brain_started_at IS
    'No-replay boundary: an expired brain_running lease moves to review.';
COMMENT ON COLUMN bitrix_inbound_jobs.delivery_started_at IS
    'Provider ambiguity boundary: an expired sending lease moves to review.';
