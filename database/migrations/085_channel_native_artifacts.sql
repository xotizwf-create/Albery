-- Durable native-file delivery for employee Telegram agents.

ALTER TABLE telegram_agent_outbox
    ADD COLUMN IF NOT EXISTS part_no integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS attachment_token text;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_tao_part_no_nonnegative'
    ) THEN
        ALTER TABLE telegram_agent_outbox
            ADD CONSTRAINT ck_tao_part_no_nonnegative CHECK (part_no >= 0);
    END IF;
END
$$;

DROP INDEX IF EXISTS uq_tao_update_reply;
CREATE UNIQUE INDEX IF NOT EXISTS uq_tao_update_part
    ON telegram_agent_outbox (update_id, part_no)
    WHERE update_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_tao_attachment_open
    ON telegram_agent_outbox (attachment_token)
    WHERE attachment_token IS NOT NULL
      AND status NOT IN ('sent','error','cancelled');

ALTER TABLE agent_automation_deliveries
    ADD COLUMN IF NOT EXISTS part_no integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS attachment_token text,
    ADD COLUMN IF NOT EXISTS rendered_text text;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'agent_automation_deliveries_run_id_target_key'
          AND conrelid = 'agent_automation_deliveries'::regclass
    ) THEN
        ALTER TABLE agent_automation_deliveries
            DROP CONSTRAINT agent_automation_deliveries_run_id_target_key;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_aad_part_no_nonnegative'
    ) THEN
        ALTER TABLE agent_automation_deliveries
            ADD CONSTRAINT ck_aad_part_no_nonnegative CHECK (part_no >= 0);
    END IF;
END
$$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_aad_run_target_part
    ON agent_automation_deliveries (run_id, target, part_no);

CREATE INDEX IF NOT EXISTS idx_aad_attachment_open
    ON agent_automation_deliveries (attachment_token)
    WHERE attachment_token IS NOT NULL
      AND status NOT IN ('delivered','error');
