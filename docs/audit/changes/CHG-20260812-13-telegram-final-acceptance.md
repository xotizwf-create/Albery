# CHG-20260812-13: Retire redundant native Telegram and complete identity acceptance

- Status: deployed
- Date opened: 2026-08-12
- Related decisions: [ADR-0005](../decisions/ADR-0005-channel-neutral-agent-runtime.md)
- Bitrix engineering task: pending

## Goal

Retire the separately degraded native Hermes Telegram transport now that the channel-neutral
employee service owns profile bots, verify that service end to end, and add only explicit stable
Telegram-to-Bitrix actor mappings.

## Before

The employee profile bot and client/IU transport pass structural smoke, but no user-visible profile
round trip has been accepted. The native owner Hermes platform rejects its old token. Delegated
Bitrix actions from Telegram fail closed until a stable identity row explicitly names a Bitrix user.

## Target / after

- Native Hermes Telegram is explicitly retired without revoking or guessing a bot identity. The
  Hermes gateway remains active for its scheduler, but it no longer retries a rejected Telegram
  credential or competes with profile-bot polling.
- A named approved employee profile completes inbound message, shared agent/MCP decision, durable
  outbox and same-profile outbound delivery.
- Telegram-to-Bitrix actor mapping is written only after both immutable IDs and the person are
  independently verified; wrong/unknown identities remain denied.

## Changed boundaries and files

Expected protected env/config backup, removal of the rejected legacy Telegram binding, safe gateway
reload, access-row acceptance, smoke and audit. Employee profile bot secrets remain solely in the
Albery agent registry.

## Safety and privacy

Never expose tokens in shell output, Git or logs. Token revocation is irreversible and therefore
requires an exact bot identity, backup and final explicit confirmation immediately before action.
No real Telegram message is sent without an exact recipient and preview.

## Verification plan and evidence

Read-only identity/credential inventory, `getMe`, fail-closed access tests, controlled round trip,
durable journal verification, service health and full smoke.

Read-only discovery and local implementation evidence on 2026-08-12:

- The active channel-neutral `main` profile bot has one stable allowed Telegram identity and passes
  provider identity checks; the separate Hermes platform loops on an already rejected old token.
- There are no durable channel-neutral inbound/outbound rows yet, so a real profile round trip has
  not been inferred from configuration alone. All `bitrix_user_id` mappings remain null; no person
  was guessed and no delegated Bitrix identity was granted.
- Deploy smoke now accepts the old platform only when it is connected or the protected production
  env explicitly records `HERMES_TELEGRAM_RETIRED=1`. This does not weaken checks for the active
  `albery-tg` profile service or IU customer transport.

## Risks

Rotating the wrong bot silences a working channel. A guessed identity mapping can authorize writes
as the wrong employee. Telegram network ambiguity can duplicate a manually retried message.

## Rollback

Restore the protected Hermes env backup and remove the explicit retirement flag to re-enable the
legacy platform. Remove an incorrect access mapping and disable the channel-neutral feature flag if
identity acceptance fails. No token is revoked as part of this change.

## Known gaps and follow-up

Production evidence on 2026-08-12:

- The rejected legacy `TELEGRAM_BOT_TOKEN` key was removed from the protected Hermes environment
  after backup; no token was guessed, rotated or revoked. `HERMES_TELEGRAM_RETIRED=1` now records the
  intentional state in Albery's protected environment.
- `hermes-gateway.service` restarted cleanly and remains active for scheduler/orchestration only.
  Its upstream state file still contains the historical word `retrying`, but the credential key is
  absent and there are no post-restart Telegram or service errors.
- The active Albery `main` profile passes Telegram `getMe`, and `albery-tg.service`, durable tables,
  access checks and full deploy smoke are healthy. CI runs `31599857089` and `31599857094` passed.

A named recipient and exact message preview are still required for the first user-visible profile
round trip. Stable Telegram-to-Bitrix actor mapping remains fail-closed until both immutable IDs and
the person are explicitly confirmed. This change therefore remains `deployed`, not `verified`.
