# ADR-0010: Durable Bitrix inbound and provider boundaries

- Status: accepted
- Date: 2026-08-14
- Owners: Albery engineering
- Supersedes: process-local Bitrix webhook dispatch and fail-open message claims

## Context

Bitrix chat messages were deduplicated in PostgreSQL but processed from an in-memory batch after the
HTTP response. A database failure made the dedupe claim fail open. Task-comment events returned HTTP
200 before any durable capture and then ran in a daemon thread. A restart could therefore lose an
acknowledged event, while replay after a partially completed Hermes/tool turn could duplicate an
external action. Brain output and provider delivery were not separate durable stages.

## Decision

- An authenticated Bitrix chat-message or task-comment webhook is acknowledged only after an
  immutable, token-free event payload has been inserted into PostgreSQL under a unique event key.
  If PostgreSQL cannot capture it, Albery returns a retryable non-2xx response and does not start
  Hermes or a provider call.
- Workers claim due events with row locks, `SKIP LOCKED`, bounded leases and a stable batch id.
  Consecutive chat pieces for one bot/dialog/user retain the existing batching behavior, but the
  batch itself is durable rather than process memory.
- Safe preparation may be retried. The transition to `brain_running` is the no-replay boundary:
  an expired brain lease becomes `review`, because tools may already have produced an external
  effect.
- The completed answer is committed before delivery. Known provider rejection retries only the
  stored answer. `sending` is committed immediately before Bitrix; timeout, connection loss or an
  expired sending lease becomes `review` and is never resent blindly.
- Task-comment replies use the same staged contract. Historical `handled=false` claims are retained
  as evidence and are not imported or replayed into the new queue.
- Queue health is content-free and part of self-check: it reports overdue queued work, expired
  preparation, `brain_running`, `sending`, `review`, exhausted delivery retries and failed rows.
- The worker uses the existing shared Hermes run-slot limiter through the existing brain runtime;
  this decision creates no public endpoint, capability or additional heavy-process capacity.
- A feature flag permits immediate rollback to the previous handler only while the database
  migration remains additive. Production acceptance must keep the durable path enabled.

## Alternatives considered

- Keep daemon threads and only change the dedupe error to fail closed: rejected because task
  comments can still disappear after HTTP 200 and prepared answers still cannot be delivered alone.
- Retry every interrupted job: rejected because a terminated Hermes turn may already have called a
  mutating MCP tool, and a timed-out Bitrix send may already be visible.
- Treat HTTP timeout as a known failure: rejected because the provider outcome is unknowable.
- Reuse the ordinary task-sync queue: rejected because task synchronization is safely repeatable,
  while an agent brain/tool/delivery turn requires a no-replay boundary and stored output.

## Consequences

- During a PostgreSQL outage Bitrix receives a retryable error instead of an immediate but unsafe
  success. This is intentional backpressure.
- A small class of interrupted jobs requires review rather than automatic completion. This trades
  possible operator work for protection against duplicate messages and business actions.
- Message content already required for agent processing remains in PostgreSQL according to the
  existing conversation retention model; OAuth/access/refresh/application tokens are never stored
  in the intake payload.
- The additive queue requires cleanup and observability, plus explicit migration/rollback steps.

## Verification / revisit trigger

Verify duplicate delivery, database capture failure, restart before brain, restart during brain,
stored-answer delivery-only retry, ambiguous provider send, task-comment self-loop prevention,
multi-part chat batching, concurrent worker claims, queue monitoring, unchanged profile authorship
and a controlled production round trip. Revisit if Bitrix adds a native idempotency key for bot
messages/comments or the worker topology changes.
