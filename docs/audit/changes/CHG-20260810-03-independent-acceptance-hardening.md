# CHG-20260810-03: Independent acceptance hardening

- Status: verified
- Date opened: 2026-08-10
- Trigger: owner's request for an independent end-to-end re-verification
- Related decision: [ADR-0002](../decisions/ADR-0002-codex-reasoning-groq-media.md)
- Bitrix engineering task: 2670; result comment 42760

## Goal

Re-test every path introduced by CHG-20260810-01, correct any discrepancy found by adversarial scenarios, and prove the final behavior on production without employee-visible test artifacts.

## Findings before implementation

1. Task check-in used `bool(value)`, so a malformed model value `"false"` became `True` and could select a task instead of failing closed.
2. Novinki treated a missing or malformed `recommendations` array as a valid empty result. The main flow could then classify the files as processed and delete them.
3. The accepted documentation described Groq as the media contour, but screenshot OCR still defaulted to `codex,groq`. The latest owner decision is Groq-primary media processing with Codex retained as a resilience fallback for vision.
4. The Novinki cron description still named Groq as its recommendation synthesizer.
5. The quality subprocess inherited the complete Albery environment although it needs only runtime/provider variables.

## Target behavior

- Check-in accepts only a real JSON boolean for `help`; malformed rows are ignored.
- Novinki distinguishes a valid empty list from a malformed schema and retains source files on malformed output.
- Screenshot OCR defaults to `groq,codex`; Groq failure falls back to Codex.
- The quality subprocess receives an allowlisted environment without Bitrix, database, Google, or Groq credentials.
- Existing success, no-help, provider-failure, retry, kill-switch, batching, convergence, and no-write paths are covered by tests and production probes.

## Verification plan

- Focused unit matrix for all four paths plus vision routing.
- `pyflakes`, skill/YAML validation, full predeploy regression suite, GitHub DB matrices, frontend, and Security audit.
- Production compile and zero-tool self-check.
- Synthetic JSON/schema/prompt-injection calls; offer help/no-help/provider-down paths; live task check-in dry-run and fail-closed path; Novinki valid/malformed/multi-batch paths; media provider probes.
- Before/after DB counters, run slots, service health, resources, and fresh journals.

## Local implementation and evidence

- The Codex runner now receives an allowlisted environment. Business credentials such as
  `DATABASE_URL`, Bitrix, Google, and Groq secrets are not inherited.
- Check-in accepts `help` only as a JSON boolean and rejects boolean, malformed, or non-positive
  task IDs.
- Task offers reject an invented agent slug and use a deterministic message bound to a real
  fallback agent. An explicit empty offer causes no Bitrix or database write.
- Novinki validates every recommendation object, retains source files on provider/schema failure,
  and permits a hierarchical merge that actually converges on its fourth round.
- Screenshot OCR now defaults to Groq first and Codex second. Audio remains on Groq Whisper.
- Focused adversarial suite: `37 passed`.
- Relevant integration matrix: `91 passed, 5 skipped` (PostgreSQL cases run in CI).
- Full predeploy: `1872 passed, 43 skipped`; compile and production-style imports passed. The
  missing local `DATABASE_URL` background warning is expected and non-fatal.
- The first red test run produced `10 failed, 24 passed`, proving that the new cases detected the
  original defects before the fixes.

## CI and production evidence

- Implementation commits: `8cf9ec6` and dependency-isolation follow-up `f2669ed`.
- The first CI run exposed a test-only dependency on optional `googleapiclient` in Python 3.10;
  the test was isolated with module stubs. The final Security audit, frontend, Python
  3.10/PostgreSQL 14, and Python 3.12/PostgreSQL 16 jobs all passed.
- Pre-change archive:
  `/var/backups/albery/code/pre-acceptance-hardening-20260810_143444.tar.gz`; SHA-256
  `e96f4f09cee76b674bf03f28f27d16ed2f7628e6b278176660c684843113c564`.
- Production fast-forwarded to `f2669ed`; changed modules compiled. The service was restarted only
  after `bitrix_inflight_turns=0` and running automations reached zero.
- One-shot deploy self-check: `tool_count=0`. A synthetic prompt-injection probe returned the
  contracted JSON without tools, and the kill switch stopped before execution.
- Task offers: a synthetic legal task selected the configured lawyer; a physical task produced no
  offer. No Bitrix post occurred.
- Live task check-in dry-run: scanned 86, passed deterministic filters 8, picked 1, writes 0. With
  the quality contour disabled, classification returned an empty fail-closed result.
- Novinki: malformed schema failed closed; synthetic batch/final synthesis returned a valid result;
  live Drive dry-run found the folder empty and made no writes or deletes.
- Media: real Groq-first vision read the synthetic OCR marker without invoking Codex fallback; Groq
  Whisper accurately transcribed a natural synthetic sentence. A separate spelled-number probe
  normalized “four eight two seven” as “V827”, documenting that exact alphanumeric fidelity is a
  provider/model limitation rather than a routing failure.
- Before/after counters stayed `bitrix_task_agent_offers=207` and `task_checkin_runs=21`.
  Final state: inflight 0, running automations 0, heavy slots held 0, five services active,
  `deploy_smoke.py` reported `SMOKE OK`, and the fresh error journal was empty.
- Closed Bitrix task 2670 and result comment 42760 contain the implementation, tests, deployment,
  backup, and rollback evidence.

## Rollback

Use the pre-change code archive and `git revert <implementation-commit>`. `QUALITY_LLM_ENABLED=0`, `B24_TASK_CHECKIN=0`, and `B24_TASK_OFFER=0` remain immediate containment controls.
