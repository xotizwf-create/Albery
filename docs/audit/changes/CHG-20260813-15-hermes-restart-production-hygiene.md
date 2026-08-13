# CHG-20260813-15: Hermes restart and production-tree hygiene

- Status: verified
- Date opened: 2026-08-13
- Related decisions: none
- Bitrix engineering task: pending; creating an external task was not separately approved

## Goal

Remove two already confirmed operational risks before the wider audit: ineffective Hermes restart
backoff on Ubuntu/systemd 249 and sensitive/runtime artifacts appearing as untracked production Git
content.

## Before

Read-only production evidence at 2026-08-13 13:38 MSK:

- server 186 runs systemd `249.11`;
- `hermes-gateway.service` is active with `Restart=always`, effective `RestartSec=5s` and
  `StartLimitIntervalSec=0`;
- `RestartMaxDelaySec` and `RestartSteps` are unknown to this systemd version and are ignored;
- a persistent failure can therefore restart indefinitely every five seconds;
- the tracked production tree matches commit `8cd58ef`, but Git reports untracked artifacts:
  old `.env-backup-*`/`env-backup-*` files, a pre-repair state copy, one old `dist.pre-*`
  frontend rollback directory and `.funnel_outgoing/` runtime bytes;
- `.funnel_outgoing/` is the intentional restricted seven-day spool used by
  `funnel_workspace_uploads.py`, not abandoned source code.

No secret values, filenames of customer documents or file payloads were recorded in this audit.

## Target / after

- Use a systemd-249-compatible policy: fixed delayed restart plus a bounded start limit, with no
  unsupported directives in the effective unit source.
- Make the policy versioned and assert it during deploy smoke so a Hermes upgrade cannot silently
  restore the unsafe behavior.
- Move legacy environment/state backups to a mode-`0700` backup directory outside the web Git
  working tree; do not delete them.
- Keep `.funnel_outgoing/` in place and mode-restricted, but explicitly ignore it as runtime state.
- Ignore local `tmp/` audit backups so they cannot be committed accidentally.

## Changed boundaries and files

Implemented locally:

- `.gitignore`;
- versioned Hermes restart-policy installer/config under `deploy/` and `scripts/`;
- `scripts/deploy_smoke.py` and focused tests;
- production `hermes-gateway.service`/drop-in after an exact backup;
- `docs/audit/*` and the master architecture risk register.

Implementation uses a systemd drop-in for the effective fixed delay/rate limit and a deliberately
narrow installer that removes only the two unsupported assignments from the base Hermes unit. The
installer never restarts the gateway; restart ownership remains with the empty-work deployment gate.
Deploy smoke and recurring self-check both detect effective-policy drift and reappearance of either
unsupported directive.

## Safety and privacy

- No employee message, task, CRM object or agent action is part of this change.
- The gateway may be restarted only after confirming empty Albery in-flight turns and no running
  agent automations.
- Backup files are moved only between validated absolute paths on server 186 and remain recoverable.
- Runtime `.funnel_outgoing/` bytes are neither listed by customer filename nor moved/deleted.

## Verification plan and evidence

Local evidence:

- compilation passed for the installer, deploy smoke and self-check;
- changed-file `pyflakes` and `git diff --check` passed;
- focused restart/smoke/self-check tests: `13 passed`;
- full local regression: `1940 passed, 44 skipped`; PostgreSQL 14/16 and LibreOffice cases retain
  their documented CI/production-capable gates;
- the local `app.py` content hash equals `HEAD`; its modified status is OneDrive/line-ending metadata
  and it is intentionally excluded from this change.

Production evidence on 2026-08-13:

- commits `c2f9acd` and `6cff732` passed tests CI `31695771552` on Python/PostgreSQL 3.10/14
  and 3.12/16 plus frontend lint/build; security CI `31695771565` passed Python and frontend
  dependency audits;
- the first rollout attempt stopped before mutation because one agent automation was running; the
  second waited for `inflight=0`, `running=0` and no gateway child process;
- the pre-pull code backup is `/var/backups/albery/pre-chg15-20260813_143531`; the effective unit,
  artifact and tracked-file backup is `/var/backups/albery/pre-chg15-20260813_143556`, mode `0700`;
- production fast-forwarded from `8cd58ef` to `6cff732`; changed Python compiled. Production pytest
  was intentionally not installed/run on the 2 GB host; both CI database matrices are the test gate;
- the installer removed only `RestartMaxDelaySec` and `RestartSteps`, installed the versioned
  drop-in and passed `systemd-analyze verify` with empty stderr;
- effective properties are `RestartUSec=30s`, `StartLimitIntervalUSec=5min`,
  `StartLimitBurst=5`; `hermes-gateway` is active with `NRestarts=0`;
- 12 classified legacy rollback artifacts moved into the protected backup without deletion. The
  active `.env` and `.funnel_outgoing/` were not moved; the spool remains mode `0700` with all
  existing files intact;
- production Git is clean. All Albery split roles, Telegram worker and gateway are active;
  `scripts/deploy_smoke.py` returned `SMOKE OK`, self-check dry-run and two natural timer runs were
  clean, and fresh error journals for all changed/adjacent services were empty.

## Risks

- An overly strict start limit could leave the scheduler stopped after a provider outage.
- Restarting at the wrong time could interrupt live work.
- Moving an active spool file would break a customer delivery; therefore the spool is never moved.
- A later Hermes package update may replace the base unit; deploy smoke must detect policy drift.

## Rollback

Restore the exact timestamped unit backup, remove the Albery policy drop-in if installed, run
`systemctl daemon-reload`, and restart at the same empty-work gate. Move backup artifacts back only
if an explicitly identified consumer proves it requires their old location; the active `.env` is not
part of the move.

## Known gaps and follow-up

- Progressive native backoff is unavailable in systemd 249. Production now uses a safe fixed delay and
  rate limit; upgrading the OS/systemd is a separate host-hardening decision.
- Full customer-channel acceptance remains a separate owner-approved workstream.
- The spool contained 122 mode-`0600` files (about 6.3 MB); its oldest file was about 14.85 days old
  despite a seven-day code retention target. Cleanup is currently opportunistic on a later upload.
  No file is removed in this infrastructure change; lifecycle/references are an explicit IU audit item.
