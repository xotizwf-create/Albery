# CHG-20260813-19: PostgreSQL and disaster-recovery audit

- Status: approved
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

Pending read-only discovery.

## Rollback

No rollback is needed for discovery. Any later configuration/script change receives its own
pre-change copy and exact restore command before deployment. Production database changes are not
authorized by this initial audit record.
