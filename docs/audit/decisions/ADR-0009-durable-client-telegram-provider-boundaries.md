# ADR-0009: Durable client Telegram and provider boundaries

- Status: accepted
- Date: 2026-08-13

## Context

The IU customer bot receives untrusted Telegram input, can queue AI replies, hand control to a
manager, attach files and synchronize a Bitrix CRM deal. Telegram and Bitrix message APIs do not
offer a caller-supplied idempotency key. A timeout can therefore mean either "nothing happened" or
"the provider committed the message and its response was lost".

The workspace already persisted raw updates, AI jobs, Telegram outbox rows and CRM actions. The
audit found three gaps around that core: Bitrix manager alerts retried an ambiguous direct call,
outgoing upload cleanup ignored active outbox references and did not run independently, and the
five-minute self-check did not inspect any customer queues.

## Decision

1. Provider delivery is a persisted state machine. Reservation (`leased`) remains retryable;
   immediately before an irreversible call it becomes `sending`; a confirmed provider id becomes
   `sent`; a timeout or interrupted call becomes `unknown` and is never resent blindly.
2. Every active or ambiguous outbox row protects each referenced upload token, regardless of file
   age. Cleanup fails closed when PostgreSQL cannot supply the reference set.
3. Customer queues are continuously monitored using counters and timestamps only. Message text,
   attachment bytes and CRM payloads do not enter alerts.
4. One-time form tokens follow the workspace retention window. Deleted-form recovery snapshots are
   kept for 90 days by default, then their payload is redacted while the merge ledger remains.
5. The public IU bot is the current production customer entry. Telegram Business intake is an
   optional transport and remains intentionally disabled until the owner enables it; its historical
   connection rows are not treated as active delivery paths.

## Consequences

- Ambiguous sends may require a human to check the provider and resolve the row, but cannot create a
  duplicate by automatic retry.
- A stuck active queue can retain a file beyond seven days; self-check makes that condition visible.
- Old recovery snapshots eventually lose their full CRM content by design.
- A real customer/manager round trip remains a separate approval gate; structural verification does
  not authorize a production message.
