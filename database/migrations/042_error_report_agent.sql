-- 042: attribute "Сообщить об ошибке" reports to the bot they were filed on.
-- NULL = the universal (main) agent — same convention as bitrix_bot_interactions.agent_slug,
-- so the per-agent monitoring filter treats both tables identically.
ALTER TABLE bitrix_error_reports ADD COLUMN IF NOT EXISTS agent_slug TEXT;
