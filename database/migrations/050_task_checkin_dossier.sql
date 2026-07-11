-- 050: Daily task check-in (12:00 МСК) + per-employee agent dossier (владелец 2026-07-11).
-- The check-in pipeline scans open HUMAN tasks once a day, picks the ones the agent can
-- genuinely accelerate (deterministic filters -> cheap classifier -> Codex offer), posts offer
-- comments, refreshes the dossiers and DMs the people it offered to. The dossier is the agent's
-- working memory about each employee: does the person use the agent, which of their tasks are
-- automatable, when did we last offer/DM. Managed by the check-in + the get/update_employee_dossier
-- MCP tools. Idempotent (IF NOT EXISTS / ON CONFLICT DO NOTHING). Registered in ALWAYS_APPLY.

CREATE TABLE IF NOT EXISTS employee_agent_dossier (
    bitrix_user_id  integer PRIMARY KEY,
    full_name       text,
    agent_access    boolean,            -- refreshed at each check-in
    turns_30d       integer DEFAULT 0,  -- agent turns in the last 30 days (chats + in-task)
    task_turns_30d  integer DEFAULT 0,  -- of them: inside tasks
    last_agent_use  timestamptz,
    offers_made     integer DEFAULT 0,
    offers_engaged  integer DEFAULT 0,  -- offered tasks where the person actually replied
    offers_declined integer DEFAULT 0,
    last_offer_at   timestamptz,
    automatable     text,               -- rolling observations: which of their tasks the agent can do
    notes           text,               -- free-form notes (update_employee_dossier)
    first_dm_at     timestamptz,
    last_dm_at      timestamptz,
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- One row per calendar day = the atomic claim that a check-in ran (survives restarts,
-- blocks double runs) + a small run report for the owner/agent.
CREATE TABLE IF NOT EXISTS task_checkin_runs (
    run_date       date PRIMARY KEY,
    started_at     timestamptz NOT NULL DEFAULT now(),
    finished_at    timestamptz,
    scanned        integer DEFAULT 0,
    passed_filters integer DEFAULT 0,
    offers_posted  integer DEFAULT 0,
    dms_sent       integer DEFAULT 0,
    details        jsonb
);

-- Visible in the Automations tab as a read-only system row (executed by task_checkin.py).
INSERT INTO agent_automations (agent_slug, name, description, schedule, prompt, deliver_to,
                               kind, created_by, creator_label)
VALUES ('main', 'Ежедневный обход задач и досье',
        'Раз в день агент отбирает открытые задачи, где реально может ускорить работу, пишет в них предложения помощи, обновляет досье сотрудников и присылает людям личные сообщения.',
        '0 12 * * 1-5',
        'Системный конвейер task_checkin.py: фильтры → классификатор → Codex-офферы → досье → ЛС. Только по рабочим дням (агент не пишет сотрудникам в выходные). Управление через env B24_TASK_CHECKIN.',
        '', 'system', 'owner', 'системная · приложение')
ON CONFLICT (agent_slug, name) DO NOTHING;
