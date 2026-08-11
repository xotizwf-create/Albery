# ADR-0004: Durable and conflict-safe agent automations

- Status: accepted
- Date: 2026-08-11
- Owners: Albery engineering
- Supersedes: in-memory execution and whole-run retry for agent automations

## Context

Interactive Bitrix and Telegram turns share a PostgreSQL-backed two-slot limit, but scheduled agent
automations currently run in a separate in-process worker. On the 2 GB production host this permits
two live Hermes processes plus an automation process. The automation queue and delayed retry also
live only in Python memory, `Run now` checks and starts in separate database operations, and a failed
delivery repeats the complete Hermes turn. A repeated turn can repeat external writes.

Independent live and scheduled work can also read and modify the same Bitrix or other business
object without a common conflict boundary.

## Decision

- Every heavy Hermes process, including an agent automation, consumes the same PostgreSQL advisory
  run-slot pool. The server-wide default remains two; automation no longer has capacity outside it.
- Agent automation runs, stages, claims, leases, results and recipient deliveries are persisted in
  PostgreSQL. Workers claim due work with row locks and `SKIP LOCKED`; restarts do not erase queued
  work or known delivery retries.
- `Run now` creates at most one active manual run for an automation in one transaction. Scheduled
  runs use a deterministic key derived from automation and scheduled minute.
- A run has separate brain and delivery stages. A successfully stored brain result is delivered
  without invoking Hermes again. Delivery retries use only that stored result.
- Every run has a unique idempotency key. Mutating MCP calls made by an automation receive a
  deterministic fingerprint within the run and are recorded before execution. Completed identical
  calls return the stored result; an ambiguous prior call fails closed for review instead of being
  executed again.
- Mutating MCP calls use a PostgreSQL advisory lock derived from the integration, tool and business
  object identifier. This serializes conflicting live and automation writes. Where no stable object
  identifier exists, the lock deliberately becomes coarser and favors safety over throughput.
- Automation context is conveyed only through a second local Hermes connector alias for the same
  private per-agent MCP route. The alias adds a non-secret automation marker; the server accepts it
  only when there is one matching leased brain run. It does not create another public MCP endpoint.
- Ambiguous crashes are not blindly retried. A brain-stage crash after a potentially mutating tool
  call, or a delivery crash after send but before acknowledgement, is sent to manual review.
- Legacy `kind='system'` Hermes cron jobs remain a separate runtime and are not made safe by this
  decision. They must be migrated or audited independently.

## Alternatives considered

- Reserve one live slot and one automation slot: rejected because an idle lane wastes scarce
  capacity, while one shared two-slot semaphore already expresses the real server resource.
- Keep the in-memory queue and only add a mutex: rejected because restarts still lose queued and
  delayed work.
- Retry the whole automation after any error: rejected because successful external actions can be
  duplicated when only delivery failed.
- Rely only on prompt instructions to avoid duplicate actions: rejected because retry correctness
  is a runtime and persistence property, not a model-behavior guarantee.
- Add public automation MCP URLs or tokens: rejected by the private per-agent MCP boundary.

## Consequences

- Live work can make an automation wait, and automations can consume one of the two global slots.
  This is intentional backpressure; no more than two heavy Hermes processes are admitted.
- Queue state becomes observable and recoverable, but requires a database migration and cleanup
  policy for historical runs/effects.
- Conservative write classification and coarse fallback locks may serialize unrelated operations.
  The classifier must fail safe: unknown tools are treated as mutating.
- Exactly-once external delivery cannot be mathematically guaranteed when an external API has no
  idempotency primitive. Albery therefore distinguishes known failures from ambiguous outcomes and
  stops for review in the ambiguous case.

## Verification / revisit trigger

Verify shared-slot contention, concurrent double-clicks, scheduled deduplication, process restart
between every stage, delivery-only retry, repeated/ambiguous tool calls, business-object lock
contention, connector authentication, and unchanged live Bitrix/Telegram behavior. Revisit when the
host size or worker topology changes, when Bitrix exposes a delivery idempotency key, or before
migrating legacy system cron jobs.
