-- 046: Recurring tasks are fired by the agent's OWN scheduler, not by Bitrix REPLICATE templates.
-- The portal has no paid subscription, and Bitrix's automatic task replication is a paid feature —
-- so a REPLICATE template (migration 045) is created but never spawns tasks ("не создаётся нормально").
-- Instead the Albery app keeps the schedule here and creates a plain one-off task on time (plain
-- tasks via REST work without a subscription). This migration extends the existing registry with the
-- scheduling state + a full spec to reproduce the one-off task; it does NOT touch or drop anything.
-- Strictly additive & idempotent (ADD COLUMN IF NOT EXISTS / CREATE INDEX IF NOT EXISTS).
-- Registered in ALWAYS_APPLY_MIGRATIONS.

ALTER TABLE bitrix_recurring_tasks ADD COLUMN IF NOT EXISTS next_run_at     timestamptz;   -- when the next instance is created (MSK-based, stored UTC)
ALTER TABLE bitrix_recurring_tasks ADD COLUMN IF NOT EXISTS last_created_at timestamptz;   -- last time the scheduler created an instance
ALTER TABLE bitrix_recurring_tasks ADD COLUMN IF NOT EXISTS last_task_id    bigint;        -- Bitrix task id of the last created instance
ALTER TABLE bitrix_recurring_tasks ADD COLUMN IF NOT EXISTS last_error      text;          -- last scheduler error (best-effort, for the UI/agent)
ALTER TABLE bitrix_recurring_tasks ADD COLUMN IF NOT EXISTS result_criteria text;          -- required result criterion (also embedded in description)
ALTER TABLE bitrix_recurring_tasks ADD COLUMN IF NOT EXISTS priority        text;          -- 'normal' | 'high'
ALTER TABLE bitrix_recurring_tasks ADD COLUMN IF NOT EXISTS spec            jsonb;          -- full task spec to recreate the one-off instance
ALTER TABLE bitrix_recurring_tasks ADD COLUMN IF NOT EXISTS source          text NOT NULL DEFAULT 'agent_scheduler';  -- 'agent_scheduler' | legacy 'bitrix_template'

-- Due-check index: the scheduler tick scans active rows whose next_run_at has passed.
CREATE INDEX IF NOT EXISTS idx_recurring_tasks_next_run
    ON bitrix_recurring_tasks (next_run_at) WHERE active;
