# Current Albery architecture

Last reviewed: 2026-08-10.

## Verified production state

Production server 186 runs implementation commit `1e0c3f87e5791570e4b6d08b3394c56d37b3575c`.
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
