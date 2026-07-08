-- 045: Registry of recurring (regular) Bitrix tasks created through the agent.
-- Bitrix stores recurring tasks as REPLICATE task templates (task.template.add). The REST API has
-- NO list method for templates (task.template.list = METHOD_NOT_FOUND on this portal), so to let
-- the agent "view all recurring tasks per person" we keep our own registry here. Each row mirrors
-- one Bitrix template we created; list_recurring_tasks reads this + enriches with the live
-- next-execution time via task.template.get.
-- Idempotent (IF NOT EXISTS). Registered in ALWAYS_APPLY_MIGRATIONS.

CREATE TABLE IF NOT EXISTS bitrix_recurring_tasks (
    id                     bigserial PRIMARY KEY,
    bitrix_template_id     bigint UNIQUE,
    title                  text NOT NULL,
    description            text,
    responsible_bitrix_id  integer,
    responsible_name       text,
    creator_bitrix_id      integer,
    period                 text NOT NULL,          -- 'daily' | 'weekly' | 'monthly'
    interval_every         integer NOT NULL DEFAULT 1,
    weekdays               integer[],              -- Bitrix nums Mon=1..Sun=7 (weekly)
    day_of_month           integer,                -- (monthly)
    create_time            text,                   -- 'HH:MM' — when each instance is created
    deadline_after_seconds integer,                -- deadline offset from creation
    deadline_desc          text,                   -- human note, e.g. '19:00 того же дня'
    schedule_desc          text,                   -- human-readable schedule
    until_date             text,                   -- end date or NULL = endless
    active                 boolean NOT NULL DEFAULT true,
    created_at             timestamptz NOT NULL DEFAULT now(),
    updated_at             timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_recurring_tasks_responsible
    ON bitrix_recurring_tasks (responsible_bitrix_id) WHERE active;
CREATE INDEX IF NOT EXISTS idx_recurring_tasks_created
    ON bitrix_recurring_tasks (created_at DESC);
