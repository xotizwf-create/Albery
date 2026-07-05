-- Per-agent scheduled automations (Центр Агента → Агенты → вкладка «Автоматизации»).
-- kind='agent'  — executed in-app by the scheduler thread (agent_automations.py):
--                 one hermes turn on the agent's own connector, result delivered to a
--                 Bitrix dialog as that agent's bot. Created by the owner in the UI or
--                 by the agent itself from chat (schedule_my_automation).
-- kind='system' — read-only rows mirroring the legacy Hermes cron jobs on the box
--                 (hermes cron list); managed outside the app, never executed by it.
CREATE TABLE IF NOT EXISTS agent_automations (
    id            SERIAL PRIMARY KEY,
    agent_slug    TEXT NOT NULL,
    name          TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    schedule      TEXT NOT NULL,                    -- 5-field cron, MSK
    prompt        TEXT NOT NULL DEFAULT '',         -- the task for the agent's turn ('' for system rows)
    deliver_to    TEXT NOT NULL DEFAULT '',         -- Bitrix dialog_id ('' = notifications chat)
    kind          TEXT NOT NULL DEFAULT 'agent' CHECK (kind IN ('agent', 'system')),
    created_by    TEXT NOT NULL DEFAULT 'owner' CHECK (created_by IN ('owner', 'self')),
    creator_label TEXT NOT NULL DEFAULT '',
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    last_run_at   TIMESTAMPTZ,
    last_status   TEXT,                             -- ok | error | silent | running
    last_result   TEXT,
    last_error    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (agent_slug, name)
);

CREATE INDEX IF NOT EXISTS agent_automations_slug_idx ON agent_automations (agent_slug);

-- Seed: the four Hermes cron automations that already run for the main agent
-- (live `hermes cron list` on the box, 2026-07-05).
INSERT INTO agent_automations (agent_slug, name, description, schedule, kind, creator_label)
VALUES
    ('main', 'Разбор Zoom-созвонов',
     'Находит новые Zoom-созвоны, готовит полный аналитический отчёт по контракту и предлагает владельцу задачи в Битрикс (сводка в Telegram).',
     '*/5 * * * *', 'system', 'Hermes cron · zoom-to-tasks'),
    ('main', 'Ежедневный отчёт собственнику',
     'Отчёт по дню + адресные рекомендации руководителям; согласование в Telegram, отправка в Битрикс.',
     '0 18 * * 0-4,6', 'system', 'Hermes cron · owner-daily'),
    ('main', 'Недельный отчёт собственнику',
     'Итоги недели для собственника по контракту owner_weekly.',
     '0 18 * * 5', 'system', 'Hermes cron · owner-weekly'),
    ('main', 'Дайджест по руководителям',
     'Еженедельный дайджест по руководителям.',
     '0 19 * * 3', 'system', 'Hermes cron · leader-digest')
ON CONFLICT (agent_slug, name) DO NOTHING;
