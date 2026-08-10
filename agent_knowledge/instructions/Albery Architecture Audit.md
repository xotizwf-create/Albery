---
name: Albery Architecture Audit
scope: optional
sort_order: 3
---

When a request concerns Albery's architecture, integrations, model routing, deployments, or change history, use `skill:albery-audit` and the versioned records in `docs/audit/`.

Separate three things in every answer: the verified production state, an approved target state, and work that is still pending verification. Prefer links or record IDs (`ADR-*`, `CHG-*`) over memory. Do not infer that a commit is deployed merely because it exists in Git.
