-- 054_wb_sync_state_v2.sql
-- Idempotent. Tick-model sync state: quota blocks (Retry-After -> blocked_until),
-- resumable backfill cursors, done flags. Why: long-lived sleeping backfill processes
-- proved fragile (advisory-lock connection died mid-sleep, timer runs burned quota);
-- the 30-min tick now consults these columns and never calls a blocked method.
ALTER TABLE wb_sync_state ADD COLUMN IF NOT EXISTS blocked_until timestamptz;
ALTER TABLE wb_sync_state ADD COLUMN IF NOT EXISTS cursor_date date;
ALTER TABLE wb_sync_state ADD COLUMN IF NOT EXISTS done boolean NOT NULL DEFAULT false;
