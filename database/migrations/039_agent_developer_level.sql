-- Access levels become presets, not hard caps: an agent's tools are now selected from
-- the FULL registry. The level (tier) only decides the default preset and, critically,
-- whether owner-only/dangerous tools may be enabled at all — allowed only for 'developer'.
-- Idempotent: drop-and-recreate the CHECK so re-runs converge.
ALTER TABLE agents DROP CONSTRAINT IF EXISTS agents_tier_check;
ALTER TABLE agents ADD CONSTRAINT agents_tier_check CHECK (tier IN ('faq', 'ops', 'developer'));
