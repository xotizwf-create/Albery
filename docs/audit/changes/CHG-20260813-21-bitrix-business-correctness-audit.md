# CHG-20260813-21: Bitrix business correctness audit

- Status: verified
- Date opened: 2026-08-13
- Approval: owner approved continued execution of the recorded full audit roadmap
- Remediation approval: 2026-08-14, owner approved items 1–3 of the next-step plan

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

### Finding 2: task-comment work was acknowledged before it was durable — remediated

`OnTaskCommentAdd/Update` previously acknowledged Bitrix and started a daemon thread. The thread fetched
the comment and inserts its first-sight marker before Hermes, but a process stop after HTTP ACK can
lose the work; a stop after first-sight can leave `handled=false` and the duplicate webhook is then
refused. The four historical rows above prove the terminal ambiguity exists. The safe target is a
PostgreSQL state machine with durable capture before ACK, one lease, a no-replay brain boundary,
stored answer and a `sending/sent/review` provider boundary. Production now inserts a unique,
token-free event row before HTTP 200; a PostgreSQL failure returns retryable HTTP 503 and starts no
agent work. The four historical rows stay unchanged and are not imported into the new queue.

### Finding 3: ordinary bot-message dedupe failed open on database error — remediated

`_b24_message_claim` returned `True` when PostgreSQL failed. The production webhook no longer uses
that legacy claim while the durable flag is enabled: chat messages are captured atomically before
ACK, and capture failure returns HTTP 503. The insert also writes the legacy first-sight marker in
the same transaction so older observability remains coherent.

## Post-deploy control: 2026-08-13 17:24 MSK

A read-only production control after deployment `208bac4` found no regression:

- `albery`, `albery-tg`, `albery-web`, `albery-mcp`, `hermes-gateway`, Nginx, PostgreSQL and the
  self-check timer are active; there are no failed systemd units;
- bot/web/MCP health endpoints report `database=ok`; the complete deploy smoke ends in `SMOKE OK`
  and self-check reports `clean`;
- all nine private MCP endpoints preserve their exact expected tool counts, public and retired MCP
  routes remain closed, VPN/provider reachability and the canonical signed-export check pass;
- the OAuth state exists, parses as non-empty JSON and is owner-only `0600`; no credential content
  was emitted by the check;
- all 2,648 task events and all seven recorded automation runs are `done`; no active task event,
  in-flight Bitrix turn, workspace queue item or manager-alert delivery remains;
- the relevant service journals contain no warning-or-higher entry since deployment and the
  production Git tree is clean at `208bac4`.

No real message or CRM mutation was used for this control. There were no new Bitrix bot interactions
between the deployment and the check, so this evidence proves runtime/infrastructure regression
coverage but does not replace an explicitly approved recipient-visible round trip. The two open
durability findings above remain pre-existing design risks and were not introduced by the OAuth
change.

## Approved remediation target: 2026-08-14

Related decision: [ADR-0010](../decisions/ADR-0010-durable-bitrix-inbound-boundary.md).

Before behavior:

- chat dedupe fails open when PostgreSQL is unavailable;
- chat batching and task-comment dispatch live in process memory after the webhook ACK;
- an interrupted brain or provider call has no durable stage that distinguishes safe retry from an
  ambiguous external outcome;
- a successfully generated answer is not independently recoverable for delivery-only retry.

Target behavior:

- capture every authenticated chat/task-comment event in PostgreSQL before ACK, with secrets
  removed and a unique provider-event key;
- preserve multi-part chat batching through durable scope claims and leases;
- commit `brain_running` as the no-replay boundary, store the completed answer, then commit
  `sending` before the provider call;
- retry only safe preparation and known delivery failures; move interrupted brain/sending work to
  `review` without blind replay;
- monitor all nonterminal/review states in self-check and retain the four historical unhandled
  comments as non-replayed evidence.

Risks are accidental double processing, altered batching, wrong profile/bot authorship, stuck queue
growth and a deploy restart during a live turn. Rollback is the protected pre-change code/database
backup plus the feature flag; the additive table remains inert on rollback. Production restart is
allowed only with empty live/automation work, and deployment requires migration, compile, focused
failure injection, full regression, CI, smoke, queue checks, exact profile counts and fresh logs.

## Durable inbound deployment: 2026-08-14

Functional commits `73ecbcb` and `2b93c6f` implement ADR-0010 for ordinary bot messages and task
comments:

- migration `087` adds `bitrix_inbound_jobs` with unique provider-event keys, durable batching,
  leases, stored answers and explicit `brain_running`, `sending`, retry, review and terminal states;
- authenticated webhooks persist a recursively token-free payload before ACK and fail closed with
  retryable HTTP 503 when exact capture is unavailable;
- concurrent stored-answer claims exclude active leases, the producing worker retains ownership
  through the delivery boundary and long brain calls renew their lease;
- safe preparation and known provider rejection retry in stages; an interrupted model/tool turn or
  ambiguous send stops for review and is never replayed blindly;
- self-check reads only state/count/age information and treats overdue, failed and review work as
  critical. The worker still enters the existing shared two-slot Hermes limiter.

Local focused tests passed `59/59`; the complete local suite passed `1988`, with 48 environment-only
skips. The first PostgreSQL CI run correctly exposed an early retry-delay assumption in the new
test; production was not touched. After correcting the test, GitHub tests `31788704439` passed on
PostgreSQL 14/Python 3.10 and PostgreSQL 16/Python 3.12, and security run `31788704444` passed.

Before production mutation, code and the three affected legacy tables were protected as private
`0600` archives under `/var/backups/albery/code/pre-chg21-20260814_093827.tar.gz` and
`/var/backups/albery/db/pre-chg21-20260814_093827.dump`; `pg_restore --list` accepted the database
archive. The pull/restart gates both found zero in-flight Bitrix turns and zero running agent
automations. Production is clean at `2b93c6f`, migration `087` is present, `albery`, `albery-web`,
`albery-mcp` and `albery-tg` are active, full smoke is `SMOKE OK`, self-check is clean, the new queue
and its content-free health result are empty, the public imbot readiness route returns HTTP 200 and
fresh error journals are empty.

This closes the structural crash/database durability findings. Status remains `deployed` until one
fresh approved owner-visible chat round trip traverses the new queue; task-comment delivery remains
covered by deterministic/DB tests unless an exact disposable task and cleanup are separately
approved.

## Fresh durable owner round trip: 2026-08-14

After migration `087` and the staged worker were live, the owner approved the exact visible response
`Тест durable Bitrix пройден` in the existing private chat. One authenticated synthetic provider
event was captured before HTTP 200; an immediate redelivery returned `duplicate=true` and referenced
the same job. PostgreSQL contains one event row. It completed `sent` with one brain attempt and one
delivery attempt, stored the exact answer, and Bitrix returned provider message id `45682`. The exact
outbound journal row exists once. No in-flight/open Bitrix job, automation, Telegram/workspace queue
or warning/error journal remained; full smoke and self-check passed. This closes the stated fresh
live gate and advances CHG-21 to `verified`. Task-comment provider delivery remains covered by the
same deployed stage machine plus deterministic/PostgreSQL matrix tests; no disposable real task was
created without separate approval.

## Owner-visible live round trip: 2026-08-13 17:29 MSK

The owner explicitly approved a constrained live acceptance. The public authenticated Bitrix app
webhook received one synthetic `ONIMBOTMESSAGEADD` event as Alexander Nikitenko (`user_id=16`) for
the main Albery bot (`bot_id=24`) in the existing private dialog. The exact request prohibited tools
and external actions and required the exact answer `Тест Albery пройден`.

- the first public webhook call returned HTTP 200 with `accepted=true`;
- an immediate redelivery of the same synthetic message id returned HTTP 200 with
  `duplicate=true`;
- PostgreSQL contains exactly one first-sight claim, one inbound journal row and one matching
  interaction, proving the duplicate did not start a second turn;
- Hermes completed in 13,610 ms with status `ok`, no error and exactly the required answer;
- Bitrix accepted exactly one final outbound message for the owner and returned provider message id
  `45262`; the same exact text is present once in the outbound journal;
- no in-flight row remained, there was no warning-or-higher service log entry, all queues stayed
  empty, all seven automations and 2,648 task events stayed `done`;
- the post-send full smoke again ended in `SMOKE OK`, self-check was `clean`, all services and three
  role health endpoints remained healthy.

The synthetic inbound text exists in the internal immutable journal but was not posted as a visible
human chat message; the bot's reply was a real provider-visible Bitrix message to the approved owner.
No task, CRM object, automation or third-party recipient was changed. This closes the ordinary
Bitrix private-chat live round-trip acceptance gap. It does not close Findings 2 and 3, which concern
crash/database-degradation durability and still require implementation.
