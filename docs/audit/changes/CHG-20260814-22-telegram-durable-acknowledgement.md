# CHG-20260814-22: Restore employee Telegram acknowledgement on the durable path

- Status: verified
- Date opened: 2026-08-14
- Related decisions: [ADR-0005](../decisions/ADR-0005-channel-neutral-agent-runtime.md), [ADR-0009](../decisions/ADR-0009-durable-client-telegram-provider-boundaries.md)
- Bitrix engineering task: pending

## Goal

Remove the silent wait in the employee Telegram channel while preserving capture-before-processing,
the no-replay model boundary and durable outbound delivery.

## Before

Production captured the owner's message to the `main` employee profile, completed one agent turn in
about 22 seconds and Telegram accepted the stored reply. The durable code path did not invoke the
best-effort emoji acknowledgement used by the older in-process path, so the chat looked inactive
while the full agent turn was running.

## Target / after

After stable-id access is accepted, the employee profile bot places a best-effort `eyes`
acknowledgement on the exact inbound Telegram message before media/model processing. The reaction is
cosmetic: a provider failure is ignored and cannot change update, model or outbox state. The actual
answer remains a separately stored durable outbox delivery.

## Changed boundaries and files

- `tg_multi.py`: acknowledge an authorized, supported inbound message before the expensive turn.
- `tests/unit/test_tg_multi.py`: isolate reaction calls and assert the acknowledgement target.
- Audit/current architecture records after production acceptance.

## Safety and privacy

The reaction runs only after fail-closed access succeeds. It contains no message content and grants
no delegated Bitrix identity. It never replaces the durable answer and is never used as evidence
that a model or external action succeeded.

## Verification plan and evidence

Run focused reaction/Telegram tests, static checks and the full local suite; require green CI. Before
production mutation, create a code backup and confirm empty employee Telegram/Bitrix/automation
active queues. Deploy by fast-forward, compile, restart only `albery-tg.service`, then verify service
health, queue health and the already approved native Telegram file delivery.

Local evidence on 2026-08-14:

- The focused employee Telegram suite passes: `24 passed`.
- The full local suite passes: `1989 passed, 48 skipped`; database-marked tests remain delegated to
  the PostgreSQL CI matrix as designed.
- `pyflakes` reports no issue in changed production module `tg_multi.py`; the test module retains
  one pre-existing direct-import warning unrelated to this change. `git diff --check` is clean.

## Risks

Telegram may reject reactions for a chat or message type. This is intentionally best-effort. A
reaction must not be placed for a denied identity, and its failure must not delay or fail the answer.

## Rollback

Fast-forward to a reverting commit and restart only `albery-tg.service` after the normal safety
gates. The durable captured update and outbox rows need no rollback.

## Known gaps and follow-up

The first observed owner message was a short ordinary message rather than the prepared exact phrase.
It nevertheless traversed the real profile, brain and provider boundaries once. Final evidence and
status will be appended after deployment and the approved file acceptance.

## Production deployment and acceptance: 2026-08-14

Commit `353136f` passed security and both PostgreSQL CI matrices, then deployed by fast-forward only
after all Bitrix, Telegram and automation active-work gates were zero. Changed files were backed up
to `/var/backups/albery/code/pre-chg22-20260814_100905.tar.gz` with mode `0600`; only
`albery-tg.service` was restarted. The same production helper used by the automatic path placed the
`eyes` reaction on the owner's real inbound Telegram message and the Telegram provider returned
success. The actual durable message/reply pair remained exactly once, the approved native file was
sent exactly once, smoke/self-check passed, all queues were terminal/clean and fresh service error
journals were empty. The production behavior is `verified`.
