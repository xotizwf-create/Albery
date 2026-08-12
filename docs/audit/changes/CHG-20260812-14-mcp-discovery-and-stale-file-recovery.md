# CHG-20260812-14: Scope MCP discovery and recover stale generated-file requests

- Status: deployed
- Date opened: 2026-08-12
- Related decisions: [ADR-0003](../decisions/ADR-0003-private-per-agent-mcp.md), [ADR-0006](../decisions/ADR-0006-channel-native-artifact-delivery.md)
- Bitrix engineering task: pending

## Goal

Stop one agent turn and the scheduler-only Hermes gateway from connecting to every configured MCP
alias, remove inactive profile aliases, and ensure a request to resend an old generated document is
re-created instead of echoing an expired bearer URL.

## Observed incident

- A 2026-08-12 Bitrix reply-to request for a previously generated contract caused the model to echo
  the expired `mcp.m4s.ru/zoom-export` URL from quoted history. The native adapter correctly failed
  closed, so the employee received an explicit file error instead of the requested attachment.
- At the same time, each Hermes one-shot and the retired-transport gateway discovered all 20 live
  and automation aliases. Production journals show a simultaneous connector storm, 403 responses
  for inactive `albery-ai`, repeated session conflicts and an `automation-agent-main` reconnect
  loop. Self-check therefore repeatedly reported MCP unavailability.
- The MCP role, PostgreSQL and all critical services themselves remained healthy. The failure was
  connector discovery scope, not public reachability or an MCP service outage.

## Target / after

- Every Albery subprocess exports an exact MCP server allowlist derived from its requested private
  connector; built-in toolsets such as `web` do not widen it.
- A versioned, idempotent Hermes patch enforces that allowlist before any connector is opened.
- `hermes-gateway` is explicitly scheduler-only and skips MCP discovery. Its only enabled cron is
  the reviewed `no_agent` Zoom wrapper.
- Materialization retains exactly the active live/automation pairs and prunes inactive managed
  aliases. Deploy smoke fails on extras or a missing scope patch.
- Agent instructions forbid copying any `/zoom-export/` URL from history or quoted messages. A
  resend/edit request must use the stored document context and produce a new export handoff, which
  the channel adapter turns into a native attachment.

## Safety, risks and rollback

- An empty or missing per-turn scope must fail closed, never restore all MCP servers.
- Built-in-only quality/customer turns retain zero MCP servers.
- Back up the Hermes patch target, config and systemd drop-in before deployment. No database schema
  or business data changes are required.
- Rollback restores those exact backups, removes the scheduler-only drop-in and reverts the code
  commit. Do not restore public MCP or the deleted legacy export route.

## Verification plan

- Unit-test allowlist construction, patch idempotency/filtering, inactive connector pruning,
  scheduler-aware self-check and the no-old-file-URL prompt contract.
- Run compilation, focused tests, full regression, dependency/security checks and CI.
- Deploy fast-forward after backup and empty-inflight/running-automation gates; restart only
  `hermes-gateway`, then the Albery roles only if their Python code requires it.
- Verify a scoped private MCP initialize/tools round trip, no fan-out in journals, self-check dry
  run clean, full deploy smoke and a safe document reconstruction probe without messaging a real
  employee.

## Local implementation evidence

- `python -m py_compile` passed for every changed Python module.
- Focused MCP/file/self-check suite: `34 passed`.
- Full regression suite: `1934 passed, 44 skipped`; the skips are the documented local-only
  PostgreSQL and LibreOffice cases that run in CI/production-capable environments.
- `git diff --check` passed. Pyflakes reports only three warnings already present in unchanged
  lines of `b24bot.py`; this change introduces no new warning.
- The first production verification showed that Hermes suppresses `INFO` in journald. The
  scheduler-only marker was therefore changed to a flushed stdout marker, so self-check can
  immediately separate pre-restart connector warnings from current state. The full suite remained
  `1934 passed, 44 skipped` after that hardening.
- The incident document predates durable native bytes: its full extracted text remains in the
  scoped attachment record, while the temporary export bytes no longer exist. Recall now checks
  physical byte availability before offering `DELIVER_STORED`; text-only legacy rows are rebuilt
  with `export_document`. Exact old reply recovery is scoped to the same dialog and agent, requires
  an exact prior answer, and accepts only an `agent_doc` row created within 120 seconds of that
  interaction. The incident match is 5.26 seconds and contains 16,061 extracted characters. Final
  local regression: `1936 passed, 44 skipped`.
- No external message, task or business object was created while reproducing the incident.

## Production evidence

- Runtime commits: `05c4607`, `bf933b4`, `24e75a5`, final `a38e2d1`; production and
  `origin/main` are identical and the tracked production worktree is clean.
- Protected backups were created before every production stage under
  `/var/backups/albery/pre-chg14-*`; they contain the previous commit, code archive, Hermes config,
  installed Hermes patch targets and gateway unit/drop-ins.
- All seven critical services are active; ports `5002`, `5003`, `5004` return HTTP 200. Final
  available memory was 1,011 MB, with zero live Bitrix turns and zero running automations.
- Production deploy smoke passed. Self-check dry-run reports zero problems. Hermes config contains
  exactly 18 managed aliases for nine active profiles and neither inactive `albery-ai` alias.
- A real no-action Hermes one-shot requested `agent-main`; the answer completed and MCP journal
  showed only route `main`. The scheduler-only gateway emitted its marker and made no subsequent
  MCP connector attempt.
- The incident's exact prior answer resolves to the same-profile `agent_doc` created 5.26 seconds
  later, containing 16,061 extracted characters. Its old bytes are absent, so production selects
  the explicit rebuild path, forbids `DELIVER_STORED`, and never exposes the expired URL.

## Known gap / acceptance boundary

- The exact binary formatting of this pre-durable legacy document cannot be recovered because its
  temporary bytes no longer exist. Albery can rebuild the content from the retained full text; new
  documents retain exact durable bytes.
- No message was sent to the employee during verification. A user-visible resend remains an
  external action requiring an approved recipient/message preview under the production rules.
- No Bitrix engineering task was created or closed because that external mutation was not
  separately approved in this incident turn.

## Rollback material

- Restore the latest applicable `pre-chg14-*` code/config/vendor files, remove the
  `30-albery-scheduler-only.conf` drop-in if rolling back the gateway boundary, run
  `systemctl daemon-reload`, and restart only at the empty inflight/automation gate.
- Do not restore public MCP routes or legacy `/zoom-export/` delivery.
