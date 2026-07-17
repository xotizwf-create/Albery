-- 057_system_automation_keys.sql
-- Idempotent. Machine keys for kind='system' automation rows: each row names its real
-- executor (hermes cron job / cron.d line / in-app thread), so the app can write runs
-- back into the registry and edit the executor through the UI instead of showing a
-- dead read-only витрина (owner 2026-07-17).
--   hermes:<job-name>          — Hermes cron on this box (hermes cron list/edit/pause)
--   crond:<file>:<mode>        — /etc/cron.d/<file>, line calling the script in <mode>
--   app:<name>                 — thread inside albery.service (reads its row itself)

ALTER TABLE agent_automations ADD COLUMN IF NOT EXISTS system_key text;

UPDATE agent_automations SET system_key = 'hermes:zoom-to-tasks'
 WHERE kind = 'system' AND system_key IS NULL AND creator_label LIKE '%zoom-to-tasks%';
UPDATE agent_automations SET system_key = 'hermes:owner-daily'
 WHERE kind = 'system' AND system_key IS NULL AND creator_label LIKE '%owner-daily%';
UPDATE agent_automations SET system_key = 'hermes:owner-weekly'
 WHERE kind = 'system' AND system_key IS NULL AND creator_label LIKE '%owner-weekly%';
UPDATE agent_automations SET system_key = 'hermes:leader-digest'
 WHERE kind = 'system' AND system_key IS NULL AND creator_label LIKE '%leader-digest%';
UPDATE agent_automations SET system_key = 'crond:albery-funnel-control:check'
 WHERE kind = 'system' AND system_key IS NULL AND creator_label LIKE '%funnel-check%';
UPDATE agent_automations SET system_key = 'crond:albery-funnel-control:summary'
 WHERE kind = 'system' AND system_key IS NULL AND creator_label LIKE '%funnel-summary%';
UPDATE agent_automations SET system_key = 'crond:albery-novinki-watch:main'
 WHERE kind = 'system' AND system_key IS NULL AND creator_label LIKE '%novinki-watch%';
UPDATE agent_automations SET system_key = 'app:task-checkin'
 WHERE kind = 'system' AND system_key IS NULL AND name LIKE 'Ежедневный обход задач%';

CREATE UNIQUE INDEX IF NOT EXISTS agent_automations_system_key_uniq
    ON agent_automations (system_key) WHERE system_key IS NOT NULL;
