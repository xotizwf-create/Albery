# CHG-20260811-09: Channel-neutral Telegram agents

- Status: implemented_local
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
- The updated master SVG parses as valid XML and the regenerated `1800×2070` PNG was
  visually inspected; the diagram explicitly separates confirmed production from the
  feature-gated `implemented_local` target.
- Production read-only inspection was attempted with the configured server-186 credential and
  rejected during SSH authentication. No remote command ran and no production state changed.
- Feature flag remains `TG_CHANNEL_NEUTRAL_ENABLED=0` by default. The migration, historical bot
  reconciliation, current token `getMe`, automation inventory and live round trip must pass before
  production cutover.

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
  degraded internal Telegram bot.
- Duplicate historical Telegram profiles need an inspected merge; automatic name-based merging is
  forbidden.
- Native Hermes `kind='system'` cron inventory and migration remain explicit rollout gates.
- Local PostgreSQL integration tests could not run because no local `DATABASE_URL` or Docker engine
  is available; migration syntax/repeatability must therefore be proven on a disposable or
  backup-restored PostgreSQL database before the production flag is enabled.
