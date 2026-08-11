# CHG-20260811-09: Channel-neutral Telegram agents

- Status: deployed
- Date opened: 2026-08-11
- Related decisions: [ADR-0005](../decisions/ADR-0005-channel-neutral-agent-runtime.md), [ADR-0003](../decisions/ADR-0003-private-per-agent-mcp.md), [ADR-0004](../decisions/ADR-0004-durable-conflict-safe-agent-automations.md)
- Bitrix engineering task: pending

## Goal

Make an employee agent behave the same in Bitrix and Telegram, with the communication channel as
the only transport/presentation difference, while retaining the isolated IU customer bot.

## Before

- Bitrix and Telegram can share `agent_slug` and MCP tools but assemble different prompts, histories,
  requester identities and delivery behavior.
- Agent Center creates a separate Telegram profile rather than attaching Telegram to an existing
  profile.
- Additional Telegram agents are public when their allowlist is empty and use in-memory offsets plus
  direct `sendMessage`.
- `agent_automations.deliver_to` is a Bitrix-only untyped string.
- The native owner Hermes Telegram bot is credential-degraded and remains a separate runtime.

## Target / after

- One agent profile owns identity, instructions, skills, personal learning, MCP rights and channel
  bindings; Bitrix and Telegram call one core turn builder.
- Agent Center can attach/manage Telegram on an existing agent and display all bound channels.
- Telegram access fails closed and optionally maps a Telegram identity to a Bitrix employee id for
  safe delegated actions.
- Profile Telegram intake, brain jobs and replies are durable and recoverable without blind resend.
- Automations store a typed origin/destination and deliver through the correct profile/channel.
- IU customer workspace and its zero-tool policy remain unchanged.

## Changed boundaries and files

- `shared/agent_channel_runtime.py` — channel context and common profile behavior/knowledge contract.
- `shared/media_ingestion.py` — Groq-only media preprocessing for screenshots, audio and documents;
  no business tools or decision authority.
- `b24bot.py`, `tg_multi.py` — same profile identity/private MCP, channel-scoped history and durable
  employee Telegram intake/outbox.
- `agent_center.py` and Agent Center UI/API — attach Telegram to an existing profile, explicit
  fail-closed access and optional Telegram-to-Bitrix employee mapping.
- `agent_automations.py` and migration `084_channel_neutral_telegram_agents.sql` — typed channel,
  profile and conversation destination plus Telegram sender adapter and access re-check on delivery.
- `scripts/deploy_smoke.py`, `.env.example`, unit tests and architecture/audit documents.

## Safety and privacy

- No bot token, personal chat content or raw credential enters Git, test output or audit records.
- Empty/unknown access always denies before Hermes. Telegram identity never authorizes Bitrix
  impersonation without an explicit stable mapping.
- Provider-ambiguous delivery stops for review; retries never recompute a successful brain result.
- Existing IU customer traffic and production services remain untouched until local and CI evidence
  is complete and an authorized safe deployment window exists.

## Verification plan and evidence

- Prompt/context equivalence tests for main and subagents across Bitrix/Telegram.
- Access tests for empty list, username bootstrap, stable Telegram id, revocation and Bitrix mapping.
- PostgreSQL migration repeatability plus update/turn/outbox claim, restart and dedup tests.
- Automation typed-destination tests for Bitrix and Telegram, including stored-result retry and
  ambiguous send review.
- Regression for existing Bitrix turns, IU workspace, private MCP, shared run slots and automations.
- Python compile/pyflakes/full pytest, frontend lint/build, dependency audits, CI, then controlled
  production migration/restart/smoke if access is available.

Initial evidence:

- Read-only audit CHG-20260811-08: 451 unit/contract tests passed; 19 PostgreSQL tests skipped
  locally without `DATABASE_URL`; fresh SSH to server 186 was rejected.
- Pre-change local backup:
  `tmp/backups/pre-chg-20260811-09-20260811_142804`; recorded Git head `64dfbdb`.

Implementation evidence:

- Python compilation and `git diff --check`: passed.
- Targeted channel/access/media/automation/migration suite: `48 passed`; after the final stable-id
  hardening, the affected critical subset was rerun: `43 passed`.
- Full project unit regression after the final hardening: `1745 passed, 1 skipped` (LibreOffice is
  server-only), with no
  functional failures; the nine warnings are third-party `httplib2` deprecations.
- Frontend TypeScript check: `npm run lint` passed; production bundle `npm run build` also
  passed (`2341` modules transformed). Vite reported only the pre-existing large-chunk
  optimization warning, not a build failure.
- Pyflakes passed for the changed Python implementation modules; the three findings in
  `b24bot.py` are unchanged pre-existing unused locals/import outside the CHG-09 diff.
- `npm audit --omit=dev --audit-level=high`: `0 vulnerabilities`; no literal
  credential-shaped values were found in the changed implementation files.
- Commit `6067b5c` was pushed to `origin/main`.
- GitHub `tests` run `31490360688`: passed. It built the frontend and applied the complete
  schema plus migration `084` before DB-marked tests on PostgreSQL 14/Python 3.10 and
  PostgreSQL 16/Python 3.12.
- GitHub `Security audit` run `31490361457`: passed.
- The updated master SVG parses as valid XML and the regenerated `1800×2070` PNG was
  visually inspected; the diagram explicitly separates confirmed production from the
  feature-gated `implemented_local` target.
- Production read-only inspection was attempted with the configured server-186 credential and
  rejected during SSH authentication. No remote command ran and no production state changed.
- At the local-only checkpoint the feature flag default remained
  `TG_CHANNEL_NEUTRAL_ENABLED=0`; migration, historical bot reconciliation and live probes were
  still required before production cutover.

Production deployment evidence on 2026-08-11:

- The authorized rollout used the credential vault on server 217; no credential or bot token was
  printed or copied into Git. Production was a clean fast-forward from `9cb40b1` to implementation
  head `216dccb`; GitHub tests run `31490595463` and security run `31490595486` were green first.
- A complete pre-change backup was verified under
  `/var/backups/albery/pre-chg09-20260811_154147`: Git bundle/code archive, PostgreSQL custom dump
  and schema, `.env`, systemd, Hermes configuration and the previous frontend bundle. Files are
  mode `0600`; the previous frontend remains available as an explicit rollback directory.
- Migrations `083` and `084` applied successfully and repeatably. Automation connector aliases
  were materialized from the exact per-agent private MCP definitions; no public MCP route or URL
  credential was added.
- The inspected historical duplicate was reconciled explicitly, not by name: its single stable
  Telegram access binding moved to active profile `main`; the legacy `albery-ai` row was unbound
  and deactivated. No message history row required migration. The owner-approved user mapping was
  not guessed: `bitrix_user_id` remains null until set explicitly in Agent Center.
- Cutover ran behind an empty-inflight gate. The new frontend was swapped atomically, migrations
  preceded worker activation, and `TG_CHANNEL_NEUTRAL_ENABLED=1` was then set atomically. Only
  `albery.service` and `albery-tg.service` required controlled restarts; all three relevant services
  are active and their post-cutover warning/error journals are empty.
- Production assertions passed: the profile bot identity responds to `getMe`; the one active access
  row has a stable Telegram id; the allowed stable identity is accepted, a wrong id with the same
  username is denied, and an unknown identity is denied before Hermes. The migration tables,
  durable offsets/update/outbox contract, workspace transport, Bitrix/workspace routes and all nine
  active private MCP matrices passed smoke.
- The first smoke sampled a brief VPN self-healing window. The existing hardened watchdog restored
  the required rules without manual repair; the effective route is again table 200 via `awg0`, the
  external address is the VPN exit and OpenAI returns the expected unauthenticated `401`. The
  immediate repeat passed the VPN gate.
- Full deploy smoke now fails only on the pre-existing, separate native
  `hermes-gateway.service` Telegram platform state `retrying` (its old bot token is rejected). The
  new channel-neutral employee bot, the IU workspace Telegram transport and all CHG-09 gates pass.
- No real employee message, automation, task or other external write was created during acceptance.
  Therefore the rollout is `deployed`, not `verified`: a named non-production recipient is still
  required for a user-visible Telegram round trip, and an explicit Telegram-to-Bitrix employee
  mapping is required before delegated Bitrix actions can be accepted.

## Risks

- A prompt refactor can subtly change successful Bitrix behavior.
- Incorrect identity mapping could execute a write as the wrong employee.
- Poll/update leasing errors can stall or duplicate turns.
- A wrong sender binding can expose one profile through another profile's bot.
- Deploying migration/code separately can leave workers reading an unsupported schema.

## Rollback

The change is additive and feature-gated. Disable the channel-neutral Telegram worker, stop new
claims, wait for running leases, restore the previous `tg_multi` path/code commit, and leave durable
rows for forensics. Before production migration take `pg_dump` of `agents`,
`telegram_bot_access`, `agent_automations` and all new channel tables, plus service/env/config
backups. Restart only through the empty-inflight safe gate.

## Known gaps and follow-up

- A valid credential and explicit owner-approved recipient are required to replace and live-test the
  separate degraded native Hermes Telegram bot.
- The inspected `albery-ai` duplicate was merged explicitly. Future duplicates must still be
  inspected; automatic name-based merging remains forbidden.
- Native Hermes `kind='system'` cron inventory and migration remain explicit rollout gates.
- Production-shaped PostgreSQL migration and structural smoke passed before the flag was enabled.
  A user-visible round trip was intentionally not sent without an approved recipient.
