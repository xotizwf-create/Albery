# CHG-20260813-15: Hermes restart and production-tree hygiene

- Status: implemented_local
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

Production steps remain:

1. Push the isolated implementation and require tests/security CI.
2. Back up the live unit and affected root artifacts.
3. Apply the policy, run `systemctl daemon-reload`, verify the unit and restart only at the empty gate.
4. Assert effective restart interval/start limit, active scheduler-only gateway, healthy Albery roles,
   full deploy smoke, self-check and clean fresh journals.
5. Confirm production Git has no tracked drift and only explicitly classified ignored runtime files.

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

- Progressive native backoff is unavailable in systemd 249. The target uses a safe fixed delay and
  rate limit; upgrading the OS/systemd is a separate host-hardening decision.
- Full customer-channel acceptance remains a separate owner-approved workstream.
- The spool contained 122 mode-`0600` files (about 6.3 MB); its oldest file was about 14.85 days old
  despite a seven-day code retention target. Cleanup is currently opportunistic on a later upload.
  No file is removed in this infrastructure change; lifecycle/references are an explicit IU audit item.
