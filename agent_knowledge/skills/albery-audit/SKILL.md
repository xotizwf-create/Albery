---
name: albery-audit
description: Read Albery's versioned architecture audit to explain current behavior, prior decisions, changes, risks, and verification status. Use when answering questions about architecture or before planning a non-trivial system change.
---

# Albery architecture audit

Read these sources in order:

1. `docs/audit/INDEX.md`
2. `docs/audit/architecture/CURRENT.md`
3. Related `docs/audit/changes/CHG-*.md`
4. Related `docs/audit/decisions/ADR-*.md`

Treat only `verified` records as confirmed production state. Say explicitly when a record is merely approved, locally implemented, deployed but not verified, rolled back, or superseded. Never expose secrets or raw private user content from evidence.
