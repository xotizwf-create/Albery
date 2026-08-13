# CHG-20260813-17: MCP capability and agent-rights audit

- Status: approved
- Date opened: 2026-08-13
- Related decisions: [ADR-0003](../decisions/ADR-0003-private-per-agent-mcp.md), [ADR-0004](../decisions/ADR-0004-durable-conflict-safe-agent-automations.md)
- Bitrix engineering task: pending; no external task mutation is approved yet

## Goal

Prove that the private per-agent transport is also semantically least-privileged: every registered
MCP tool has a known data domain and risk class, and each of the nine active agents receives only the
capabilities required by its role with correct confirmation, idempotency and concurrency handling.

## Before

- MCP is loopback-only, header-authenticated and scoped per active profile.
- Deploy smoke proves exact connector/tool-set calculation and rejects public/path-token access.
- The global registry contains roughly 160 tools, but no canonical human-readable matrix currently
  proves the safety metadata and effective grant of every tool/profile combination.
- Unknown automation tools fail closed as mutating, but live-agent semantic least privilege and
  domain-specific dangerous combinations have not been audited end to end.

## Target / after

- Versioned inventory of every registered tool: domain, read/write class, external effect,
  confirmation, idempotency/effect fingerprint, business-object lock and secret/PII sensitivity.
- Exact production matrix for all active agent slugs, derived from DB switches intersected with the
  manifest rather than inferred from UI labels.
- Automated gates that fail when a new tool lacks classification or when an agent receives a
  capability outside its reviewed maximum.
- Safe remediation for any overbroad grant or missing mutation guard, rolled out per the normal
  backup/CI/empty-work procedure.

## Changed boundaries and files

Initial phase is read-only across `mcp/`, agent manifest/configuration, production capability tables
and generated connector sets. Any remediation will be added here before implementation; a new ADR is
required if the capability model or public/private boundary changes.

## Safety and privacy

- Inventory uses `tools/list`, source metadata and aggregate profile grants only; it does not call a
  business tool or expose credentials, customer content or personal conversations.
- Production mutations, tool invocations and agent-right changes are prohibited during discovery.
- Profile labels may be recorded; immutable employee identifiers and tokens are not.

## Verification plan and evidence

1. Enumerate registry definitions and compare source, manifest cap, database switches and all nine
   private production `tools/list` results.
2. Classify every tool mechanically where possible and manually review every mutation/unknown.
3. Test for unclassified tools, overbroad grants, missing confirm/idempotency/object-key metadata,
   duplicate names and schema drift.
4. Implement focused hardening only after the exact findings and rollback are recorded.
5. Run full local regression, PostgreSQL 14/16 and security CI, then production negative/structural
   acceptance without writing live business data.

## Risks

- A name-based classifier can label a dangerous tool as read-only.
- Removing a legitimately required grant can make an employee agent silently less capable.
- A tool can be individually safe but dangerous in combination with another read/write capability.
- `tools/list` proves exposure, not correct external behavior; domain audits remain required later.

## Rollback

Discovery has no rollback. Any later manifest/permission change will preserve the pre-change DB rows,
config and commit, and will be reverted per profile at an empty-work gate.

## Known gaps and follow-up

This audit covers agent capability exposure and generic mutation safety. Bitrix/Zoom/Google/WB
provider correctness, prompt injection and data retention remain separate roadmap workstreams.
