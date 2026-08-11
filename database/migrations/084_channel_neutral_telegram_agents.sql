-- 084_channel_neutral_telegram_agents.sql
-- Durable employee Telegram transport, stable actor mapping and typed automation destinations.

ALTER TABLE telegram_bot_access
    ADD COLUMN IF NOT EXISTS bitrix_user_id bigint;

CREATE UNIQUE INDEX IF NOT EXISTS uq_tba_bot_tg_user
    ON telegram_bot_access (bot, tg_user_id)
    WHERE is_active AND tg_user_id IS NOT NULL;

ALTER TABLE agent_automations
    ADD COLUMN IF NOT EXISTS delivery_channel text NOT NULL DEFAULT 'bitrix',
    ADD COLUMN IF NOT EXISTS delivery_profile text,
    ADD COLUMN IF NOT EXISTS delivery_conversation_id text;

UPDATE agent_automations
   SET delivery_conversation_id = NULLIF(btrim(deliver_to), '')
 WHERE delivery_conversation_id IS NULL
   AND NULLIF(btrim(deliver_to), '') IS NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_agent_automation_delivery_channel') THEN
        ALTER TABLE agent_automations
            ADD CONSTRAINT ck_agent_automation_delivery_channel
            CHECK (delivery_channel IN ('bitrix', 'telegram'));
    END IF;
END
$$;

ALTER TABLE agent_automation_deliveries
    ADD COLUMN IF NOT EXISTS channel text NOT NULL DEFAULT 'bitrix',
    ADD COLUMN IF NOT EXISTS profile_slug text;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_agent_automation_delivery_channel_row') THEN
        ALTER TABLE agent_automation_deliveries
            ADD CONSTRAINT ck_agent_automation_delivery_channel_row
            CHECK (channel IN ('bitrix', 'telegram'));
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS telegram_agent_updates (
    id                  bigserial PRIMARY KEY,
    agent_slug          text NOT NULL REFERENCES agents(slug) ON DELETE CASCADE,
    provider_update_id  bigint NOT NULL,
    payload             jsonb NOT NULL,
    status              text NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','brain_running','done','retry','review','ignored')),
    attempts            integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at        timestamptz NOT NULL DEFAULT now(),
    locked_at           timestamptz,
    locked_until        timestamptz,
    locked_by           text,
    last_error          text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    completed_at        timestamptz,
    UNIQUE (agent_slug, provider_update_id)
);

CREATE INDEX IF NOT EXISTS idx_tau_claim
    ON telegram_agent_updates (status, available_at, id)
    WHERE status IN ('pending','retry');
CREATE INDEX IF NOT EXISTS idx_tau_expired
    ON telegram_agent_updates (locked_until)
    WHERE status = 'brain_running';

CREATE TABLE IF NOT EXISTS telegram_agent_offsets (
    agent_slug      text PRIMARY KEY REFERENCES agents(slug) ON DELETE CASCADE,
    next_offset     bigint NOT NULL DEFAULT 0,
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS telegram_agent_outbox (
    id                  bigserial PRIMARY KEY,
    agent_slug          text NOT NULL REFERENCES agents(slug) ON DELETE CASCADE,
    update_id           bigint REFERENCES telegram_agent_updates(id) ON DELETE SET NULL,
    chat_id             text NOT NULL,
    text                text NOT NULL,
    idempotency_key     text NOT NULL UNIQUE,
    status              text NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','leased','sending','sent','retry','review','error','cancelled')),
    attempts            integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at        timestamptz NOT NULL DEFAULT now(),
    locked_at           timestamptz,
    locked_until        timestamptz,
    locked_by           text,
    provider_message_id text,
    last_error          text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    sent_at             timestamptz
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_tao_update_reply
    ON telegram_agent_outbox (update_id)
    WHERE update_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tao_claim
    ON telegram_agent_outbox (status, available_at, id)
    WHERE status IN ('pending','retry');
CREATE INDEX IF NOT EXISTS idx_tao_expired
    ON telegram_agent_outbox (locked_until)
    WHERE status IN ('leased','sending');
