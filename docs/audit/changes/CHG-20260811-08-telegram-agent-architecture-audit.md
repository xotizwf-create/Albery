# CHG-20260811-08: Telegram agent architecture audit

- Status: superseded
- Date opened: 2026-08-11
- Related decisions: [ADR-0003](../decisions/ADR-0003-private-per-agent-mcp.md), [ADR-0004](../decisions/ADR-0004-durable-conflict-safe-agent-automations.md)
- Bitrix engineering task: not required for a read-only audit; any runtime correction gets its own task/change

## Goal

Establish the actual Telegram architecture: distinguish the client bot from internal AI-agent
channels, prove how Telegram identities map to shared Albery profiles and permissions, trace inbound
and outbound delivery, determine whether new Telegram agents can be created safely, and identify
where Telegram-created automations execute and deliver.

## Before

- The master architecture lists a Hermes owner gateway, standalone `albery-tg.service`, and optional
  Telegram identities on agent profiles, but does not give one end-to-end Telegram map.
- Production evidence already says the Hermes Telegram gateway is degraded by a rejected bot token,
  while the standalone client Telegram service and Bitrix paths are separate.
- It is not yet documented whether a Telegram and Bitrix identity can reliably represent one shared
  agent profile, how per-channel session isolation works, or what the supported provisioning path is.

## Target / after

- One evidence-backed map covers profiles, tokens/configuration boundaries, ingress, session scope,
  shared run slots, MCP/tool permissions, media handling, durable delivery, retries and automation
  destinations.
- Verified production facts, repository behavior and unverified assumptions are clearly separated.
- Gaps that require code/configuration changes are proposed separately; this audit does not mutate
  production, send messages, create bots, rotate tokens or run write scenarios.

## Changed boundaries and files

- Read-only code, migration, service, test and audit inspection.
- Documentation updates: this record, `docs/audit/architecture/CURRENT.md`, audit indexes, and the
  workspace master Markdown/SVG/PNG.
- No runtime code, database row, environment, token, Telegram bot, service or production setting was
  changed by this audit.

## Findings

### Three separate Telegram contours

| Contour | Runtime and identity | Profile/capabilities | Delivery and current evidence |
| --- | --- | --- | --- |
| Internal owner AI | Native Telegram platform in `hermes-gateway.service`, with its own bot token, Hermes state/history, allowed-user config and native cron | Production audit says its MCP surface is `agent-main,web`; this shares the main profile's private tools, but not the Bitrix prompt/session/history/access implementation | Native gateway delivery. Latest production evidence under CHG-20260811-05: service active, Telegram platform `retrying`, direct `getMe` 401 because the configured token is rejected |
| Client bot / IU workspace | `albery-tg.service` owns the `TG_AGENT_BOT_TOKEN`, direct bot chats and Telegram Business updates. With the client entry enabled, even an owner DM is routed into the client scenario, not the fallback owner assistant | Untrusted customer text uses the role selected for the IU customer flow and the manifest-capped zero-tool `agent-iu-customer-runtime`; Telegram/CRM mutations are deterministic workers | Raw update is committed before offset advance; PostgreSQL outbox has idempotency, leases and `unknown` for ambiguous sends. CHG-20260811-05 confirmed this transport healthy in production |
| Additional Telegram agents | `tg_multi.py` starts one long-polling thread and one bot token per active `agents` row with `telegram_bot_token` | Uses `agent-<slug>,web`, so tools are the same per-agent MCP capability set configured in Agent Center. It has a channel-specific prompt/history/access implementation rather than the Bitrix turn builder | Direct synchronous `sendMessage`; no durable outbox, persistent offset, delivery retry or ambiguous-send review. No fresh live inventory/health proof was available |

`tg_agent.py` also contains a fallback owner-assistant path using `agent-main,web` when the client
entry is disabled. It is not the normal client-enabled route and creates another channel-specific
prompt/history implementation; it must not be mistaken for the native Hermes gateway.

### “One agent, different channels” is only partial

- The database supports both `bitrix_bot_id` and Telegram bridge fields on one `agents` row, and the
  MCP capability boundary is correctly keyed by the same `agent_slug`.
- The supported UI create path nevertheless chooses one bridge: Telegram creation makes a new
  Telegram agent; Bitrix creation registers a Bitrix bot. There is no supported action to attach a
  Telegram bot to the existing `main` profile or to an existing Bitrix profile.
- Agent Center hard-codes the main card as `channels=['Bitrix']`. The built-in Telegram bot is seeded
  as `albery-ai-bot`, but its runtime does not consistently use that profile: the fallback trusted
  owner turn uses `agent-main`, while the client turn uses `iu-customer-runtime`.
- Bitrix and Telegram therefore share model/provider and may share the same MCP tool boundary, but
  they do not share the same prompt builder, conversation history, requester identity, access model,
  delivery identity or automation destination. The current system is not yet one channel-neutral
  agent runtime.

### Creating and controlling Telegram agents

- Agent Center can create a new Telegram-only profile, ask BotFather for a bot, validate `getMe`,
  store the bridge and materialize the private `agent-<slug>` connector. The supervisor discovers it
  within the configured reload interval without a service restart.
- The same capability editor controls MCP tools, linked instructions, skills, role and active state.
  New profiles start with the bounded base tool mode rather than a broad preset.
- Conversation access is separate from Bitrix membership. Additional bots use
  `telegram_bot_access` keyed by username; the numeric Telegram id is learned only after a message.
- Critical mismatch: an empty access list on an additional bot intentionally means **allow everyone**.
  This contradicts the UI text that tells the owner to add allowed people and implies everyone else
  will be refused. A newly created bot can therefore expose its enabled business tools publicly until
  at least one allowlist row exists.
- The native Hermes gateway uses a separate environment/config allowlist and restart-based management;
  it is not controlled by the Agent Center access table. Revocation in the Agent Center cache can also
  take up to its configured TTL to affect `tg_multi`.

### Reliability and delivery

- Client workspace delivery is the strongest path: durable raw intake, per-conversation ordering,
  state-version conflict protection, one transactional outbox, file delivery, bounded CRM actions,
  no blind resend after a network-ambiguous provider call.
- Additional agents process all users of one bot serially inside one polling thread. A slow Hermes
  turn blocks polling for that bot, although other bot threads continue and the Hermes subprocess
  still uses the shared server-wide slot pool.
- Additional agents keep polling offsets only in process memory and send replies directly. A crash
  after Telegram accepted a reply but before the next higher-offset poll can replay the update and
  duplicate the answer. A failed send is only journalled as error and is not durably retried.
- All additional agents share the `albery-tg.service` process. An unrecoverable startup failure of
  the primary client token can restart that service and take the additional polling threads down too.
- Monitoring checks the service and main workspace transport, not a live `getMe`/poll/delivery state
  for every additional token. Agent-card usage statistics are also based on Bitrix interactions, so
  Telegram-only agent activity can be underreported.

### Automations requested from Telegram

- `schedule_my_automation` is available through a profile connector when its manifest allows it, so
  a Telegram agent can create a row owned by the correct `agent_slug`.
- The contract requires `deliver_to` to be a **Bitrix dialog id**. Both current and CHG-07 delivery
  call `_albery_bitrix_notify`; there is no channel/type field and no Telegram delivery adapter.
- A Telegram chat id passed from a Telegram conversation is therefore treated as a Bitrix destination
  and normally fails or goes nowhere useful. A pure Telegram profile has no Bitrix bot id, so delivery
  falls back to the main Bitrix bot rather than the Telegram bot that created the automation.
- Native Hermes cron is a different mechanism: its final output is delivered by the Hermes gateway to
  its configured Telegram target. Those `kind='system'` jobs are outside the new durable CHG-07 worker
  and the shared automation safety contour; the currently rejected gateway token blocks that delivery.

The correct future contract is an explicit destination such as
`{channel: telegram|bitrix, identity/profile, conversation_id}` plus a durable Telegram adapter. A
Telegram request must default to its current Telegram conversation; a Bitrix request must default to
its current Bitrix dialog. The channel must never be inferred from a bare numeric id.

## Safety and privacy

- Never print or record Telegram bot tokens, API credentials, chat identifiers tied to people,
  message contents or MTProto session material.
- Live probes, if access exists, are limited to service/configuration metadata and non-message health
  checks. No employee/client message is sent without explicit owner approval.

## Verification plan and evidence

- Inventory Telegram services, modules, environment variable names, profile fields and DB tables.
- Trace client-bot and AI-agent ingress/egress paths separately.
- Trace profile selection, session keys, MCP connector/tool cap and access checks.
- Trace Telegram media and shared run-slot behavior.
- Trace automation creation and delivery target parsing for Telegram-originated requests.
- Run focused non-destructive unit/contract tests and inspect existing deployment evidence.
- Attempt a read-only server-state check only if an authorized existing connection works.

Evidence completed on 2026-08-11:

- `origin/main` and local `main` both pointed to `f0ceef8`; inspected Telegram runtime code includes
  the CHG-07 shared-slot changes at `971f0b8`.
- Focused owner/additional-agent/workspace/client tests: 227 passed.
- The remaining Telegram, workspace, media, CRM and import unit suites: 212 passed.
- Automation-delivery and automation-safety contract tests: 12 passed. They confirm that the durable
  agent automation delivery adapter is `_albery_bitrix_notify`, not Telegram.
- PostgreSQL workspace suites: 19 skipped locally because `DATABASE_URL` is absent; these tests are
  designed to run in CI. No local result was mislabelled as database evidence.
- Fresh read-only SSH to `root@186.246.7.32` failed with `Permission denied`; no new live message,
  token check, bot inventory, journal query or service probe was performed.
- Latest usable production evidence remains CHG-20260811-05: `albery-tg.service` and workspace
  Telegram transport active/healthy, but native Hermes Telegram `retrying` with rejected token 401.

## Risks

- Similar bot names may hide two unrelated runtimes and credentials.
- Code capability may be mistaken for configured or healthy production behavior.
- A shared agent profile may still have channel-specific histories, delivery identities or permission
  gaps that make the experience differ between Bitrix and Telegram.
- Legacy Hermes gateway automations may bypass Albery's durable automation and delivery mechanisms.
- A newly created additional bot is open to every Telegram user while its access list is empty.
- Treating a Telegram id as a Bitrix `deliver_to` can silently misroute or lose an automation result.
- Plain bot credentials exist in the application database and BotFather business-message flow; log,
  backup and database access must be treated as secret-bearing boundaries.

## Rollback

Audit-only: revert documentation commit and restore workspace files from the pre-audit backup. No
runtime, database, token, bot registration or production service change is authorized by this record.

## Known gaps and follow-up

- Replace the rejected native Hermes gateway token, then verify connected state and one approved
  non-production-recipient round trip.
- Design one channel-neutral profile/runtime contract so `main` can have both Bitrix and Telegram
  identities while retaining per-channel conversation histories and correct sender identities.
- Make new additional Telegram bots fail closed until an explicit allowlist exists; switch access to
  stable numeric identities after first contact and invalidate cache immediately on edits.
- Add explicit channel-aware durable automation destinations and a Telegram delivery stage; do not
  overload the Bitrix `deliver_to` string.
- Move additional-agent intake/delivery to durable update/outbox state and add per-bot health,
  backoff, metrics and live smoke.
- Decide whether automatic BotFather creation and plaintext DB token storage meet the desired secret
  model; if retained, redact token-bearing network exceptions and protect/expire the business log.
- Inventory native Hermes cron jobs separately and bring every Hermes-spawning job under the shared
  server-wide run limit before calling all automations conflict-safe.
- Any runtime correction requires a separate approved CHG, backup, tests, CI, deployment and live
  acceptance; this audit deliberately makes no production claim beyond inherited evidence.

## Amendment 2026-08-13: superseded by the implemented channel architecture

This read-only record remains the authoritative historical before-state. Its remediation target was
implemented by CHG-09 (one logical Bitrix/Telegram profile, fail-closed stable identity, durable
intake/outbox and typed automation destinations), CHG-12 (shared heavy-system limit) and CHG-13
(explicit retirement of the duplicate rejected native Hermes transport). It is therefore
`superseded`, not `verified`: the still-open first user-visible employee Telegram round trip belongs
to CHG-09/13 and remains `deployed` until an approved recipient/message preview exists.
