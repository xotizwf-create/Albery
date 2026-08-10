# CHG-20260810-02: Resolve nanoid security gate

- Status: implemented_local
- Date opened: 2026-08-10
- Trigger: GitHub Security audit for commit `25e9900`
- Bitrix engineering task: pending deployment verification

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
- GitHub CI: pending.

## Risks

Low: this is a patch-level transitive dependency lock update. No production frontend rebuild is required by this backend deployment because tracked compiled assets are unchanged.

## Rollback

Revert the lockfile commit. This would restore a known high-severity advisory and is not an acceptable steady state.
