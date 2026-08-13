# CHG-20260813-20: Client Telegram/IU and CRM lifecycle audit

- Status: deployed
- Date opened: 2026-08-13
- Approval: the owner approved continued execution of the full recorded audit roadmap

## Goal

Prove that every client Telegram and Telegram Business event moves through Albery's IU workspace
exactly once, survives restarts, preserves manager/AI ownership and produces only authorized,
idempotent CRM and outbound effects. Make every failure visible and recoverable without replaying an
ambiguous provider action.

## Scope

- Bot API and Business update intake, offsets, deduplication, conversation/source identity and
  access boundary;
- text, voice, image, document and album ingestion, validation, temporary bytes and model routing;
- IU state machine, forms, terms, reminders, manager handoff/return and stale-button behavior;
- durable AI jobs, outbox, CRM actions, leases/retries, provider ambiguity and process restart;
- Bitrix lead/deal/contact/note mutations, author identity, idempotency and partial failures;
- retention cleanup, overdue spools, dead/review states, reference integrity and monitoring;
- read-only production inventory plus deterministic unit/DB/failure-injection acceptance.

## Safety boundary

Discovery is read-only. No real Telegram update is acknowledged artificially; no client or manager
message is sent; no lead/deal/contact/task/note is created or changed. A real-recipient/provider
acceptance requires an exact target, payload and cleanup preview followed by fresh explicit owner
approval. Tests use fakes or isolated transaction-scoped objects.

## Required evidence

1. One end-to-end diagram from provider update to durable intake, AI/manager decision, CRM and
   outbound delivery, with each commit/lease/idempotency boundary named.
2. Production counts/ages/statuses for all queues, outboxes, CRM actions, conversations and media
   references without message contents or unnecessary personal data.
3. Restart, duplicate update, known failure, ambiguous outcome, revoked access, stale button,
   media rejection and retention tests.
4. Monitoring and operator recovery for every non-terminal state.
5. Backup, rollback, CI, production smoke and an explicit list of any acceptance scenarios that
   remain prohibited without a real recipient.

## Actual production topology

- `albery-tg.service` is the only Bot API long-poll consumer. It persists a raw update before the
  Telegram offset advances; a failed database commit leaves the offset unchanged and makes the
  update replayable through its unique provider id.
- The current customer source is the public IU bot: `IU_CLIENT_BOT_ENABLED=1` and
  `IU_CLIENT_BOT_AI=1`. `FUNNEL_WORKSPACE_AI_ENABLED=0` controls the optional Business channel, not
  the public bot. `FUNNEL_WORKSPACE_BUSINESS_INTAKE=0` and both historical Business connections are
  intentionally disabled.
- The customer reasoning profile is `iu-customer-runtime` with zero MCP tools. Forms, stages,
  manager handoff, reminders, delivery and CRM synchronization are deterministic code paths.
- The durable stages are raw `updates`, debounced `ai_jobs`, `outbox`, `crm_actions`, client
  `reminders` and manager wait alerts. Conversation ownership uses `state_version` plus
  `ai/human/paused`; outbox and CRM lanes have unique idempotency keys and leases.
- Before remediation production contained 9 bot conversations, all linked to deals; 107 completed
  updates, 7 completed and 2 cancelled AI jobs, 88 sent outbox rows, 24 completed CRM actions and no
  active, expired, failed, unknown or dead-letter work. No client text or attachment content was
  copied into the audit.

## Findings and remediation

1. **Bitrix manager-alert ambiguity.** Alerts went directly from a leased row to
   `notify_iu_group`; any exception returned the row to `pending`, so a provider-side commit followed
   by a lost response could duplicate the alert. Migration `086` adds `sending`, `unknown` and the
   provider message id. The worker commits `sending` before Bitrix; errors become `unknown`, expired
   `sending` rows are recovered to `unknown`, and no blind retry occurs.
2. **Unsafe/inactive outgoing-file cleanup.** The spool contained 61 old `.bin/.json` pairs
   (122 files, 6,334,035 bytes) despite a seven-day policy. Cleanup now derives protected tokens from
   all non-terminal/ambiguous outbox rows, fails closed on database trouble, deletes only complete
   unreferenced token groups and runs in the maintenance loop. The first maintenance pass removed
   all 122 unreferenced files; they remain recoverable from the protected pre-change archive.
3. **Invisible customer queues.** `albery_selfcheck.py` previously inspected neither inbox nor AI,
   delivery, CRM, reminder or manager-alert queues. `scripts/workspace_queue_health.py` now reports
   only counts/ages for dead letters, failed/unknown outcomes, expired leases and work overdue by ten
   minutes. Every such condition is critical in the five-minute self-check.
4. **Personal-data retention.** Expired/used one-time form tokens now age out with the workspace
   retention. Full deleted-deal snapshots remain recoverable for 90 days by default and are then
   redacted while non-payload merge facts remain auditable. Current production records are younger
   than both thresholds, so deployment removed none of them.
5. **Media boundary.** Incoming files remain provider-hosted and are fetched server-side through an
   authenticated same-origin proxy: the bot token and provider file id never reach the browser;
   path, redirects, time, declared size and streamed bytes are bounded. Outgoing files use random
   tokens and `0700/0600` storage. Deep file-content/macro/archive scanning remains in the separate
   document/media workstream rather than being overstated here.

## Verification

- Focused local suites: 220 passed; full local regression: `1974 passed, 47 skipped`.
- Functional commit `2230a91`: GitHub tests run `31707111644` and security run `31707111605`
  succeeded; migrations and DB tests passed on PostgreSQL 14 and 16.
- Startup-retention commit `34abacc`: GitHub tests run `31707490723` and security run
  `31707490688` succeeded; focused local regression was 73 passed.
- Production is on `34abacc`. Migration `086` is present and validated, all five queue-problem
  groups are `0`, queue health is empty, full deploy smoke is `SMOKE OK`, self-check is clean, all
  Albery/Hermes services are active and fresh warning journals are empty.
- No Telegram or Bitrix message and no CRM object was created or changed for acceptance. A real
  customer/manager round trip therefore remains an explicit recipient/payload approval gate.

## Backups and removed material

- Pre-change code, database tables and the full 122-file spool are protected under
  `/var/backups/albery/code/pre-chg20-20260813_165508` and
  `/var/backups/albery/db/pre-chg20-20260813_165508.dump`.
- A second pre-final-restart backup is under `pre-chg20-20260813_165816`.
- The 122 expired, unreferenced spool files were removed automatically after the first deployed
  maintenance pass. They are recoverable from `funnel_outgoing.tar.gz` in the first code backup.

## Rollback

Fast rollback is the pre-change code tree plus restart of `albery-tg` and `albery-web`. Migration
`086` is backward-compatible and may remain. Exact schema rollback requires first proving there are
no `sending/unknown` manager rows, then restoring the old status constraint/removing the provider-id
column. Form tables can be restored from the targeted dump and removed spool files from the tar
archive. Durable queue rows must never be deleted merely to make monitoring green.
