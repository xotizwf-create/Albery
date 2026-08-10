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

## Changes

| ID | Status | Change |
| --- | --- | --- |
| [CHG-20260810-01](changes/CHG-20260810-01-quality-model-routing.md) | verified | Move offers, task check-in, and Novinki analysis from Groq to isolated Codex |
| [CHG-20260810-02](changes/CHG-20260810-02-nanoid-security-update.md) | verified | Update locked nanoid after the mandatory security gate detected a high-severity advisory |

## Status rule

`verified` is the only status that confirms production behavior. Earlier statuses describe intent or progress, not the live server.
