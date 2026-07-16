-- 055_wb_async_reports.sql
-- Idempotent. Durable state for asynchronous WB warehouse and paid-storage reports,
-- plus an API contract version that invalidates quota blocks left by retired methods.
ALTER TABLE wb_sync_state ADD COLUMN IF NOT EXISTS api_version integer NOT NULL DEFAULT 0;
ALTER TABLE wb_sync_state ADD COLUMN IF NOT EXISTS task_id text;
ALTER TABLE wb_sync_state ADD COLUMN IF NOT EXISTS task_date_from date;
ALTER TABLE wb_sync_state ADD COLUMN IF NOT EXISTS task_date_to date;
ALTER TABLE wb_sync_state ADD COLUMN IF NOT EXISTS task_started_at timestamptz;
ALTER TABLE wb_cards ADD COLUMN IF NOT EXISTS excluded boolean NOT NULL DEFAULT false;
