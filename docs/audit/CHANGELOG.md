# Architecture audit changelog

Append entries in reverse chronological order. Link to the detailed record; do not duplicate its full content here.

## 2026-08-12

- `implemented_local` — [CHG-20260812-10](changes/CHG-20260812-10-verified-agent-links.md): the reported document links were valid signed files routed through the intentionally dark legacy MCP hostname. New links use the public web host, historical export links are canonicalized, a narrow HMAC+TTL compatibility route is explicit, and deploy smoke now downloads real bytes through Nginx; full local regression and dependency audit passed.
- `approved` — [CHG-20260812-10](changes/CHG-20260812-10-verified-agent-links.md): urgently reconstruct the invalid-link incident, identify provenance and add a deterministic fail-closed employee-facing link contract without exposing private URLs or conversation content.

## 2026-08-11

- `deployed` — [CHG-20260811-09](changes/CHG-20260811-09-channel-neutral-telegram-agents.md): production migrated behind verified backups and an empty-inflight gate; the employee Telegram identity/access moved explicitly to `main`, fail-closed stable-id checks and durable tables passed, the feature flag is on, and all CHG-09 smoke gates pass. No real message was sent, so a user-visible round trip remains before `verified`.
- `deployed` — [CHG-20260811-07](changes/CHG-20260811-07-durable-conflict-safe-agent-automations.md): migration 083, private automation connector aliases and the shared two-slot durable worker are live on server 186. Production structure/smoke passed without creating an automation or external write; controlled reversible business acceptance remains before `verified`.
- `implemented_local` — [CHG-20260811-09](changes/CHG-20260811-09-channel-neutral-telegram-agents.md): employee Telegram now attaches to an existing Bitrix agent profile, uses the same identity/knowledge/private MCP, fails closed, processes media through Groq, persists update/brain/outbox stages, and returns typed automations through the originating channel. Full unit regression passed; production cutover remains disabled because server SSH authentication is unavailable and live bot identities were not reconciled.
- `accepted` — [ADR-0005](decisions/ADR-0005-channel-neutral-agent-runtime.md): one logical agent owns behavior and rights; Bitrix and Telegram supply only channel context, history, rendering and transport.
- `implemented_local` — [CHG-20260811-08](changes/CHG-20260811-08-telegram-agent-architecture-audit.md): documented three separate Telegram contours and proved that channel-neutral agents are only partially implemented; the additional-agent path is open when its allowlist is empty, lacks durable delivery, and Albery automations currently deliver through Bitrix rather than their originating Telegram conversation.
- `documentation_sync` — [CHG-20260811-07](changes/CHG-20260811-07-durable-conflict-safe-agent-automations.md): the workspace architecture Markdown is now the single human entry point and explicitly separates verified production from the locally implemented automation target; versioned ADR/CHG records remain canonical evidence.
- `implemented_local` — [CHG-20260811-07](changes/CHG-20260811-07-durable-conflict-safe-agent-automations.md): agent automations now use PostgreSQL stages and leases, atomic trigger keys, the shared two-slot Hermes limit, delivery-only retry, per-run write deduplication and business-object advisory locks; production rollout is still pending.
- `accepted` — [ADR-0004](decisions/ADR-0004-durable-conflict-safe-agent-automations.md): durable staged execution and fail-closed ambiguous outcomes replace the in-memory/whole-run retry model for agent automations.
- `implemented_local` — [CHG-20260811-06](changes/CHG-20260811-06-bitrix-agent-automation-diagram.md): the Bitrix overview now separates profile routing from agent-owned automations and distinguishes the two live-turn slots from the independent one-worker automation lane.
- `deployed` — [CHG-20260811-05](changes/CHG-20260811-05-vpn-routing-automation-recovery.md): independent control acceptance re-ran full tests and live VPN/Codex/Groq/MCP/Zoom/Bitrix/WB/Drive checks. Core recovery is healthy, but Telegram credential replacement, the automation-36 resend decision, and a successful automation-59 write run remain before `verified`.
- `implemented_production` — [CHG-20260811-05](changes/CHG-20260811-05-vpn-routing-automation-recovery.md): policy routing restored and self-healing watchdog deployed; Codex, private MCP, end-to-end automation probe and all queued Zoom reports recovered. Hermes Telegram remains blocked by its pre-existing rejected bot token, and the missed employee report awaits explicit resend approval.
- `approved` — [CHG-20260811-05](changes/CHG-20260811-05-vpn-routing-automation-recovery.md): production incident confirmed; a live AmneziaWG handshake masked missing policy routes, causing Codex HTTP 403, failed Zoom analysis, failed agent automations, and Telegram delivery loss. Restore routing and harden the watchdog.

## 2026-08-10

- `verified` — [CHG-20260810-04](changes/CHG-20260810-04-private-per-agent-mcp.md): ten rotated per-agent connectors are loopback/header-only; five shared and all path-token/SSE routes are removed; the legacy MCP host is dark except for a webhook allowlist; CI, live tool matrices, public negative probes, Codex quality, in-process Telegram reads, webhooks, health and journals passed.
- `implemented_local` — [CHG-20260810-04](changes/CHG-20260810-04-private-per-agent-mcp.md): shared endpoints removed, private header transport and atomic credential migration implemented; local regression passed.
- `approved` — [CHG-20260810-04](changes/CHG-20260810-04-private-per-agent-mcp.md): migrate to private header-authenticated per-agent MCP and retire fixed shared connector classes.
- `accepted` — [ADR-0003](decisions/ADR-0003-private-per-agent-mcp.md): loopback-only per-agent MCP is the sole model capability boundary.
- `verified` — [CHG-20260810-03](changes/CHG-20260810-03-independent-acceptance-hardening.md): final CI matrices, production probes, no-write counters, media checks, smoke, backup, and Bitrix task 2670 confirmed.
- `implemented_local` — [CHG-20260810-03](changes/CHG-20260810-03-independent-acceptance-hardening.md): adversarial tests now pass; schema, boolean, agent binding, environment isolation, and Groq-primary vision routing are hardened.
- `approved` — [CHG-20260810-03](changes/CHG-20260810-03-independent-acceptance-hardening.md): adversarial re-verification found schema, boolean, environment-isolation, and media-routing gaps.
- `verified` — [CHG-20260810-02](changes/CHG-20260810-02-nanoid-security-update.md): security gate, production deployment, and Bitrix task 2668 confirmed.
- `verified` — [CHG-20260810-01](changes/CHG-20260810-01-quality-model-routing.md): zero-tool invariant, live Codex paths, production health, and Bitrix task 2666 confirmed.
- `implemented_local` — [CHG-20260810-02](changes/CHG-20260810-02-nanoid-security-update.md): locked transitive nanoid updated after a high-severity security advisory.
- `implemented_local` — [CHG-20260810-01](changes/CHG-20260810-01-quality-model-routing.md): quality reasoning moved to an isolated Codex contour; Groq retained for media processing.
- `accepted` — [ADR-0002](decisions/ADR-0002-codex-reasoning-groq-media.md): provider responsibility boundary.
- `accepted` — [ADR-0001](decisions/ADR-0001-versioned-audit-base.md): versioned audit base.
