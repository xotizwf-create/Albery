# CHG-20260814-25: Audit Zoom, Google and Wildberries connector resilience

- Status: in_progress
- Date opened: 2026-08-14
- Related decisions: [ADR-0004](../decisions/ADR-0004-durable-conflict-safe-agent-automations.md), [ADR-0007](../decisions/ADR-0007-exhaustive-mcp-policy-and-fail-closed-caps.md)
- Approval: owner approved roadmap items 1, 3 and 4 on 2026-08-14
- Bitrix engineering task: pending

## Goal

Audit Zoom, Google and Wildberries end to end for token expiry/API drift, quotas and rate limiting,
timeouts, retry/idempotency, partial writes, durable recovery and content-free monitoring; fix each
confirmed defect without replaying an ambiguous external effect.

## Before

- All three providers are operational and production smoke checks basic credentials/reachability.
- Previous VPN recovery restored model-backed Zoom/automation flows.
- One Wildberries automation exceeded the 300-second brain limit on 2026-08-14, then recovered via
  durable retry and delivered exactly once; connector-level cause and safe degradation remain to be
  audited.
- The roadmap has not yet marked this workstream audited end to end.

## Target / after

- Versioned inventory of every connector/auth path, schedule, read/write operation and durable
  boundary.
- Bounded retries only for classified safe failures, honoring provider rate-limit signals and never
  recomputing/replaying an ambiguous write.
- Atomic token/state publication, observable freshness/error states and operator recovery for each
  provider.
- Deterministic failure-injection tests plus read-only production probes; real writes use isolated
  reversible objects only when separately necessary and safe.

## Changed boundaries and files

- `zoom.py`, Zoom dispatch/scheduler integration and related tests.
- `gdrive.py`, company Drive/Novinki/Docs/Sheets paths and related tests.
- `app.py`, `mcp/context_server.py`, `novinki_watch.py` and migration `088` for private,
  idempotent object creation and a durable Sheet -> Bitrix task -> cleanup stage machine.
- `wb_cabinet.py`, WB sync/backfill/automation paths and related tests.
- Self-check/smoke/health helpers when confirmed blind spots exist.
- Audit roadmap, current architecture and master diagram after production verification.

## Safety and privacy

Discovery and initial provider probes are read-only. Do not publish documents, change sharing,
modify WB data, create Zoom objects or message employees during audit. Credentials and provider
payload contents never enter Git or audit output. Any necessary mutation gets an exact isolated
target, backup/cleanup and an explicit sub-boundary before execution.

## Verification plan and evidence

Map code, database and schedules; inspect sanitized production counters/ages and provider health.
Add deterministic tests for 401/403, 404 drift, 429/Retry-After, 5xx, timeout/connection ambiguity,
pagination, partial writes, duplicate triggers and restart recovery. Run focused/full local tests,
CI/security, protected deployment, provider read probes, smoke/self-check and fresh journals.

## Risks

- A generic retry can duplicate an external write.
- Provider pagination/API changes can silently omit business data.
- Token refresh races can invalidate a valid credential.
- Aggressive live probes can consume quotas or expose private provider data.

## Rollback

Each provider fix must remain separately reversible. Back up code/config and any targeted database
state before deployment; use exact pre-change commit/config, stop only affected schedules at an
empty-work gate, and never delete durable error/review evidence to make monitoring green.

## Known gaps and follow-up

Read-only production probes on 2026-08-14 confirmed Zoom OAuth/API and Google Drive API access,
fresh Zoom/Drive stores, successful current WB syncs and a WB token expiring in January 2027. Static
audit confirmed three remediation boundaries: Zoom must preserve the previous transcript on a
partial download and reclaim expired processing leases; Drive deletion must require an explicit
complete listing and refreshed OAuth state must publish atomically; versioned Apps Script sources
must not contain reusable sync/webhook secrets. Local remediation and failure-injection tests are in
progress. No provider write, secret rotation or production deployment has run yet. Python 3.10 also
becomes unsupported by current Google client releases after 2026-10-04 and is recorded as a host
upgrade follow-up.

The Google permission inventory read only permission types/roles, not object names, ids or content.
Of 179 objects visible to the dedicated OAuth identity, 131 have an `anyone` permission: 113 files
and 15 folders are public writers, and three files are public readers. This is consistent with the
old default that automatically granted `anyone/writer` on document, sheet and folder creation.
Local target behavior is private/inherited access by default; any public share becomes a separate
exact-confirmation operation, defaults to reader and still permits explicit writer when approved.
Existing grants are not changed without an owner-approved migration policy because revocation can
break live business links. The first permission-inventory probe accidentally repeated its first
read-only page for about 90 seconds due to a missing pagination break; it was terminated, made no
provider mutation, and the corrected probe completed in 13 seconds.

The Novinki pipeline had a separate partial-write defect: sheet creation, Bitrix task creation and
source cleanup were three unjournaled effects, and cleanup permanently deleted the Drive object
after any `removeParents` exception. Local migration `088` adds a content-free durable run keyed by
the exact source-id snapshot. Sheet creation uses an app-property idempotency key; the private sheet
grants writer only to the active responsible employee's exact directory email. `task_sending` is a
no-replay boundary and an interrupted/failed call becomes `review`; source cleanup is resumable and
only removes the watched parent after a current-parent read, never deleting the object. Focused
connector/MCP/migration tests pass `82/82`; full regression must be rerun after this final layer.

The post-remediation local suite passed `2006` with 48 environment-only skips. Current-tree secret
search no longer finds the exposed Apps Script sync or webhook values. This does not sanitize Git
history and does not rotate the still-live values; both become harmless only after the correct Apps
Script owner publishes the property-backed version and the server/Script values are rotated as one
gated operation.
