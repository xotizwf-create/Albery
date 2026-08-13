# CHG-20260813-19: PostgreSQL and disaster-recovery audit

- Status: implemented_local
- Date opened: 2026-08-13
- Approval: the owner approved execution of the full audit roadmap and required every step and
  decision to be recorded

## Goal

Demonstrate that Albery's PostgreSQL state can be operated and recovered safely: schema ownership,
growth, retention/PII, performance, backup completeness, local/offsite freshness and a real isolated
restore all have measured evidence and a named rollback/recovery procedure.

## Scope

- PostgreSQL version/configuration, database/role exposure and connection envelope;
- schema/migration parity, constraints, indexes, table/index growth, dead rows and long activity;
- PII-bearing durable/runtime tables and explicit retention/deletion contracts;
- local and offsite backup scripts, schedules, permissions, freshness, failure visibility and
  cryptographic identity where practical;
- isolated restore drill outside the production database, including integrity queries and measured
  RPO/RTO;
- safe remediation of discovered gaps through the normal backup, tests, CI and production gates.

## Safety boundary

Discovery is read-only. No production table is rewritten, vacuumed, reindexed or restored. A restore
drill may create only an explicitly named disposable database/environment after capacity is proven;
it must never reuse production connection variables and must be cleaned up only after its exact
resolved target is validated. Secrets, row contents and unnecessary personal data are excluded from
the audit record.

## Required evidence

1. Exact production database and largest-table/index inventory without row content.
2. Backup schedule/script/source/destination inventory and latest local/offsite artifact metadata.
3. Schema-only and full isolated restore result, or a precisely documented resource blocker.
4. Integrity checks on restored schema/data, measured backup age (RPO) and restore time (RTO).
5. Remediation, regression, protected backup and rollback evidence for every change.

## Findings

### Production database

- PostgreSQL `14.23`, database `albery`, application role `albery_app`; the application role is a
  non-superuser and PostgreSQL listens on localhost. Passwords use SCRAM and SSL is enabled.
- Logical footprint at inspection: `3,841,969,499` bytes, 185 public base tables, 905 indexes and
  576 partitions. There were no long transactions, idle-in-transaction sessions, invalid
  constraints, deadlocks or PostgreSQL error-journal entries in the inspected window.
- Largest relations were `wb_finance_details` 2.70 GB, `wb_orders` 299 MB,
  `wb_paid_storage` 230 MB, `wb_sales` 223 MB, `bitrix_task_snapshots` 105 MB and
  `wb_prices_current` 90 MB. The restored logical copy was 3.048 GB, about 794 MB smaller than the
  live physical database; finance alone accounted for about 615 MB of that difference. This is a
  maintenance/capacity finding, not authorization for `VACUUM FULL` or a live rewrite.
- Autovacuum is enabled. The large WB relations retain old planner estimates and have never crossed
  their default vacuum/analyze thresholds since statistics reset. Table-specific tuning must be
  evaluated with the 2 GB capacity envelope rather than applied blindly.
- `effective_cache_size=4GB` exceeds physical RAM and 98,515 sessions were recorded as abandoned
  since the 2026-08-02 statistics reset. Pool/lifecycle and tuning belong to the capacity audit.
- Page checksums are off; `archive_mode` is off; there are no replicas or replication slots. Albery
  therefore has nightly recovery, not point-in-time recovery.

### Data lifecycle

- The customer funnel has an implemented 30-day retention sweep that protects live queue/outbox
  dependencies. Generated attachment/export files also have bounded cleanup.
- There is no project-wide retention contract for employee chat partitions, Bitrix interaction and
  decision history, Zoom transcripts or Wildberries history/finance data. These tables can contain
  personal or commercially sensitive data. A blanket cleanup was deliberately not invented during
  this audit: legal/business retention periods and deletion semantics must be approved per domain.

### Backups before remediation

- Local cron ran daily at 03:15 and kept ten dumps. Latest inspected artifact was
  `albery_20260813_031501.dump`, 255,991,381 bytes, private `0600`, about 12.6 hours old; its
  SHA-256 was `a37e9e3957106a7f1e091ce9e994a10294ced61093e71e55f46ccb0c511d42f5` and
  `pg_restore --list` returned 2,192 TOC lines.
- Offsite cron ran at 03:45. Server 217 held the same exact bytes/SHA and accepted
  `pg_restore --list`, but retained only one dump. Its 15 GB filesystem was 95% used with about
  777 MB free. Roughly 166 MB of deleted files are held by Chrome children of
  `hermes-gateway.service`; no unrelated service was restarted as part of this database audit.
- The local job wrote directly to `.dump` and did not validate it. Offsite used MD5 and cron logs
  only. Self-check did not inspect backups. The restore helper targeted live `DATABASE_URL` with
  `--clean --if-exists`, which made a routine command capable of destroying production.

## Implemented remediation

- Local backup is single-instance (`flock`), writes a private `.partial`, validates with
  `pg_restore --list`, writes an SHA-256 sidecar and atomically publishes both. Failed/stale partials
  cannot masquerade as completed dumps.
- Offsite transfer validates the local sidecar, writes a remote `.partial`, checks remote SHA-256
  and remote archive readability, atomically publishes and stores a private verified-status JSON.
  Remote filenames are strictly allowlisted before any cleanup command.
- Albery self-check now alerts on stale/missing local or offsite evidence, unsafe permissions,
  malformed sidecars, unreadable archives and stale partial files. It avoids repeatedly hashing the
  large file every five minutes.
- `restore_postgres.sh` only creates a new `albery_restore_*` database, refuses an existing/production
  target, uses `--exit-on-error`, validates contents and runs `pg_amcheck` when available. The real
  production process is documented in [the recovery runbook](../runbooks/POSTGRES_RECOVERY.md).
- Architecture invariant is recorded in
  [ADR-0008](../decisions/ADR-0008-verified-postgresql-backup-chain.md).

## Recovery drill evidence

- The offsite artifact was copied directly into a private WSL Linux directory; SHA-256 matched.
- Full restore into PostgreSQL 16 took 67 seconds. It produced a 3.048 GB database with 185 base
  tables, 905 indexes, 576 partitions, four extensions, 939/939 validated constraints and clean
  `pg_amcheck`. Representative exact row counts included 990,829 finance rows, 188,045 orders,
  359,510 task snapshots, 40,533 Zoom transcript segments and 21 automations.
- The new production-shaped backup script then created a 256,368,123-byte atomic dump from that
  restored database in 32 seconds; its sidecar verified. The new restore helper restored that dump
  into another isolated database in about 60 seconds, returned 185 tables and passed `pg_amcheck`.
- Measured offsite download was about 7m26s. Demonstrated data-availability time is therefore about
  8m33s after artifact selection, excluding human incident decision, package installation and live
  traffic cutover. Worst-case scheduled RPO remains approximately 24 hours.
- Both disposable databases and both private local dump copies were removed after their exact names
  and resolved paths were validated. Source production/offsite backups were not modified.

## Tests

- Focused backup/restore/health tests: `24 passed`.
- Changed Python files: `pyflakes` clean; Python compile and Linux shell parse passed.
- Full local regression: `1968 passed, 46 skipped`.
- Practical isolated backup -> SHA-256 -> restore -> `pg_amcheck` cycle passed.

## Known gaps

- PITR, replication and page checksums are not present. They require a separately sized destination
  and maintenance plan; server 217 cannot safely receive WAL in its current state.
- Offsite has one copy and 95% disk use. Capacity expansion/object storage is P1 follow-up; retention
  must not be increased on the current disk.
- Domain retention/erasure policy, WB bloat maintenance, connection pooling and PostgreSQL memory
  tuning remain explicitly routed to privacy/capacity workstreams.
- Production deployment, CI and the first verified natural backup cycle are still pending; this
  record must not become `verified` before those gates pass.

## Rollback

Before deployment, protect the changed scripts/self-check in a mode-`0700` timestamped code backup.
Rollback restores those exact files, removes only the newly created status metadata directory after
validating its resolved path, and needs no database rollback because this change performs no schema
or row mutation. Do not roll back a newly produced `.dump`; completed backup artifacts remain valid.
