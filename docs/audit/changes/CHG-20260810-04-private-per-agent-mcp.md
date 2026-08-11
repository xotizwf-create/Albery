# CHG-20260810-04: Private per-agent MCP migration

- Status: verified
- Date opened: 2026-08-10
- Related decision: [ADR-0003](../decisions/ADR-0003-private-per-agent-mcp.md)
- Bitrix engineering task: not created; no employee-visible task was needed for the technical rollout

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

Implemented: Flask MCP routes/authentication, Agent Center connector materialization, Telegram internal
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
- Used a deliberately temporary, flag-gated path-token compatibility route for the first restart,
  then removed the route and its flag from source and production after live config/token migration.
- Moved dialogue summaries and error-digest analysis to the isolated zero-tool Codex runner; moved
  deterministic Telegram/CRM and maintenance operations to an allowlisted in-process dispatcher.
- Removed the broad main-agent fallback and changed owner Telegram's default to `agent-main,web`.
- Added Nginx 404/no-access-log rules on both public hosts while preserving webhook routes.
- Focused security/behavior matrix: `71 passed`, then compatibility-adjusted focused matrix:
  `63 passed`.
- Full local regression after final compatibility removal: `1884 passed, 43 skipped`; skipped cases
  require PostgreSQL/LibreOffice and run in CI/production as documented. Compile and predeploy
  checks passed.

Production evidence:

- Compatibility/private implementation commit: `e2df63c315965f3632c86094ff41291e7d39ea13`.
- Final source commit with the path-token route physically removed:
  `a09e64c8120687807ad8e1ac3fe49e6841982e80`.
- Final perimeter hardening commit with no Nginx proxy to the MCP role:
  `1e0c3f87e5791570e4b6d08b3394c56d37b3575c`.
- GitHub Actions: tests `31396642964` passed on frontend, Python 3.12/PostgreSQL 16 and Python
  3.10/PostgreSQL 14; security audit `31396642760` passed. The first-phase runs `31395304088`
  and `31395303987` also passed.
- Final hardening CI: tests `31397960636` and security audit `31397961188` passed.
- Migration rotated all ten active agent credentials, removed five shared Hermes connectors and
  atomically installed ten loopback/header connectors in a mode-`0600` config.
- Live `tools/list` matched the exact DB/manifest-derived set for every active agent: counts were
  `110, 137, 166, 109, 10, 0, 0, 116, 141, 20` in slug order reported by deploy smoke.
- `scripts/deploy_smoke.py` passed after both production phases: 53 workflow references, retired
  shared/SSE routes, path-token, forwarded/public access, site, calculator, workspace and services.
- Both public hosts returned 404 for `/mcp`, `/mcp-agent/main` and `/sse`; the legacy MCP host's
  default route, including `/healthz`, also returned 404 while site login remained 200. Invalid
  Zoom/Bitrix webhook probes reached application authentication and returned 403, proving the
  allowlist did not swallow required callback paths.
- Ports `5002`, `5003`, and `5004` listened only on `127.0.0.1`; all six relevant services were
  active and five-minute error journals were empty.
- A synthetic zero-tool Codex text run passed; owner routing returned `agent-main,web`; the new
  in-process Telegram dispatcher completed a read-only CRM lookup and rejected an unallowlisted
  tool. No employee messages and no CRM writes were made during acceptance.

Backups created before mutation:

- code: `/var/backups/albery/code/pre-private-mcp-20260810_170232.tar.gz`, SHA-256
  `0280a5bd63f0aeb5d347fddae7961871fdabba0e32fb40de4d4681a1ab9eee02`;
- environment: `/var/www/albery/.env-backup-private-mcp-20260810_170232`, SHA-256
  `a2d96905d0b82d9e3686d0d1779bfaccfdb6440a8879976b84af2971c43e21a6`;
- Hermes config: `/root/.hermes/config.yaml.bak-private-mcp-pre-20260810_170232`, SHA-256
  `66fd7493e0a305409936b85ff4127ec0467647c8ea1a4fa5dda609a85bbfa679`;
- Nginx config: `/etc/nginx/sites-available/albery.bak-private-mcp-20260810_170232`, SHA-256
  `dcdf514896b360051e5b62570f0b002b0ced5a89db62cff26a294b562195a3d9`;
- agents table: `/var/backups/albery/db/agents-pre-private-mcp-20260810_170232.sql`, SHA-256
  `3f57cfd8bf224d25e9402d3a71846ac50770efc8103ff83cb0b419c3848ab484`;
- migration also created `/root/.hermes/config.yaml.bak-private-mcp-20260810_170526` immediately
  before replacing connector configuration.
- final dark-host Nginx backup:
  `/etc/nginx/sites-available/albery.bak-pre-dark-mcp-20260810_172623`, SHA-256
  `cb0932a83c5fb72704b8029ebd9b55cbdb4609996572e526c654a9ff1516adb2`.

## Risks

- Rewriting Hermes config or rotating DB tokens out of order can temporarily disconnect agents.
- An over-broad Nginx matcher can block external webhooks.
- Removing shared routes before migrating owner/summarizer/internal callers can create silent
  degradation.
- A stale external custom client, if any exists outside the repository, will stop working by design.

## Rollback

Restore the pre-change code archive, `.env`, Hermes config, Nginx config, and `agents` table dump;
restore the previous Nginx configuration; restart only in an empty window; run the previous deploy
smoke and verify journals. Restoring the old public/path-token design is not an accepted steady-state
rollback; if emergency access is needed, restore within loopback and rotate credentials again.

## Known gaps and follow-up

Cross-host private MCP transport is out of scope until the runtime leaves this server. A Bitrix
engineering task was intentionally not created because acceptance required no employee-visible
task or message; the Git audit record is the durable change log.

Correction recorded 2026-08-11: the acceptance above proved MCP capability and a synthetic Codex
turn, but did not assert effective VPN policy routing, the connected state of the Telegram
platform, or completion of a scheduled business output. CHG-20260811-05 adds these gates. The
private MCP transport was not the root cause of that incident, but the earlier acceptance scope
was insufficient to detect it.
