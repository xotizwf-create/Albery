# CHG-20260812-10: Verify agent-generated links before delivery

- Status: verified
- Date opened: 2026-08-12
- Incident: an employee received one or more agent-generated links that did not exist
- Approval: the owner explicitly requested an urgent production repair and root-cause analysis
- Related decisions: [ADR-0003](../decisions/ADR-0003-private-per-agent-mcp.md), [ADR-0005](../decisions/ADR-0005-channel-neutral-agent-runtime.md)
- Bitrix engineering task: pending

## Before

The agent can include URLs returned by tools, found in source material, recalled from history or
written directly by the model. Export-link repair protects signed Albery download URLs from
character damage, but there is no universal provenance/availability gate for every URL in a final
employee-facing answer.

## Root cause

The affected employee used the legal agent to generate a Word document and received two different
signed `/zoom-export/` URLs. Both files existed, both HMAC signatures were valid, both links were
within their 30-minute TTL, and the bot/web Flask roles returned the files with HTTP 200. Public
`mcp.m4s.ru` returned 404, while the identical signed path on `www.m4s.ru` returned 200.

CHG-20260810-04 correctly made the legacy MCP hostname dark by default, but the export generator
still built employee-facing URLs from `MCP_HOST=mcp.m4s.ru`. The Nginx perimeter test asserted that
MCP and default Flask routes were dark, but deploy smoke never downloaded a real signed artifact.
The contradiction therefore passed both unit and production acceptance. This incident is routing
drift, not a fabricated document or corrupted signature.

## Target / implementation

- Generate all new signed artifacts on explicit `EXPORT_PUBLIC_HOST`, falling back to the canonical
  web host and then `www.m4s.ru`; never use `MCP_HOST` for employee-facing downloads.
- At the final Bitrix delivery choke point, canonicalize historical Albery `/zoom-export/` URLs to
  the public export host while retaining filename/signature repair.
- Keep one narrow Nginx compatibility location for already-delivered, HMAC+TTL-protected export
  links on `mcp.m4s.ru`; every MCP, health, login and API route remains 404 there.
- Extend deploy smoke to create a disposable signed file, download its exact bytes through the
  canonical public host and through the legacy compatibility path, then remove it.
- Do not add a generic crawler/filter for arbitrary external links: authenticated Bitrix/Drive
  resources cannot be safely judged by an anonymous HTTP probe, and they were not the cause here.

## Risks

- A broad URL filter can remove legitimate private Bitrix/Drive links that are inaccessible to an
  unauthenticated server-side probe.
- Rechecking URLs can add latency or leak signed/query credentials into logs.
- Mutating or one-time URLs must never be opened by a generic validator.
- Prompt-only protection is probabilistic and cannot be the sole safety boundary.

## Safety and rollback

- Preserve only redacted/minimal incident evidence; do not store employee messages or private URLs
  in Git.
- Back up every production file/configuration changed and record the pre-change commit.
- Prefer deterministic validation close to answer delivery, with an environment kill switch if a
  new runtime component is required.
- Rollback is the backed-up files plus the pre-change Git commit; restart only after the shared
  empty-inflight gate.

## Verification evidence

- Minimal production diagnosis used only the two affected export records and did not persist their
  URLs or conversation text in Git. File existence, signature, TTL and loopback/public HTTP status
  were checked independently.
- Local pre-change backup:
  `tmp/backups/pre-chg-20260812-10-20260812_144822`; pre-change Git head `6ec6b9a`.
- Python compilation and pyflakes passed for changed runtime/smoke modules; `git diff --check`
  passed.
- Focused link/Nginx/private-perimeter suite: `46 passed`.
- Full local regression: `1914 passed, 44 skipped`; the skipped PostgreSQL/LibreOffice scenarios are
  the documented CI/server-only cases. Nine warnings are third-party `httplib2` deprecations.
- `pip-audit -r requirements.txt`: no known vulnerabilities.
- Implementation commit `5d23c7999f7f8a5f87de87c31b3d489ba698a276` passed GitHub tests run
  `31593951073` and security run `31593950769`.
- Production preflight at head `6ec6b9a` found a clean tracked tree, 846 MiB available RAM, 13 GiB
  free disk, all five relevant application services active, valid Nginx configuration, zero
  Bitrix in-flight turns and zero running legacy/durable agent automations.
- Pre-deploy production backup:
  `/var/backups/albery/pre-link-route-20260812_145622`. It contains a full Git bundle, tracked-code
  archive, protected production environment, live Nginx configuration and manifest; files are mode
  `0600`. No database backup was necessary because schema/data were not changed.
- Production fast-forwarded to `5d23c79`; Python compilation passed. The repository Nginx file was
  installed atomically, `nginx -t` passed and reload completed. A second empty-inflight gate passed
  before controlled restarts of `albery-mcp`, `albery-web` and `albery`.
- All six relevant services are active. Full deploy smoke passed all CHG-10 gates: canonical
  `www.m4s.ru` signed download returned the exact disposable bytes; the narrow legacy-host path did
  the same; all public MCP/SSE/default negative probes remained 404; nine exact private MCP
  matrices, VPN, Bitrix/workspace routes and Albery Telegram transports passed. The only smoke
  failure remains the known unrelated native Hermes Telegram state `retrying`.
- An additional production acceptance called the real `export_document` tool through the legal
  agent's private/header-authenticated MCP, received a `www.m4s.ru` URL, downloaded a valid DOCX with
  HTTP 200 and removed the temporary document plus sidecar. No message was sent to the employee.
- Post-deploy warning/error journals for the three restarted services were empty and the tracked
  production tree remained clean.

## Post-verification controlled employee acceptance

At 15:17 MSK on 2026-08-12 the owner explicitly approved sending the requested result to the
affected employee. The original 30-minute export had expired, so the delivery was rebuilt from the
complete `agent_doc` text already captured for the same legal-agent/dialog scope; no conversation
content or download URL was copied into Git.

- The active Bitrix employee identity and active legal-agent bot identity were checked both in the
  local registry and against live Bitrix immediately before sending. The existing agent access rule
  allowed the recipient, and a prior dialogue with that exact agent/bot pair existed.
- The rebuilt DOCX was a valid Office archive. Extracting it again produced the same normalized
  10,712-character text as the stored requested result.
- The fresh canonical `www.m4s.ru` signed URL returned HTTP 200 and byte-identical DOCX content
  before delivery.
- Bitrix accepted exactly one outbound message from the legal agent. The full message journal
  recorded exactly one matching outbound row for that returned Bitrix message id, with the intended
  recipient/agent/bot scope and a direct instruction that the file could be downloaded.
- A separate post-delivery process read the URL from the recorded message, downloaded it again with
  HTTP 200, validated the DOCX archive, and proved that its extracted text still matched the stored
  result. At that check the URL had 1,765 seconds of TTL remaining.
- `albery`, `albery-web`, and `albery-mcp` were all `active` after delivery.
- This acceptance changed no code, configuration, schema, or durable business data beyond the
  explicitly approved outbound message and its normal journal entry. There was therefore no new
  deployment backup or technical rollback; an external message is irreversible and was sent only
  after the owner's explicit approval. The signed export remains subject to normal TTL cleanup.

## Changed files

- `zoom.py`, `.env.example`: canonical public export host and final-link canonicalization.
- `deploy/nginx-albery.conf`: narrow signed-export compatibility location only.
- `scripts/deploy_smoke.py`: disposable byte-for-byte public download probe.
- export/perimeter/service-split unit tests and the file-delivery playbook.
