-- 058_crm_lead_forms.sql
-- Idempotent. Self-hosted CRM lead questionnaires (owner Евгений 2026-07-17): a public form
-- at /form/<token>; on submit the Albery app creates a DEAL in the chosen funnel with answers
-- mapped to deal fields (+ optional contact). Bitrix REST cannot create native CRM web-forms,
-- so we host our own; the agent spins one up via the create_crm_lead_form MCP tool.

CREATE TABLE IF NOT EXISTS crm_lead_forms (
    token              text PRIMARY KEY,             -- public slug in the URL
    title              text NOT NULL,
    intro              text NOT NULL DEFAULT '',
    category_id        integer NOT NULL,             -- CRM funnel (deal category)
    stage_id           text NOT NULL,                -- stage code, e.g. C16:S84294148
    pipeline_name      text NOT NULL DEFAULT '',
    assigned_by_id     bigint,                       -- responsible bitrix user id (optional)
    deal_title_tpl     text NOT NULL DEFAULT '',     -- e.g. «Партнёр — {name}» ({key} = answer)
    questions          jsonb NOT NULL,               -- [{key,label,type,required,options,placeholder,field_code,role}]
    success_message    text NOT NULL DEFAULT '',
    create_contact     boolean NOT NULL DEFAULT true,
    is_active          boolean NOT NULL DEFAULT true,
    submissions        integer NOT NULL DEFAULT 0,
    last_submission_at timestamptz,
    created_by         text NOT NULL DEFAULT 'agent',
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS crm_lead_form_submissions (
    id          bigserial PRIMARY KEY,
    form_token  text NOT NULL REFERENCES crm_lead_forms(token) ON DELETE CASCADE,
    deal_id     bigint,
    contact_id  bigint,
    data        jsonb,
    error       text,
    ip          text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_clf_sub_form ON crm_lead_form_submissions (form_token, id);
