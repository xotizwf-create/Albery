-- 062_agents_telegram_bridge.sql
-- Idempotent. Telegram как ВТОРОЙ МОСТ для обычного агента, а не отдельная сущность.
--
-- Why: в 061 Telegram-агенты жили в своей таблице telegram_agents — со своим именем и ролью, но
-- без всего, что делает агента агентом в этой системе: набора MCP-инструментов, подключённых
-- инструкций, базы знаний, личных инструкций и автоматизаций. Владелец потребовал, чтобы раздел
-- Telegram работал 1 в 1 как Битрикс, а отличался только мостом. Единственный способ это дать —
-- завести Telegram-агента тем же субагентом в таблице `agents` (у него уже есть свой коннектор
-- agent-<slug>, редактор возможностей и реестр знаний), добавив к нему телеграмный мост.
--
-- Битрикс-мост у субагента — bitrix_bot_id. Телеграмный мост — эти три поля. Агент может иметь
-- любой из них или оба: канал определяется тем, какой мост заполнен.
ALTER TABLE agents ADD COLUMN IF NOT EXISTS telegram_bot_token   text;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS telegram_username    text;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS telegram_bot_user_id bigint;

CREATE INDEX IF NOT EXISTS idx_agents_telegram ON agents (telegram_username)
    WHERE telegram_username IS NOT NULL;
