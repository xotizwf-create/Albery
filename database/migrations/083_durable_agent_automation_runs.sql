-- Durable, staged execution for kind='agent' automations.
-- The source automation remains editable; every run keeps an immutable execution snapshot.
CREATE TABLE IF NOT EXISTS agent_automation_runs (
    id                  BIGSERIAL PRIMARY KEY,
    automation_id       INTEGER NOT NULL REFERENCES agent_automations(id) ON DELETE CASCADE,
    agent_slug          TEXT NOT NULL,
    idempotency_key     TEXT NOT NULL UNIQUE,
    trigger_kind        TEXT NOT NULL CHECK (trigger_kind IN ('schedule', 'manual')),
    scheduled_for       TIMESTAMPTZ NOT NULL,
    status              TEXT NOT NULL DEFAULT 'queued' CHECK (status IN (
                            'queued', 'brain_running', 'brain_retry',
                            'delivery_pending', 'delivery_running', 'delivery_retry',
                            'done', 'silent', 'error', 'review'
                        )),
    available_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    lease_until         TIMESTAMPTZ,
    claimed_by          TEXT,
    brain_attempts      INTEGER NOT NULL DEFAULT 0,
    delivery_attempts   INTEGER NOT NULL DEFAULT 0,
    automation_snapshot JSONB NOT NULL,
    result_text         TEXT,
    last_error          TEXT,
    had_mutating_effect BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at          TIMESTAMPTZ,
    brain_finished_at   TIMESTAMPTZ,
    finished_at         TIMESTAMPTZ,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS agent_automation_runs_due_idx
    ON agent_automation_runs (available_at, id)
    WHERE status IN ('queued', 'brain_retry', 'delivery_pending', 'delivery_retry');

CREATE INDEX IF NOT EXISTS agent_automation_runs_lease_idx
    ON agent_automation_runs (lease_until)
    WHERE status IN ('brain_running', 'delivery_running');

CREATE INDEX IF NOT EXISTS agent_automation_runs_automation_idx
    ON agent_automation_runs (automation_id, created_at DESC);

-- The row lock on agent_automations makes the predicate below atomic for Run now.
CREATE UNIQUE INDEX IF NOT EXISTS agent_automation_one_active_manual_idx
    ON agent_automation_runs (automation_id)
    WHERE trigger_kind = 'manual'
      AND status IN ('queued', 'brain_running', 'brain_retry',
                     'delivery_pending', 'delivery_running', 'delivery_retry');

CREATE TABLE IF NOT EXISTS agent_automation_deliveries (
    id              BIGSERIAL PRIMARY KEY,
    run_id          BIGINT NOT NULL REFERENCES agent_automation_runs(id) ON DELETE CASCADE,
    target          TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
                        'pending', 'sending', 'retry', 'delivered', 'error', 'review'
                    )),
    attempts        INTEGER NOT NULL DEFAULT 0,
    available_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    lease_until     TIMESTAMPTZ,
    last_error      TEXT,
    delivered_at    TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, target)
);

CREATE INDEX IF NOT EXISTS agent_automation_deliveries_due_idx
    ON agent_automation_deliveries (available_at, id)
    WHERE status IN ('pending', 'retry');

CREATE TABLE IF NOT EXISTS agent_automation_tool_effects (
    id              BIGSERIAL PRIMARY KEY,
    run_id          BIGINT NOT NULL REFERENCES agent_automation_runs(id) ON DELETE CASCADE,
    fingerprint     TEXT NOT NULL,
    tool_name       TEXT NOT NULL,
    object_key      TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('started', 'done', 'error')),
    result_json     JSONB,
    last_error      TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, fingerprint)
);

COMMENT ON TABLE agent_automation_runs IS
    'Durable stage machine for kind=agent automations; idempotency_key deduplicates triggers.';
COMMENT ON TABLE agent_automation_tool_effects IS
    'Fail-closed per-run ledger for mutating MCP tool calls; never stores credentials.';
