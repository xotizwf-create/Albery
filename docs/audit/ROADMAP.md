# Albery full audit roadmap

Last reviewed: 2026-08-13.

This is the canonical queue for the full-system audit requested by the owner. Detailed facts,
changes, tests and rollback evidence remain in linked `CHG-*` records. A workstream is complete only
when its production behavior is `verified`; code review or a green unit suite alone is insufficient.

## Status legend

- `in_progress`: active audit or remediation exists;
- `pending`: not yet audited end to end;
- `acceptance`: deployed behavior still needs an approved real-recipient or provider scenario;
- `verified`: production behavior and recovery path have been demonstrated.

## Workstreams

| Priority | Status | Workstream | Required outcome |
| --- | --- | --- | --- |
| P0 | in_progress | Hermes restart and production-tree hygiene | systemd-249-compatible restart control, no secret backups in the web repository, runtime files explicitly ignored |
| P0 | pending | Audit-record reconciliation | old CHG gates are amended from later evidence; historical records are explicitly superseded rather than silently rewritten |
| P0 | acceptance | Employee channel acceptance | approved Bitrix native-file delivery and employee Telegram round trip; Telegram-to-Bitrix identity remains fail-closed until immutable IDs and person are confirmed |
| P1 | pending | MCP capability and agent-rights matrix | every registered tool classified by data domain, read/write danger, confirmation, idempotency, object locking and exact agent grants |
| P1 | pending | PostgreSQL and disaster recovery | schema/retention/PII/performance review plus isolated restore drill and measured RPO/RTO for local and offsite backups |
| P1 | pending | Client Telegram/IU and CRM funnel | full customer-message, manager handoff, form, media, CRM and durable-outbox lifecycle including restart and provider ambiguity |
| P2 | pending | Bitrix business correctness | webhook deduplication, OAuth recovery, authorship, tasks, CRM/funnel mutations, partial failure and boundary cases |
| P2 | pending | Zoom, Google and Wildberries | token expiry, API drift, quotas/rate limits, retry/idempotency, partial writes and observable recovery for each connector |
| P2 | pending | Web/API and Agent Center | authentication, authorization, IDOR, CSRF/CORS, upload limits, atomic configuration, audit trail and rollback |
| P2 | pending | Knowledge, memory and prompts | provenance, ACL, prompt injection, precedence, channel isolation, retention, deletion and versioned skill/prompt rollout |
| P2 | pending | Document and media ingestion | MIME/content validation, archive/macro hazards, conversion sandbox, memory/size limits, temporary-file lifecycle and exact output |
| P3 | pending | Observability and incident response | correlation IDs, SLIs/SLOs, queue/provider alarms, PII-safe logs, runbooks and incident/restore exercises |
| P3 | pending | Host and supply-chain hardening | service users, systemd sandboxing, firewall/egress, SSH, secret rotation, OS/dependency updates, vendor patch lifecycle and reproducible rollback |
| P3 | pending | Capacity and degradation | safe envelope for the 2 GB host, queue growth, DB pools, large media and simultaneous light/heavy work |
| P3 | pending | Monolith decomposition | staged reduction of `app.py` and `mcp/context_server.py` without changing public or agent contracts in a big-bang rewrite |

## Execution rules

1. Each non-trivial remediation gets its own CHG and, when a boundary changes, an ADR.
2. Read-only discovery comes before mutation. Production changes require a backup and a named rollback.
3. No employee message, task, CRM mutation or delegated identity grant is used as acceptance without
   an exact preview/target and explicit owner approval.
4. New audit findings are recorded even when no immediate remediation is chosen.
5. `docs/audit/architecture/CURRENT.md` and the master architecture are updated only from verified
   production evidence; target behavior is labelled separately.
