# Albery architecture audit

This directory is the canonical, versioned knowledge base for Albery's architecture and engineering changes. It is designed for people, Codex, Hermes agents, and other repository-aware AI agents.

## Read first

1. [Operating rules](OPERATING_RULES.md)
2. [Current architecture](architecture/CURRENT.md)
3. [Decision register](#decisions)
4. [Change register](#changes)
5. [Chronological changelog](CHANGELOG.md)
6. [Full audit roadmap](ROADMAP.md)

## Inventories

- [All 166 MCP capabilities](inventories/MCP_CAPABILITIES.md)
- [Exact production agent/tool grants](inventories/MCP_AGENT_GRANTS.md)

## Decisions

| ID | Status | Decision |
| --- | --- | --- |
| [ADR-0001](decisions/ADR-0001-versioned-audit-base.md) | accepted | Git is the source of truth for the architecture audit |
| [ADR-0002](decisions/ADR-0002-codex-reasoning-groq-media.md) | accepted | Codex handles quality reasoning; Groq handles media |
| [ADR-0003](decisions/ADR-0003-private-per-agent-mcp.md) | accepted | MCP is loopback-only, header-authenticated, and scoped per agent |
| [ADR-0004](decisions/ADR-0004-durable-conflict-safe-agent-automations.md) | accepted | Agent automations use durable stages, the shared heavy-process limit and fail-closed idempotency |
| [ADR-0005](decisions/ADR-0005-channel-neutral-agent-runtime.md) | accepted | Bitrix and employee Telegram are channel adapters for one logical agent profile |
| [ADR-0006](decisions/ADR-0006-channel-native-artifact-delivery.md) | accepted | Generated files are delivered as native channel attachments, not employee-facing bearer links |
| [ADR-0007](decisions/ADR-0007-exhaustive-mcp-policy-and-fail-closed-caps.md) | accepted | Every MCP tool is semantically classified and every agent cap fails closed |
| [ADR-0008](decisions/ADR-0008-verified-postgresql-backup-chain.md) | accepted | PostgreSQL backups are atomic, SHA-256/pg_restore verified and routine restores are isolated |

## Changes

| ID | Status | Change |
| --- | --- | --- |
| [CHG-20260810-01](changes/CHG-20260810-01-quality-model-routing.md) | verified | Move offers, task check-in, and Novinki analysis from Groq to isolated Codex |
| [CHG-20260810-02](changes/CHG-20260810-02-nanoid-security-update.md) | verified | Update locked nanoid after the mandatory security gate detected a high-severity advisory |
| [CHG-20260810-03](changes/CHG-20260810-03-independent-acceptance-hardening.md) | verified | Independently re-test and harden all quality-routing paths |
| [CHG-20260810-04](changes/CHG-20260810-04-private-per-agent-mcp.md) | verified | Retire shared MCP sets and make per-agent MCP private |
| [CHG-20260811-05](changes/CHG-20260811-05-vpn-routing-automation-recovery.md) | verified | Restore VPN policy routing and recover model-backed automations; successor acceptance closed the original gates |
| [CHG-20260811-06](changes/CHG-20260811-06-bitrix-agent-automation-diagram.md) | superseded | Historical Bitrix per-agent/independent-automation diagram replaced by the durable shared-limit architecture |
| [CHG-20260811-07](changes/CHG-20260811-07-durable-conflict-safe-agent-automations.md) | verified | Put agent automations under the shared limit and make queue, retry and effects durable/conflict-safe |
| [CHG-20260811-08](changes/CHG-20260811-08-telegram-agent-architecture-audit.md) | superseded | Historical Telegram before-state audit replaced by the channel-neutral runtime and final transport decision |
| [CHG-20260811-09](changes/CHG-20260811-09-channel-neutral-telegram-agents.md) | deployed | Implement one Bitrix/Telegram profile, closed access, durable Telegram delivery and typed automation destinations |
| [CHG-20260812-10](changes/CHG-20260812-10-verified-agent-links.md) | verified | Repair signed export links broken by the dark legacy MCP hostname and add public round-trip smoke |
| [CHG-20260812-11](changes/CHG-20260812-11-channel-native-artifacts.md) | deployed | Deliver generated files as Bitrix/Telegram attachments and remove legacy MCP-host export compatibility |
| [CHG-20260812-12](changes/CHG-20260812-12-automation-acceptance-system-cron.md) | verified | Complete durable automation acceptance and classify/migrate legacy system cron jobs |
| [CHG-20260812-13](changes/CHG-20260812-13-telegram-final-acceptance.md) | deployed | Retire redundant native Hermes Telegram and complete explicit channel identity acceptance |
| [CHG-20260812-14](changes/CHG-20260812-14-mcp-discovery-and-stale-file-recovery.md) | deployed | Scope one-shot/gateway MCP discovery and rebuild old generated-file requests instead of echoing expired URLs |
| [CHG-20260813-15](changes/CHG-20260813-15-hermes-restart-production-hygiene.md) | verified | Make Hermes restart control compatible with systemd 249 and separate runtime/backup artifacts from the production Git tree |
| [CHG-20260813-16](changes/CHG-20260813-16-audit-record-reconciliation.md) | verified | Reconcile historical CHG statuses against later production acceptance without overstating open employee-channel scenarios |
| [CHG-20260813-17](changes/CHG-20260813-17-mcp-capability-rights-audit.md) | verified | Classify all MCP capabilities, freeze fail-closed per-agent caps and centrally guard consequential calls without changing live grants |
| [CHG-20260813-18](changes/CHG-20260813-18-vpn-healthcheck-transient-hardening.md) | verified | Version and harden the VPN/provider healthcheck against proven one-shot false alarms while preserving fail-closed sustained-outage detection |
| [CHG-20260813-19](changes/CHG-20260813-19-postgresql-disaster-recovery-audit.md) | deployed | Audit PostgreSQL and harden atomic local/offsite recovery with measured RPO/RTO; natural backup acceptance pending |
| [CHG-20260813-20](changes/CHG-20260813-20-client-telegram-iu-crm-lifecycle-audit.md) | approved | Audit the full client Telegram/IU, manager handoff, media, CRM and durable-delivery lifecycle |

## Runbooks

- [PostgreSQL recovery](runbooks/POSTGRES_RECOVERY.md)

## Status rule

`verified` is the only status that confirms production behavior. Earlier statuses describe intent or progress, not the live server.
