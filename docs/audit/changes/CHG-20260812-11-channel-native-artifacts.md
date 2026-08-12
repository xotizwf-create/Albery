# CHG-20260812-11: Channel-native generated file delivery

- Status: implemented_local
- Date opened: 2026-08-12
- Related decisions: [ADR-0006](../decisions/ADR-0006-channel-native-artifact-delivery.md), [ADR-0003](../decisions/ADR-0003-private-per-agent-mcp.md)
- Bitrix engineering task: pending

## Goal

Remove employee-facing bearer links for generated files, retain exact bytes securely for retries
and editing, and remove the no-longer-needed legacy MCP-host compatibility route.

## Before

Bitrix and Telegram answers can contain signed `/zoom-export/` URLs. The file expires after 30
minutes; generated-document recall stores extracted text but deliberately drops the raw bytes.
`mcp.m4s.ru` exposes one narrow compatibility prefix for older signed links.

## Target / after

- Bitrix sends the real file from the selected profile bot in the same dialogue.
- Employee Telegram sends the real file through its selected profile bot and durable outbox.
- Generated-document capture preserves exact bytes under restricted filesystem permissions.
- Final adapters remove internal export URLs and fail closed on invalid artifacts.
- `mcp.m4s.ru/zoom-export/` returns 404; canonical public export smoke remains on `www.m4s.ru`.

## Changed boundaries and files

Expected: Bitrix final reply, Telegram durable outbox, attachment storage, migration, Nginx,
deployment smoke, tests, architecture and file-delivery playbook.

## Safety and privacy

- Store only unguessable attachment tokens in durable queues; never log file bytes or signed URLs.
- Keep attachment directory mode `0700` and payloads `0600`; preserve size/age cleanup.
- Sender/recipient/profile are resolved before upload. A failed upload produces no fallback bearer
  link.
- Production changes require code/config/database backups and the empty-inflight restart gate.

## Verification plan and evidence

- Unit tests for parsing, exact-byte materialization, Bitrix upload payload, Telegram document
  outbox, retries, invalid artifacts and cleanup.
- Full local regression, dependency audit and CI.
- Production migration/config smoke and direct-channel round trip only with an explicitly approved
  test recipient.

Local implementation evidence on 2026-08-12:

- The final Bitrix adapter resolves only a valid unexpired HMAC handoff, uploads exact bytes with
  `imbot.v2.File.upload` from the selected profile bot and never falls back to a visible URL.
- Employee Telegram uses multipart `sendDocument`; text and each file are independent durable
  outbox parts, so provider retry cannot recompute the model result or duplicate a successful part.
- Automation delivery uses the same independent-part contract. Exact bytes are retained with
  directory/file modes `0700`/`0600`; cleanup protects every open delivery and bounds retained raw
  bytes to the configured age and total size.
- Migration `085` is registered and repeatable by construction. Nginx removes the confirmed-unused
  `mcp.m4s.ru/zoom-export` location; smoke requires 404 there and verifies canonical exact bytes.
- Focused safety/transport tests: `59 passed`. Full local regression: `1927 passed, 44 skipped`;
  skipped DB matrices remain mandatory in CI. Compilation, changed-file pyflakes and
  `git diff --check` passed.

## Risks

- A provider may accept a file while the connection fails, producing an ambiguous outcome.
- Base64 upload increases transient memory; current artifact size limits must remain enforced.
- Removing the legacy host breaks any forgotten old link; the owner explicitly states none remain.

## Rollback

Disable native artifact delivery, restore the previous code/Nginx/config backups and keep additive
outbox columns for forensics. Do not restore public MCP; a temporary export compatibility route may
be restored only from the backed-up exact Nginx block if a forgotten valid link is proven.

## Known gaps and follow-up

CI, production migration/Nginx rollout and provider-native live acceptance remain pending. No
employee file or message was sent during local verification.
