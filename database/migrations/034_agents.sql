-- Subagents: each row is a separate Bitrix bot (registered via the same local
-- application) with its own role prompt, tool tier, member allowlist and a
-- personal instruction store the agent itself can extend (self-learning).

CREATE TABLE IF NOT EXISTS agents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    role_prompt TEXT NOT NULL DEFAULT '',
    tier TEXT NOT NULL DEFAULT 'faq' CHECK (tier IN ('faq', 'ops')),
    tools TEXT[] NOT NULL DEFAULT '{}',
    bitrix_bot_id BIGINT,
    mcp_token TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    color TEXT NOT NULL DEFAULT 'GREEN',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_members (
    agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    bitrix_user_id BIGINT NOT NULL,
    PRIMARY KEY (agent_id, bitrix_user_id)
);

CREATE TABLE IF NOT EXISTS agent_instructions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'owner' CHECK (source IN ('owner', 'self')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (agent_id, name)
);

-- Attribute every bot turn to the agent that handled it (NULL = the main agent).
ALTER TABLE bitrix_bot_interactions ADD COLUMN IF NOT EXISTS agent_slug TEXT;
CREATE INDEX IF NOT EXISTS idx_bbi_agent ON bitrix_bot_interactions (agent_slug);
