# CHG-20260813-21: Bitrix business correctness audit

- Status: in_progress
- Date opened: 2026-08-13
- Approval: owner approved continued execution of the recorded full audit roadmap

## Goal

Prove that Bitrix webhooks, bot turns, task comments, task/CRM mutations and OAuth identities are
durable, correctly attributed, conflict-safe and observable under duplicate delivery, restart,
timeout and partial provider failure.

## Safety boundary

Discovery is read-only. No message, task, comment, deal, contact or timeline entry is created,
updated or deleted. A real Bitrix mutation requires an exact object, author, payload, rollback and
fresh owner approval.

## Scope

- inbound webhook authentication, ACK timing, durable capture and duplicate delivery;
- bot and task-comment batching, restart behavior, provider ambiguity and user-visible recovery;
- OAuth/webhook identity separation, token refresh/rotation and author attribution;
- task and CRM create/update/delete preconditions, idempotency, object concurrency and partial writes;
- queue/dead-letter monitoring, PII-safe diagnostics, backups and rollback.

## Findings

### Baseline production evidence

- All 2,648 task-event rows are `done`; there is no queued/processing/error task sync work.
- The last 24 hours contain 11 bot interactions, zero non-`ok` and zero slower than five minutes;
  there are no current in-flight turns and no fresh warning journal entries.
- Bot-message first-sight has 712 rows. Task-comment first-sight has 681 rows: 386 confirmed
  handled, 291 intentional self-reply loop guards and four old non-self rows with `handled=false`.
- The four unhandled rows are from 7–9 July. They are historical proof of an incomplete task-comment
  path, but are not replayed: the original model/tool/provider outcome cannot be reconstructed
  safely after more than a month.

### Finding 1: local OAuth state was readable by other host users — remediated

The local Bitrix application state contained the rotating OAuth chain under
`.b24_testbot_state.json` with mode `0644`. No token value was copied into audit output. Commit
`208bac4` introduces one shared atomic JSON writer: a same-directory temporary file is `0600`,
flushed and fsynced, atomically replaced, and the published state is forced to `0600`. A failed
publish deletes only its temporary file and preserves the previous valid state. Both bot runtime and
standalone notification scripts now use the same primitive.

Production deployment used backup `/var/backups/albery/code/pre-chg21-oauth-20260813_171527`.
The existing file was changed from `0644` to `0600` without rewriting or rotating credentials.
Functional CI tests/security (`31709007521` / `31709007561`), full local regression
`1978 passed, 47 skipped`, bot-role health, full smoke, self-check and fresh journals are clean.

### Finding 2: task-comment work is acknowledged before it is durable — open

`OnTaskCommentAdd/Update` currently acknowledges Bitrix and starts a daemon thread. The thread fetches
the comment and inserts its first-sight marker before Hermes, but a process stop after HTTP ACK can
lose the work; a stop after first-sight can leave `handled=false` and the duplicate webhook is then
refused. The four historical rows above prove the terminal ambiguity exists. The safe target is a
PostgreSQL state machine with durable capture before ACK, one lease, a no-replay brain boundary,
stored answer and a `sending/sent/review` provider boundary. Until deployed, old rows remain evidence,
not a replay queue.

### Finding 3: ordinary bot-message dedupe fails open on database error — open

`_b24_message_claim` currently returns `True` when PostgreSQL fails. This protects availability but
can double-run Hermes and external tools during a database degradation. The channel needs durable
capture-before-ACK rather than either dropping the message or failing open.
