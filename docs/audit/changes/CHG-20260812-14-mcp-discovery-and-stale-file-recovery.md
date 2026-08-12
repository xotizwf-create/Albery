# CHG-20260812-14: Scope MCP discovery and recover stale generated-file requests

- Status: implemented_local
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
- No external message, task or business object was created while reproducing the incident.
