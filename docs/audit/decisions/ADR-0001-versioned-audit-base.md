# ADR-0001: Versioned architecture audit base

- Status: accepted
- Date: 2026-08-10
- Owners: Albery engineering

## Context

Architecture facts, deployment evidence, and decisions were spread across code, temporary reports, chat history, production state, and Bitrix tasks. Agents could see only fragments, and a separate mutable database would introduce another source of truth.

## Decision

Use `docs/audit/` in the Albery Git repository as the canonical architecture audit base. Store current architecture, ADRs, end-to-end change records, templates, and an append-only index there. Expose the workflow through repository and runtime skills. Use Bitrix for operational notification and ownership, linking back to the Git record.

## Consequences

- Every record is reviewable, diffable, and tied to code commits.
- Agents with repository access see the same history as engineers.
- Secrets and raw user content must be excluded from Git evidence.
- Runtime telemetry remains in PostgreSQL and journals; the audit stores only references and conclusions supported by those sources.
- No independent PostgreSQL “audit database” is created, avoiding split-brain and schema maintenance.
