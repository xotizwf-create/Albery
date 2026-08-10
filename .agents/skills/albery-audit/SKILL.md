---
name: albery-audit
description: Maintain Albery's versioned architecture audit, ADRs, change records, current-state diagrams, verification evidence, and rollback notes. Use for any non-trivial Albery code, configuration, prompt, model-routing, integration, database, deployment, security, or architecture change, and whenever a user asks what changed, why it changed, or what the current final decision is.
---

# Albery Audit

Keep the repository, production state, and audit records consistent.

## Workflow

1. Read `docs/audit/INDEX.md`, `docs/audit/OPERATING_RULES.md`, `docs/audit/architecture/CURRENT.md`, and related change/decision records.
2. Create or update a `CHG-*` record before implementing a non-trivial change. State the previous behavior, target behavior, risks, rollback, and verification plan.
3. Create an ADR when the change affects a system boundary, provider, data model, security model, public contract, or architectural invariant.
4. Implement atomically. Never record secrets, raw credentials, or unnecessary user content in audit files.
5. Record changed files, tests, live evidence, backup location, rollback path, and known gaps. Distinguish observed facts from assumptions.
6. Update the current architecture and its diagrams when behavior or dependencies change.
7. Append to `docs/audit/CHANGELOG.md`; never rewrite verified history. Supersede or roll back records explicitly.

The work is complete only when code, documentation, deployed behavior, and verification evidence agree. Use [references/audit-map.md](references/audit-map.md) for file roles and statuses.
