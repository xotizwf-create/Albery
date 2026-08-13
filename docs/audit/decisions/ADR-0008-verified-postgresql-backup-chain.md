# ADR-0008: Verified PostgreSQL backup chain and isolated restore by default

- Status: accepted
- Date: 2026-08-13

## Context

Albery keeps ten nightly custom-format PostgreSQL dumps locally and one copy on server 217. The
artifacts existed, but the local job wrote directly to the final filename, neither job proved
`pg_restore` readability, offsite identity used MD5, self-check did not watch either chain, and the
one-argument restore helper ran `pg_restore --clean` directly against `DATABASE_URL`. A disk-full
event had already left offsite backup stale for eighteen days without an alert.

## Decision

1. A dump is complete only after `pg_dump` writes a private `.partial`, `pg_restore --list` accepts
   it, SHA-256 is recorded in a private sidecar, and both files are atomically renamed.
2. Offsite transfer uses a private `.partial`, validates remote SHA-256 and remote
   `pg_restore --list`, atomically publishes it and records a root-only local verification status.
3. Five-minute self-check validates freshness, permissions, archive readability, status parity and
   stale partials. It does not repeatedly hash the full dump; hashing belongs to the daily producer
   and transfer jobs.
4. The standard restore helper may create only a new `albery_restore_*` database. It never accepts
   the production database and never uses `--clean`. A production disaster restore is an explicit
   maintenance-window runbook with human confirmation.
5. Current nightly dumps define the accepted recovery tier: worst-case RPO is approximately 24
   hours. PITR is not claimed until WAL archiving has a separately sized, monitored destination.

## Consequences

- A final `.dump` means it was locally readable and hashed; a successful offsite status means the
  exact remote bytes were also readable.
- Failure becomes visible within five minutes instead of relying on someone reading cron logs.
- Routine operators cannot accidentally erase the live database with a drill command.
- Offsite capacity remains a known limitation: the 15 GB receiver holds one dump and is 95% full.
  Increasing retention or enabling WAL shipping is forbidden until storage is expanded or replaced.

## Rejected alternatives

- Hashing every dump on every self-check tick: unnecessary continuous I/O on a 2 GB host.
- Enabling WAL archiving to the existing receiver: it has insufficient capacity and would create a
  new outage mode.
- Keeping the destructive one-argument restore helper: convenience does not justify an implicit
  production target.
