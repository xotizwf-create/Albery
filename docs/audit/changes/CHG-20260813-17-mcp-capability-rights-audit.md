# CHG-20260813-17: MCP capability and agent-rights audit

- Status: verified
- Date opened: 2026-08-13
- Related decisions: [ADR-0003](../decisions/ADR-0003-private-per-agent-mcp.md), [ADR-0004](../decisions/ADR-0004-durable-conflict-safe-agent-automations.md), [ADR-0007](../decisions/ADR-0007-exhaustive-mcp-policy-and-fail-closed-caps.md)
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

Discovery was read-only across `mcp/`, agent manifest/configuration, production capability tables
and generated connector sets. The approved remediation now changes these boundaries:

- `mcp/tool_policy.py` is the exhaustive semantic policy for 160 regular and six self-service tools;
- `mcp/context_server.py` applies policy schemas and rejects consequential calls centrally;
- `shared/automation_safety.py` uses policy effects instead of read-looking name prefixes;
- `agent_knowledge.py`, every `agent_knowledge/agents/*.yaml` and new-agent creation make tool caps
  mandatory and fail closed;
- `scripts/deploy_smoke.py` and `scripts/albery_selfcheck.py` watch policy/cap drift;
- [MCP capability inventory](../inventories/MCP_CAPABILITIES.md) describes every function;
- [production grant matrix](../inventories/MCP_AGENT_GRANTS.md) records every effective profile/tool
  combination observed before rollout.

## Findings

- Production exposed exactly 160 regular tools plus six per-profile self-service tools.
- All nine private connectors exactly matched their calculated DB/manifest set: counts were 110,
  137, 166, 109, 0, 0, 116, 141 and 20 in slug order recorded by the matrix.
- Seven operational manifests had no `tools` cap. Two active slugs had no same-name manifest at all.
  `agent-razrabotchik` used `max`, so a future registry addition was an automatic grant.
- The old automation classifier used read-looking prefixes. It safely over-serialized
  `workspace_get_*`, but a future mutation with a read-looking name could bypass the intended lock.
- Several administrative, permission-changing and external-communication tools did not advertise
  or uniformly enforce confirmation. Delete tools were centrally rejected by name, but four delete
  schemas could not tell the model how to confirm.
- Current role grants are broad: finance, lawyer, main and marketplace profiles expose 109-141
  tools. They are now exactly documented and protected against future auto-grant, but aggressive
  role narrowing was not guessed because it could silently remove legitimate employee workflows.

## Implemented behavior

- 166/166 names are classified: 77 read, 55 ordinary write, 17 privileged configuration,
  11 destructive and six local-artifact writes across explicit data domains.
- 47 consequential tools now require `confirm=true` in both model-visible schema and dispatcher.
  This includes destructive, agent/permission, organization, Telegram send/join, contract/terms,
  public Drive sharing and management-dispatch operations.
- Unknown tools fail closed as mutating. All regular writes keep the cross-process object lock; an
  automation write additionally keeps its per-run effect fingerprint/ledger.
- Every versioned agent YAML has an explicit cap. Seven operational caps freeze the current
  166-name reviewed maximum, while both customer runtimes remain empty. Individual DB switches still
  determine the exact active subset, so the constructor remains on/off rather than fixed presets.
- Missing/malformed caps resolve to zero. New-agent creation writes the reviewed snapshot before
  external connectors and deactivates the row if that step fails.

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

### Verified evidence

- Production read-only inventory: 160 regular + six self tools; nine direct `tools/list` results
  exactly matched their calculated sets. No business tool was invoked.
- Pre-deploy production simulation applied the new caps to the live DB modes/lists: all nine profiles
  reported `before == after` with the same counts.
- Focused policy/manifest/HTTP tests: `100 passed`.
- Full local regression: `1955 passed, 44 skipped`; PostgreSQL-marked tests remain for CI.
- Python compile and `git diff --check`: clean. New policy/inventory files have zero pyflakes issues;
  the unchanged repository baseline findings remain outside this change.
- Implementation commit: `b5da07cf9c1518f2da8c9b4e0d09727fa11de136`. GitHub tests run
  `31698926291` passed frontend plus PostgreSQL 14/Python 3.10 and PostgreSQL 16/Python 3.12;
  security run `31698926377` passed.
- Production was clean at prior commit `7c6040a`; no Bitrix turn, automation run or shared heavy
  slot was active. The full source tree was backed up under
  `/var/backups/albery/pre-chg17-20260813_152041` with directory mode `0700`.
- Both shared heavy slots were held during the empty-work restart, closing the race between drain
  inspection and systemd. The four affected services returned active; production Git stayed clean.
- All nine post-deploy private `tools/list` sets exactly matched DB switches intersected with the
  new manifest cap, preserving counts `110/137/166/109/0/0/116/141/20`.
- Negative live calls for `delete_agent`, `send_telegram_message` and
  `delete_my_instruction` omitted `confirm`; all three were rejected before their handlers. No
  agent, message or instruction was changed.
- Deploy smoke, dry-run self-check and generated capability inventory passed; relevant fresh error
  journals were empty. Final acceptance ran on successor production commit `57a215a`, which changes
  only the separately verified healthcheck/audit contour after the CHG-17 runtime commit.

## Risks

- A name-based classifier can label a dangerous tool as read-only.
- Removing a legitimately required grant can make an employee agent silently less capable.
- A tool can be individually safe but dangerous in combination with another read/write capability.
- `tools/list` proves exposure, not correct external behavior; domain audits remain required later.

## Rollback

The prior commit and affected source/manifests are preserved under
`/var/backups/albery/pre-chg17-20260813_152041`. Roll back by returning to the prior commit and restarting
`albery`, `albery-mcp`, `albery-web` and `albery-tg` at the empty-work gate. No DB rows are changed by
this rollout. If only the new confirmation policy causes an incompatibility, revert the code commit;
the cap snapshot can remain because the pre-deploy simulation proves it preserves all current sets.

## Known gaps and follow-up

This audit covers capability exposure and generic mutation safety. Broad role grants are documented
but not silently narrowed; provider-specific correctness and prompt injection remain separate roadmap
workstreams. A real employee request that exercises a newly-confirmed operation is not required for
safe structural acceptance because no external write may be triggered without exact preview/approval.
