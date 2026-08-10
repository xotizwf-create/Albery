# CHG-20260810-02: Resolve nanoid security gate

- Status: verified
- Date opened: 2026-08-10
- Trigger: GitHub Security audit for commit `25e9900`
- Bitrix engineering task: 2668; result comment 42738

## Goal

Restore the mandatory security gate after npm published a high-severity advisory for the locked transitive `nanoid` version.

## Before

`Интерфейс/package-lock.json` resolved `nanoid` 3.3.16. GitHub's `npm audit --omit=dev` reported `GHSA-2v37-7h3g-55p8` and failed the deployment gate.

## After (locally implemented target)

The lockfile resolves `nanoid` 3.3.18. Application source and declared dependency ranges are unchanged.

## Verification evidence

- `npm audit --omit=dev`: `found 0 vulnerabilities`.
- `npm run lint`: passed.
- `npm run build`: passed (existing bundle-size warning only).
- GitHub tests and Security audit: two final workflows of each type completed successfully for `0f20709`.
- Production fast-forwarded to `0f20709`; application smoke and health checks passed.
- Closed Bitrix task 2668 contains the advisory, dependency diff, checks, backup, commit, and rollback evidence.

## Risks

Low: this is a patch-level transitive dependency lock update. No production frontend rebuild is required by this backend deployment because tracked compiled assets are unchanged.

## Rollback

Backup: `/var/backups/albery/code/pre-quality-routing-20260810_135802.tar.gz`. Revert `0f20709` only as a short emergency measure; it would restore a known high-severity advisory and is not an acceptable steady state.
