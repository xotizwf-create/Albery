# Albery architecture audit

This directory is the canonical, versioned knowledge base for Albery's architecture and engineering changes. It is designed for people, Codex, Hermes agents, and other repository-aware AI agents.

## Read first

1. [Operating rules](OPERATING_RULES.md)
2. [Current architecture](architecture/CURRENT.md)
3. [Decision register](#decisions)
4. [Change register](#changes)
5. [Chronological changelog](CHANGELOG.md)

## Decisions

| ID | Status | Decision |
| --- | --- | --- |
| [ADR-0001](decisions/ADR-0001-versioned-audit-base.md) | accepted | Git is the source of truth for the architecture audit |
| [ADR-0002](decisions/ADR-0002-codex-reasoning-groq-media.md) | accepted | Codex handles quality reasoning; Groq handles media |
| [ADR-0003](decisions/ADR-0003-private-per-agent-mcp.md) | accepted | MCP is loopback-only, header-authenticated, and scoped per agent |
| [ADR-0004](decisions/ADR-0004-durable-conflict-safe-agent-automations.md) | accepted | Agent automations use durable stages, the shared heavy-process limit and fail-closed idempotency |

## Changes

| ID | Status | Change |
| --- | --- | --- |
| [CHG-20260810-01](changes/CHG-20260810-01-quality-model-routing.md) | verified | Move offers, task check-in, and Novinki analysis from Groq to isolated Codex |
| [CHG-20260810-02](changes/CHG-20260810-02-nanoid-security-update.md) | verified | Update locked nanoid after the mandatory security gate detected a high-severity advisory |
| [CHG-20260810-03](changes/CHG-20260810-03-independent-acceptance-hardening.md) | verified | Independently re-test and harden all quality-routing paths |
| [CHG-20260810-04](changes/CHG-20260810-04-private-per-agent-mcp.md) | verified | Retire shared MCP sets and make per-agent MCP private |
| [CHG-20260811-05](changes/CHG-20260811-05-vpn-routing-automation-recovery.md) | deployed | Restore VPN policy routing and recover model-backed automations; three live acceptance gates remain |
| [CHG-20260811-06](changes/CHG-20260811-06-bitrix-agent-automation-diagram.md) | implemented_local | Document Bitrix per-agent routing and the independent agent-automation lane |
| [CHG-20260811-07](changes/CHG-20260811-07-durable-conflict-safe-agent-automations.md) | implemented_local | Put agent automations under the shared limit and make queue, retry and effects durable/conflict-safe |
| [CHG-20260811-08](changes/CHG-20260811-08-telegram-agent-architecture-audit.md) | implemented_local | Audit all Telegram contours, profile/permission mapping, delivery reliability and automation destinations |

## Status rule

`verified` is the only status that confirms production behavior. Earlier statuses describe intent or progress, not the live server.
