# CHG-20260812-12: Complete automation acceptance and system-cron safety

- Status: implemented_local
- Date opened: 2026-08-12
- Related decisions: [ADR-0004](../decisions/ADR-0004-durable-conflict-safe-agent-automations.md)
- Bitrix engineering task: pending

## Goal

Prove the durable automation runtime with a reversible production scenario, inventory every legacy
`kind='system'` job, and bring any heavy agent-like work under an explicit safe execution model.

## Before

The durable automation code is deployed and test-covered but lacks controlled live business
acceptance. Automation 59 has no successful post-recovery write observation; automation 36 retains
an intentionally unreplayed failed delivery. Legacy system cron rows are outside the durable worker.

## Target / after

- Run-slot contention, atomic Run now, durable stages, write-effect deduplication and delivery-only
  retry are observed on reversible production test objects.
- Every system cron is classified as deterministic/light, heavy agent-like, or retired. Heavy
  agent-like work is migrated to the shared durable limit; deterministic jobs receive explicit
  idempotency/locking contracts.
- Automation 59 is observed on a controlled reversible write or a normal successful scheduled run.
- Automation 36 remains unreplayed unless the owner separately approves its exact recipient and
  payload; its historical error is not falsified.

## Changed boundaries and files

Inventory first; expected automation worker, system cron definitions/wrappers, tests, smoke and
architecture records. No production row will be triggered until its object and visible outcome are
fully identified.

## Safety and privacy

Use dedicated reversible test objects and remove them after acceptance. Back up affected tables.
Never send a missed employee report or mutate live WB/Sheet data without exact preflight and
explicit approval of the visible effect.

## Verification plan and evidence

Inventory production definitions and service timers; test concurrency/restart/retry locally and in
CI; run one reversible production automation through all durable stages and verify cleanup.

Read-only inventory and local implementation evidence on 2026-08-12:

- The only heavy legacy system job is Hermes cron `zoom-to-tasks` every five minutes. It is a
  no-agent script with its own `flock`, timeout and domain state. All other current `kind='system'`
  rows are deterministic in-app or crond orchestration and do not start an unbounded Hermes turn.
- A reviewed allowlist wrapper now acquires the same PostgreSQL run slots as live and durable agent
  turns, refuses the unsafe process-local fallback, enforces a 900-second timeout and executes no
  arbitrary command. The reviewed watchdog checksum is pinned; untracked script drift fails closed.
- A versioned Hermes shim and deploy-smoke assertion make the cron binding observable. Focused
  busy/fallback/release/drift tests pass, including proof that the job never starts without a
  globally enforceable slot.
- Normal production evidence already closed the old business gates without replay: automation 36
  completed and delivered its 09:00 run on 2026-08-12; automation 59 completed and delivered its
  14:30 run. No missed employee result was resent.

## Risks

An external API may accept a write before Albery records success. A test may accidentally target a
live object. Moving deterministic jobs into a heavy lane can delay schedules unnecessarily.

## Rollback

Stop new claims with the existing feature flag, wait for leases, restore code/config/table backups,
and retain durable run/effect history for forensics.

## Known gaps and follow-up

CI and production cron rebinding remain pending. A reversible private-MCP effect-ledger probe is
still required after deployment; no live business object or employee delivery may be used for it.
