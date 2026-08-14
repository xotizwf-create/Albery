# CHG-20260814-23: Complete Telegram reaction parity with Bitrix

- Status: verified
- Date opened: 2026-08-14
- Related decisions: [ADR-0005](../decisions/ADR-0005-channel-neutral-agent-runtime.md)
- Supersedes the incomplete final-reaction behavior recorded by: [CHG-20260814-22](CHG-20260814-22-telegram-durable-acknowledgement.md)
- Bitrix engineering task: not required; this is an employee Telegram transport correction

## Goal

Make employee Telegram use the same user-visible lifecycle as Bitrix: `eyes` while an authorized
request is being processed, then `thumbs up` only after the complete durable answer has been
accepted by Telegram.

## Before

CHG-22 restored the initial `eyes` acknowledgement before the expensive turn. The durable outbox
did not replace that reaction after successful provider delivery, so a completed Telegram answer
could still look merely “read” instead of “answered”. This is a UX/state-parity gap, not message
loss: the observed reply and native file were each delivered exactly once.

## Target / after

- Authorized inbound message: best-effort `eyes` before media/model work.
- Delivery retry/review/error or an incomplete multipart answer: keep `eyes`; never claim success.
- Only when every outbox part belonging to the captured update is durably `sent`, resolve the exact
  original message from the stored provider payload and replace the reaction with `thumbs up`.
- Reaction failure remains cosmetic and must never change the already committed outbox result or
  trigger delivery/model replay.
- Manual/automation outbox rows without an inbound `update_id` do not change any user reaction.

## Changed boundaries and files

- `tg_multi.py`: derive the original Telegram message only from the durable captured update and
  finalize its reaction after all related outbox parts are `sent`.
- Unit tests: success, incomplete multipart/failure and manual-outbox no-op behavior.
- Audit and current architecture after production evidence.

## Safety and privacy

The helper reads only the already-authorized update row and verifies `agent_slug`, chat identity and
terminal state. It does not read unrelated conversations, resend content, call Hermes/MCP or grant
Bitrix identity. Provider ambiguity cannot produce `thumbs up` because non-`sent` parts fail closed.

## Verification plan and evidence

Run focused Telegram/native-artifact tests, static checks and the full local suite; require green
security and both PostgreSQL CI matrices. Before production restart, require zero live Bitrix,
Telegram and automation work and create a private code backup. Deploy only `albery-tg.service`.
Without resending the answer, run the deployed finalizer against the already terminal owner update,
verify Telegram accepts `thumbs up`, then run smoke, self-check, queue and fresh-journal checks.

Local evidence on 2026-08-14:

- Focused employee Telegram/native-file suite: `40 passed`.
- Full local regression: `1993 passed, 48 skipped`; PostgreSQL-marked cases remain required in CI.
- Changed production module passes `pyflakes` and compilation; `git diff --check` is clean.
- Tests prove `thumbs up` requires update status `done`, every related outbox part `sent`, matching
  profile/chat and currently allowed stable identity. Incomplete/error/review, denied and manual
  outbox cases do not change the reaction.

Production evidence on 2026-08-14:

- Functional commit `aeefb6867b87a434190d0665d3a2a03f87b41e0c` passed GitHub tests run
  `31797071579` and security run `31797071625` before deployment.
- The preflight and restart gates both observed zero active Bitrix turns/jobs, automation brain or
  delivery stages, Telegram brain turns and Telegram delivery leases. The private code backup is
  `/var/backups/albery/code/pre-chg23-20260814_114219.tar.gz` with mode `0600`.
- Only `albery-tg.service` restarted. `albery-tg`, `albery`, `albery-web` and `albery-mcp` were all
  active afterward; the production worktree was clean at the exact approved commit.
- The deployed finalizer resolved the existing approved owner update `1` to original Telegram
  message `931`; Telegram accepted `thumbs up`. No reply/file was resent: response outbox `1`
  remained `sent`, attempts=`1`, provider message=`932`, and the independent native-file outbox `2`
  remained `sent`, attempts=`1`, provider message=`933`.
- Full production smoke returned `SMOKE OK`; workspace/Telegram/Bitrix/automation queues were empty,
  and fresh error journals for all four services contained no entries. Self-check returned success.
  Its rolling 65-minute window still reported one automation timeout from 14:35 MSK, seven minutes
  before this deployment; durable run `21` had already recovered to `done` on attempt two and made
  exactly one successful delivery. A separate read-only probe confirmed current Bitrix OAuth.

## Risks

A malformed/mismatched stored payload could target the wrong message. Fail closed unless update,
profile, chat and all outbox states agree. A provider reaction failure must be logged only at debug
level and must not downgrade a successful delivery.

## Rollback

Revert the code commit by fast-forward and restart only `albery-tg.service` after the normal empty
work gates. Stored update/outbox rows and delivered messages need no rollback.

## Known gaps and follow-up

None. The production acceptance used the existing approved conversation and did not resend any
message or file. The recovered pre-deployment automation timeout remains visible until it naturally
ages out of self-check's rolling journal window; it is not an active queue or delivery failure.
