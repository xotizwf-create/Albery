# Current Albery architecture

Last reviewed: 2026-08-11.

## Verified production state

Production server 186 runs implementation commit `d6ff01807818933c0efd56ae59fd69b9033fc0d7`.
The model routing was deployed under
[CHG-20260810-01](../changes/CHG-20260810-01-quality-model-routing.md) and independently
re-verified/hardened under
[CHG-20260810-03](../changes/CHG-20260810-03-independent-acceptance-hardening.md).

## Verified runtime shape

```mermaid
flowchart LR
    U[Bitrix and Telegram users] --> R[Albery runtime]
    R --> A[Hermes live agents and MCP tools]
    R --> Q[Codex quality contour]
    R --> M[Groq media contour]
    A --> DB[(PostgreSQL)]
    A --> EXT[Bitrix, Zoom, Drive, Wildberries]
    Q --> O[Task offers]
    Q --> C[Task check-in classification]
    Q --> N[Novinki synthesis]
    M --> STT[Audio transcription]
    M --> OCR[Screenshots and OCR]
    Q -. JSON only; zero tools .-> R
```

### Quality contour

- Calls the installed Hermes/Codex runtime through a dedicated one-shot process.
- Receives untrusted task/file text through standard input, never through command arguments.
- Receives only allowlisted runtime/provider environment variables; Albery business-system
  credentials are not inherited by the one-shot process.
- Has zero MCP, web, shell, or application tools; deploy self-check asserts this invariant.
- Produces JSON only, uses the shared global run-slot limiter, has bounded timeout and retry.
- Can be disabled immediately with `QUALITY_LLM_ENABLED=0`.
- Task check-in strictly validates JSON booleans/IDs and fails closed. Novinki strictly validates
  the response schema and retains source files if any AI batch fails. Task offers use a
  deterministic non-generative fallback bound to a real configured agent.

### Media contour

Groq Whisper handles audio transcription. Screenshot/OCR uses Groq first and Codex only as a
resilience fallback. Neither media path is a generative fallback for the three quality-reasoning
scenarios above.

### Audit visibility

- Coding agents discover `.agents/skills/albery-audit/SKILL.md` and root `AGENTS.md`/`CLAUDE.md`.
- Runtime agents receive `skill:albery-audit` plus the optional architecture-audit instruction.
- Durable decisions and change evidence live in `docs/audit/`.

## Verified private per-agent MCP

This production state is accepted under [ADR-0003](../decisions/ADR-0003-private-per-agent-mcp.md)
and verified by [CHG-20260810-04](../changes/CHG-20260810-04-private-per-agent-mcp.md).

```mermaid
flowchart LR
    U[Bitrix and Telegram users] --> R[Albery runtime]
    R --> H[Hermes on the same host]
    H -->|127.0.0.1:5004<br/>Bearer header| P[/mcp-agent/slug]
    P --> C[DB switches intersect manifest cap]
    C --> T[Exact agent tool set]
    T --> DB[(PostgreSQL)]
    T --> EXT[Bitrix, Zoom, Drive, Wildberries]
    INTERNET[Public Internet] --> N[Nginx]
    N -->|/mcp* and /sse*: 404| X[No public MCP]
    N -->|authenticated event routes| W[Bitrix, Zoom and Drive webhooks]
    R --> Q[Zero-tool Codex contour]
    Q --> S[Summaries and quality reasoning]
```

- There are ten active model-facing MCP endpoints, exactly one for each active agent. Their tool
  sets are computed from database switches intersected with the versioned manifest cap.
- The five former shared connectors and all legacy SSE routes are removed. URL-token routes do not
  exist; changing an environment flag cannot restore them.
- Hermes stores loopback URLs and Bearer headers in a mode-`0600` configuration. All ten agent
  credentials were rotated during migration.
- Ports `5002`, `5003`, and `5004` listen on `127.0.0.1` only. Nginx returns 404 for `/mcp*` and
  `/sse*` on both public hosts; the legacy MCP hostname also returns 404 for every default route,
  including health/login/API, while forwarding only Bitrix, Zoom, and Drive webhooks.
- Owner Telegram uses `agent-main,web`. Deterministic Telegram/CRM operations use an in-process
  allowlist rather than a shared HTTP MCP credential. Missing main-agent wiring fails closed.
- Dialogue summaries and diagnostic digests use the isolated zero-tool Codex quality contour;
  Groq remains responsible for audio and primary screenshot/OCR processing.

## Telegram channel architecture

The repository and inherited production evidence were audited under
[CHG-20260811-08](../changes/CHG-20260811-08-telegram-agent-architecture-audit.md). No fresh live
Telegram probe was possible on 2026-08-11 because the available SSH connection was rejected; the
production-health statements below therefore retain the last usable CHG-20260811-05 evidence.

```mermaid
flowchart TD
    C[Client and Telegram Business messages] --> W[albery-tg.service workspace path]
    W --> Z[Zero-tool IU customer runtime]
    Z --> O[(PostgreSQL transactional outbox)]
    O --> API[Telegram Bot API]

    T[Trusted owner Telegram] --> G[hermes-gateway.service]
    G --> M[agent-main plus web]
    M --> GD[Native gateway delivery and Hermes cron]

    X[Additional bot token] --> P[tg_multi polling thread]
    P --> A[agent-slug plus web]
    A --> DS[Direct synchronous sendMessage]

    BA[Bitrix bot for a profile] --> BP[Bitrix turn builder]
    BP --> M
```

- These are three separate runtimes. The native owner gateway has its own Telegram credential,
  Hermes memory, allowlist and native cron. The client/IU workspace has durable PostgreSQL intake
  and delivery. Additional Agent Center bots use one polling thread and token per profile inside
  `albery-tg.service`.
- A profile may technically hold both Bitrix and Telegram identities, and `agent-<slug>` gives both
  channels the same private MCP capability boundary. The supported product path does not attach a
  Telegram identity to the existing `main`/Bitrix profile, however, and the channel runtimes do not
  share a prompt builder, dialogue history, requester identity, access model or delivery adapter.
  “The same agent through two channels” is therefore an architectural target, not the current
  end-to-end behavior.
- Agent Center can create Telegram-only profiles and manage their tools, instructions, skills,
  role and active state. Additional-bot conversation access is stored separately in
  `telegram_bot_access`. Its current empty-list semantics are fail-open: a new bot accepts every
  Telegram user until at least one allowlist entry exists, contrary to the UI wording. The native
  owner gateway uses a different configuration allowlist.
- The client workspace is the reliable Telegram path: raw updates are committed before offsets,
  conversations are ordered, and outbound messages use an idempotent leased outbox with explicit
  handling for ambiguous provider outcomes. Additional bots keep offsets in memory and call
  `sendMessage` directly; a crash can replay an update, delivery is not durably retried, and a slow
  model turn serially blocks that bot's polling thread.
- Albery `agent_automations` are not channel-aware. Their `deliver_to` contract is a Bitrix dialog
  id, and delivery always calls the Bitrix notifier. A Telegram chat id can therefore fail or be
  misrouted, while a Telegram-only profile falls back to the main Bitrix sender. Native Hermes cron
  can deliver through the owner gateway, but is a separate `kind='system'` mechanism outside the
  CHG-07 durable automation worker and shared safety contour.
- Latest inherited production evidence: standalone `albery-tg.service` and the client workspace
  transport were healthy; the native Hermes Telegram platform was `retrying` because Telegram
  rejected its configured token with HTTP 401. No current live health proof exists for each
  additional bot token.

Required follow-up is a separate approved runtime change: channel-neutral identity binding,
fail-closed Telegram access, durable additional-agent intake/delivery, per-bot health, and an
explicit automation destination `{channel, profile, conversation_id}` with a Telegram adapter.

## Bitrix agent and automation split

The profile ownership statements below are verified production behavior. The durable automation
lane is implemented locally under [ADR-0004](../decisions/ADR-0004-durable-conflict-safe-agent-automations.md)
and [CHG-20260811-07](../changes/CHG-20260811-07-durable-conflict-safe-agent-automations.md),
but is not production state until migration, connector materialization, CI and live smoke pass.

```mermaid
flowchart LR
    B[Bitrix employee event] --> R[bot_id to agent_slug]
    R --> P[Profile with matching bitrix_bot_id]
    P --> C[Own identity, rules, knowledge and dialog history]
    C --> H[Hermes live turn]
    H --> M[Private MCP for this agent]
    M --> O[Reply from this Bitrix bot]

    S[Schedule or atomic Run now] --> Q[(PostgreSQL durable run queue)]
    Q --> AS[Owning agent_slug]
    AS --> AP[Same agent profile]
    AP --> L{Shared two-slot<br/>heavy-process limit}
    H --> L
    L --> AH[Hermes automation subprocess]
    AH --> AM[Automation alias of same private MCP plus web]
    AM --> E[Per-run effect ledger and object lock]
    E --> D[(Stored brain result)]
    D --> AO[Bitrix delivery stage]
    AO -->|known failure| DR[(Durable delivery retry)]
    DR --> AO
    AO -->|ambiguous outcome| RV[Manual review; no blind replay]
```

- The shared `agents` registry has ten active profiles across Bitrix, internal and Telegram
  channels. For an employee Bitrix event, `bot_id` selects the profile with the matching
  `bitrix_bot_id`; dialogue scope is the pair `(agent_slug, dialog_id)`. Every profile has its own
  identity, instructions, knowledge, access rules and private MCP capability set, while channel
  identities are optional and may be Bitrix, Telegram, both or internal-only.
- An automation row belongs to exactly one profile through `agent_automations.agent_slug`. Its
  prompt is assembled with that profile's role, skills and personal instructions; Hermes receives
  the exact profile capability set, and delivery uses that profile's Bitrix bot identity.

### Verified production behavior before CHG-20260811-07

- Scheduled and manually launched agent automations do not enter through the inbound Bitrix
  webhook. They currently use an independent in-process FIFO lane with one worker and therefore
  do not consume the two shared live Bitrix/Telegram slots.
- Queue and delayed retry are process memory. A delivery failure can repeat the whole Hermes turn.
  `Run now` is not a single atomic claim, and an interrupted run is eventually displayed as
  `interrupted` rather than resumed from a durable stage.

### Locally implemented target under CHG-20260811-07

- `agent_automation_runs` is the durable stage machine. Manual triggers are protected by a partial
  unique active-run constraint under an automation row lock; scheduled triggers use
  `schedule:<automation_id>:<minute>` keys. Due rows are claimed with `FOR UPDATE SKIP LOCKED` and
  expiring leases.
- Automation Hermes uses `automation-agent-<slug>,web`, a loopback alias of the same per-agent MCP.
  It consumes `shared.run_slots.build_default()` exactly like live Bitrix, Telegram and quality
  turns. Background work refuses the process-local fallback when PostgreSQL cannot enforce the
  server-wide limit.
- Brain output is committed before delivery. Recipient attempts live in
  `agent_automation_deliveries`; known failures retry only the stored message. Timeout/connection
  outcomes and expired `sending` leases become `review`, because automatic resend could duplicate
  a message already accepted by Bitrix.
- Unknown MCP tools are treated as mutating. Automation mutations are fingerprinted per run in
  `agent_automation_tool_effects`; a completed duplicate returns the stored result and an
  ambiguous prior effect fails closed. All model-facing mutating calls take a PostgreSQL advisory
  lock derived from the identifiable business object, with a deliberately coarse per-tool fallback.
- `kind='system'` Hermes/crond mirror rows remain outside this worker and require their own runtime
  migration/audit. The actual automation inventory and business-output acceptance also remain a
  separate controlled audit step.

## Current operational status

- Outbound model/provider traffic is policy-routed through AmneziaWG exit `95.85.243.43`.
  The watchdog verifies the effective route and reapplies missing policy rules; a fresh tunnel
  handshake alone is no longer treated as healthy.
- Codex, private MCP and Zoom report generation are operational after
  [CHG-20260811-05](../changes/CHG-20260811-05-vpn-routing-automation-recovery.md).
- Hermes Telegram is degraded: its platform state is `retrying` because Telegram rejects the
  configured bot token. This credential predates the 2026-08-10 MCP migration. The standalone
  Albery Telegram service and Bitrix delivery paths are separate and active.
- Deploy smoke checks effective VPN health and the Telegram platform state, so this known
  degradation prevents a false all-green acceptance until the bot token is replaced.
- Agent automation 36 retains its failed 09:00 status pending an explicit resend decision.
  Automation 59 has healthy read-only Google Sheet and WB-price dependencies, but its previous
  full write run timed out and still needs a successful scheduled or owner-approved controlled run.
