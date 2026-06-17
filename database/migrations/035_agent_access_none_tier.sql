-- 035: allow an explicit 'none' tier (no access) in agent_access. Default (no row) is now 'faq'
-- (knowledge base), so "Отсутствие доступа" must be storable as an explicit deny. Idempotent;
-- in ensure_postgres ALWAYS_APPLY_MIGRATIONS.

ALTER TABLE agent_access DROP CONSTRAINT IF EXISTS agent_access_tier_check;
ALTER TABLE agent_access ADD CONSTRAINT agent_access_tier_check CHECK (tier IN ('none', 'faq', 'ops', 'admin'));
