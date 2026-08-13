# ADR-0007: Exhaustive MCP policy and fail-closed profile caps

- Status: accepted
- Date: 2026-08-13
- Owners: Albery engineering
- Extends: [ADR-0003](ADR-0003-private-per-agent-mcp.md), [ADR-0004](ADR-0004-durable-conflict-safe-agent-automations.md)

## Context

Private loopback transport prevents an outsider from reaching MCP, but it does not prove that an
authenticated profile has appropriate semantics. Before this decision, automation safety inferred
read-only behavior from name prefixes, several consequential tools had no uniform confirmation
schema, and a missing agent manifest restored the legacy broad registry. In particular, a profile
in `tools_mode='max'` automatically received every tool added by a future deploy.

At the same time, Albery intentionally uses individual on/off switches rather than shared fixed
presets. The hard cap must therefore block unreviewed future capabilities without replacing the
owner's per-agent switches.

## Decision

- Maintain an exhaustive versioned policy for all regular and profile self-service MCP tools.
  Every name has a data domain, effect class, confirmation rule, sensitivity flag, automation
  effect-ledger flag and business-object-lock flag.
- An unknown name is mutating by default and cannot pass registry validation until reviewed.
- Consequential operations expose and enforce one central `confirm=true` contract before their
  handler, provider call or business lock. Handler-specific checks remain defense in depth.
- Every agent manifest contains an explicit `tools` upper bound. A missing, malformed or legacy
  manifest resolves to an empty cap, never to the registry.
- Operational profiles use a dated snapshot of the currently reviewed registry as their maximum;
  their exact effective set remains the individual DB switches intersected with this cap. Thus a
  current toggle continues to work, while a tool introduced tomorrow is off until reviewed.
- Customer runtimes remain explicit zero-tool profiles.
- A newly created agent starts in DB `base` mode but must first persist the current reviewed cap.
  If this file write fails, the DB row is deactivated and no Bitrix/Hermes connector is created.
- Deploy smoke and continuous self-check treat missing, unknown or unexpectedly empty operational
  caps as failures.

## Consequences

- Adding a tool now requires an intentional policy and cap update; CI fails on unclassified names.
- Losing a manifest causes a visible safe outage for that profile rather than privilege widening.
- Current production grants are unchanged by the initial rollout; role-level narrowing remains an
  explicit business decision based on the exact grant matrix and usage evidence.
- `confirm=true` is an accidental-action guard, not cryptographic proof of human presence. Provider
  authorization and domain-specific identity checks remain mandatory.
- Self-service mutations cannot run from scheduled automations; they are not placed in the
  automation effect ledger. Their delete paths still require explicit confirmation.

## Verification / revisit trigger

Verify policy/registry equality, all confirmation schemas and dispatch refusals, exact before/after
tool sets for every active profile, missing-manifest fail-closed behavior, new-agent cap ordering,
private `tools/list`, deploy smoke and continuous self-check. Revisit when tool approval moves to a
separate signed control plane or when role-specific caps are approved.
