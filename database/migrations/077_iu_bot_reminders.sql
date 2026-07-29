-- Durable 30-minute reminders for the public IU Telegram bot.
CREATE TABLE IF NOT EXISTS iu_bot_reminders (
    conversation_id bigint NOT NULL
        REFERENCES funnel_workspace_conversations(id) ON DELETE CASCADE,
    kind            text NOT NULL CHECK (kind IN ('waiting_question', 'after_answer')),
    anchor_message_id bigint NOT NULL,
    due_at          timestamptz NOT NULL,
    status          text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'leased', 'sent', 'cancelled')),
    locked_by       text,
    locked_until    timestamptz,
    attempts        integer NOT NULL DEFAULT 0,
    last_error      text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (conversation_id, kind)
);

CREATE INDEX IF NOT EXISTS idx_iu_bot_reminders_due
    ON iu_bot_reminders (due_at)
    WHERE status = 'pending';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'iu_form_merges'
           AND column_name = 'bot_notified_at'
    ) THEN
        ALTER TABLE iu_form_merges
            ADD COLUMN bot_notified_at timestamptz;
        -- Старые склейки уже обработаны до появления клиентского уведомления.
        -- Не догоняем их после деплоя: это отправило бы людям запоздалые сообщения.
        UPDATE iu_form_merges SET bot_notified_at = now();
    END IF;
END $$;

ALTER TABLE iu_form_merges
    ADD COLUMN IF NOT EXISTS bot_notify_error text;
