# ADR-0003: Private per-agent MCP boundary

- Status: accepted
- Date: 2026-08-10
- Owners: Albery engineering
- Supersedes: fixed shared MCP connector classes

## Context

Albery currently exposes five shared connector classes (`/mcp`, `/mcp-faq`, `/mcp-ops`,
`/mcp-core`, `/mcp-ops-core`) and per-agent connectors whose bearer-equivalent token is embedded
in the URL. The actual agent capability model is already stored per agent: the database mode and
tool switches are intersected with the versioned manifest cap. The shared classes duplicate that
model, create capability drift, and allow the main agent to widen its permissions through a broad
fallback. URL tokens can also enter access logs, histories, diagnostics, and referrers.

The MCP host cannot be made entirely private because the same public host receives authenticated
Bitrix, Zoom, and Google Drive webhooks.

## Decision

- Every Hermes agent turn uses exactly one `agent-<slug>` connector. Its visible and callable
  tools are computed from the agent's live switches and manifest cap.
- MCP transport is loopback-only (`127.0.0.1:5004`). Nginx returns a non-descriptive 404 for every
  `/mcp*` and `/sse*` request on public hosts; Flask independently rejects forwarded/non-loopback
  MCP requests.
- Per-agent credentials are sent only in the `Authorization: Bearer ...` header. Credentials are
  never part of a URL. The Hermes config remains mode `0600`.
- Existing per-agent credentials are rotated during migration because their previous values were
  present in URL paths and access logs.
- The five shared connector classes and their SSE compatibility routes are retired. Model-free
  internal automation invokes allowlisted Python handlers in-process, not through a broad HTTP
  MCP endpoint.
- Tool-free summarization uses the isolated zero-tool Codex runner. It does not receive an MCP,
  web, shell, or application connector.
- Missing `agent-main` configuration fails closed with a visible temporary-unavailable response;
  it never falls back to a broader shared connector.
- The public webhook routes on `mcp.m4s.ru` remain reachable and retain their independent secrets.

## Alternatives considered

- Keep shared routes but hide them behind bearer headers: rejected because it preserves duplicate
  capability sources and a broad credential.
- Put MCP behind a VPN: rejected for the current single-host runtime because loopback is smaller,
  simpler, and does not introduce a new network dependency. Revisit if Hermes moves to another
  host.
- Keep tokens in HTTPS paths: rejected because transport encryption does not prevent path logging
  and operational leakage.
- Fall back from `agent-main` to `/mcp-ops-core`: rejected because availability must not widen or
  silently change authority.

## Consequences

- One capability source of truth and a smaller externally reachable surface.
- A broken per-agent connector becomes a visible availability failure instead of a privilege
  widening. Monitoring and deploy smoke must therefore validate every active connector.
- Local host compromise still exposes the Hermes config and database; filesystem permissions and
  host hardening remain required.
- A future multi-host Hermes deployment requires a new private transport decision (private subnet,
  mTLS, or WireGuard) before loopback can be replaced.

## Verification / revisit trigger

Verify public 404s without credentials, loopback-only success with bearer headers, rejection of
path tokens and forwarded requests, exact tool lists for every active agent, config mode `0600`,
retired shared connector absence, webhook health, and end-to-end Bitrix/Telegram turns. Revisit if
Hermes or MCP moves off the production host.
