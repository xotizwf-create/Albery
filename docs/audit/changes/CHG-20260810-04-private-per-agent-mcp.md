# CHG-20260810-04: Private per-agent MCP migration

- Status: implemented_local
- Date opened: 2026-08-10
- Related decision: [ADR-0003](../decisions/ADR-0003-private-per-agent-mcp.md)
- Bitrix engineering task: pending

## Goal

Retire the five fixed shared MCP connector classes, make per-agent capability switches the only
model-facing authority, remove credentials from URLs, and make the MCP transport unreachable from
the public Internet without interrupting Bitrix, Telegram, or external webhook processing.

## Before

- Five shared MCP routes and three legacy SSE routes are registered alongside ten active per-agent
  connectors.
- Per-agent credentials are stored in `/mcp-agent/<slug>/<token>` URLs.
- `agent-main` can fall back to a broad shared ops connector.
- owner Telegram, dialogue summaries, error digest, and deterministic Telegram/CRM operations still
  depend on shared connector names or `/mcp` HTTP.
- Nginx proxies every path on `mcp.m4s.ru` to the MCP role.

## Target / after

- Only `/mcp-agent/<slug>` remains as a model-facing MCP route.
- Hermes connects through loopback and supplies the per-agent token in an Authorization header.
- Public `/mcp*` and `/sse*` requests receive 404 at Nginx and are independently rejected by Flask.
- Shared connector entries are removed from Hermes configuration and their compromised-by-design
  URL-era credentials are rotated.
- owner Telegram uses `agent-main`; summaries use the zero-tool quality contour; deterministic
  internal operations call an allowlisted in-process dispatcher.
- Main-agent connector failure is fail-closed and visible.

## Changed boundaries and files

Planned: Flask MCP routes/authentication, Agent Center connector materialization, Telegram internal
operations, Bitrix routing and summaries, error digest, deploy smoke, connector migration tooling,
Nginx configuration, tests, environment documentation, architecture audit, and overview diagram.

## Safety and privacy

- No credentials or raw private conversations will be written to Git or command output.
- Production code, environment, Hermes config, Nginx config, and the affected `agents` table will
  be backed up before mutation.
- Connector config is validated before atomic replacement and kept mode `0600`.
- Public webhook paths are tested separately so MCP blocking cannot silently block integrations.

## Verification plan and evidence

- Unit tests for loopback enforcement, header-only auth, retired routes, config generation,
  in-process allowlists, zero-tool summaries, and fail-closed main routing.
- Full pyflakes/pytest/predeploy and CI.
- Production compile, safe restart with no in-flight turns/running automations, deploy smoke for all
  active agents, public negative probes, header/path-token negative probes, webhook health, owner
  Telegram/Bitrix scenario checks, service/resource/journal checks.

Local implementation evidence:

- Removed the five shared Flask MCP routes and their SSE compatibility routes; retired paths are
  intercepted with 404.
- Added loopback plus forwarded-address enforcement, header-only steady-state per-agent auth,
  private connector materialization, config mode `0600`, cross-process config locking, and an
  atomic token/config migration script.
- Added a deliberately temporary, flag-gated path-token compatibility route for the first rollout
  restart only. It returns 404 by default and will be removed after live config/token migration.
- Moved dialogue summaries and error-digest analysis to the isolated zero-tool Codex runner; moved
  deterministic Telegram/CRM and maintenance operations to an allowlisted in-process dispatcher.
- Removed the broad main-agent fallback and changed owner Telegram's default to `agent-main,web`.
- Added Nginx 404/no-access-log rules on both public hosts while preserving webhook routes.
- Focused security/behavior matrix: `71 passed`, then compatibility-adjusted focused matrix:
  `63 passed`.
- Full local regression: `1884 passed, 43 skipped`; skipped cases require PostgreSQL/LibreOffice and
  run in CI/production as documented. Compile of all changed Python modules passed.

## Risks

- Rewriting Hermes config or rotating DB tokens out of order can temporarily disconnect agents.
- An over-broad Nginx matcher can block external webhooks.
- Removing shared routes before migrating owner/summarizer/internal callers can create silent
  degradation.
- A stale external custom client, if any exists outside the repository, will stop working by design.

## Rollback

Restore the pre-change code archive, `.env`, Hermes config, Nginx config, and `agents` table dump;
restore the previous Nginx configuration; restart only in an empty window; run the previous deploy
smoke and verify journals. A staged rollout flag keeps internal-only enforcement reversible until
the final verification completes.

## Known gaps and follow-up

Production evidence, backup paths, commit, CI, token-rotation result, and Bitrix engineering task
will be appended during implementation and deployment. Cross-host private MCP transport is out of
scope until the runtime leaves this server.
