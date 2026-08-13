# CHG-20260813-20: Client Telegram/IU and CRM lifecycle audit

- Status: approved
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

## Findings

Pending read-only discovery.

## Rollback

No rollback is needed for discovery. Any remediation receives a protected pre-change backup and an
exact code/config/schema rollback before deployment. Durable queue rows are never deleted merely to
make a monitor green.
