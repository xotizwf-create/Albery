# PostgreSQL recovery runbook

This runbook is for a real Albery database incident. Do not execute it as a routine test.

## Current recovery contract

- Local backup: daily at 03:15, ten-day retention.
- Offsite copy: daily at 03:45, one verified dump on server 217.
- Accepted RPO: up to about 24 hours; there is no PITR/WAL archive.
- Last measured drill on 2026-08-13: offsite download about 7m26s, restore about 60-67s, 185
  tables/905 indexes/576 partitions, 939 validated constraints and clean `pg_amcheck`.

## Triage before any restore

1. Confirm the incident is data corruption/loss rather than an application, pool, disk or OAuth
   failure. Preserve PostgreSQL and application journals.
2. Record the exact latest local and offsite dump names, sizes, SHA-256 values and timestamps.
3. Run `pg_restore --list` and verify the SHA-256 sidecar. Never select a `.partial` file.
4. Prefer an isolated restore first:

   ```bash
   sudo /var/www/albery/scripts/restore_postgres.sh \
     /var/backups/albery/postgres/albery_YYYYMMDD_HHMMSS.dump \
     albery_restore_incident_YYYYMMDD
   ```

5. Inspect the isolated database and run application-level integrity queries. Do not point live
   services at it yet.

## Production replacement gate

Production replacement requires all of the following before commands are prepared:

- incident owner explicitly approves the exact dump and acknowledged data-loss window;
- current database receives a separate protected emergency dump if it is readable;
- Bitrix in-flight turns and running automations are empty or deliberately drained;
- all Albery writers are stopped and maintenance mode is visible;
- active DB sessions are identified and terminated only after the services are stopped;
- exact rollback database/dump and code commit are recorded.

The production commands are intentionally not embedded in the routine helper. A reviewed incident
operator prepares them for the exact database/server at the time of the incident. Required order:

1. stop/drain all writers;
2. preserve the old database under an explicit rollback name or emergency dump;
3. create a fresh production database with the correct owner and locale;
4. restore with `--exit-on-error --no-owner --no-acl`;
5. apply idempotent schema ensure/migrations;
6. run constraints, table/index inventory, `pg_amcheck`, `/healthz` and deploy smoke;
7. restart only through the empty-window safe-restart process;
8. observe journals, queues and one approved no-write/read scenario before reopening traffic.

## Failure handling

- A failed isolated restore is automatically removed by the helper; the source dump is retained.
- A SHA/size mismatch or failed `pg_restore --list` means the artifact is not a backup and must not
  be used.
- If both local and offsite copies are stale, record the actual RPO before requesting approval; do
  not imply that newer data can be recovered.
- Never enable WAL archiving to server 217 in its current 15 GB/95%-used state.
