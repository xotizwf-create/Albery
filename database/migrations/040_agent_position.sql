-- Agent job title (должность), kept in sync with the Bitrix bot's WORK_POSITION.
-- Idempotent.
ALTER TABLE agents ADD COLUMN IF NOT EXISTS position TEXT NOT NULL DEFAULT 'ИИ-агент Albery';
