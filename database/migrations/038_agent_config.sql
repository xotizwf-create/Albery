-- Per-agent configuration: flexible tool/instruction/skill selection with a fixed
-- (always-on) baseline enforced in code. Fully idempotent (IF NOT EXISTS everywhere).
--
-- tools_customized distinguishes "not configured → full tier set" (the historical
-- default) from "explicitly customized → only agents.tools[] (plus the mandatory
-- baseline) are served by the connector". Without this flag an empty tools[] is
-- ambiguous between "everything" and "nothing".
ALTER TABLE agents ADD COLUMN IF NOT EXISTS tools_customized BOOLEAN NOT NULL DEFAULT FALSE;

-- Opt-in links from an agent to library knowledge: instruction folders
-- (ai_instruction_folders.id, stored as text) or Hermes skills (skill id string).
-- The agent's turn only ever injects the linked items, so an agent literally
-- cannot use knowledge it is not linked to.
CREATE TABLE IF NOT EXISTS agent_knowledge_links (
    agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('instruction', 'skill')),
    ref_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (agent_id, kind, ref_id)
);

CREATE INDEX IF NOT EXISTS idx_agent_knowledge_links_agent ON agent_knowledge_links (agent_id);
