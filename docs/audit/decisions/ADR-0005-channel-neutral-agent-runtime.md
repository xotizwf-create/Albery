# ADR-0005: Channel-neutral agent runtime

- Status: accepted
- Date: 2026-08-11
- Owners: Albery engineering
- Supersedes: channel-specific Telegram agent prompts and direct-delivery runtime

## Context

Bitrix and Telegram profiles already share the `agents` registry and private `agent-<slug>` MCP
capability boundary, but they do not execute the same turn contract. Bitrix assembles identity,
instructions, skills, personal learning, history, requester context and automation destination in
`b24bot.py`; additional Telegram bots use a short role-only prompt in `tg_multi.py`. Telegram access
also fails open when the allowlist is empty, delivery is direct and offsets live in memory.

The desired product invariant is one agent profile with several channel bindings. Channel choice
must affect transport and presentation only, not identity, role, capability, instruction or safety
logic. The public IU customer bot is deliberately outside this invariant because it is an untrusted
zero-tool funnel runtime rather than an employee agent.

## Decision

- `agents.slug` is the single identity and capability owner. A profile may have Bitrix, Telegram,
  both, or no external binding; Agent Center attaches or removes a channel on the existing profile
  instead of creating a second logical agent.
- All employee-agent turns use one pure prompt/context builder. It receives a channel adapter for
  presentation, conversation identity, actor identity and history. Role, core instructions,
  selected skills, personal instructions, capability truth, confirmation rules and automation
  guidance are shared.
- Conversation history remains isolated by `(agent_slug, channel, conversation_id)` so identical
  numeric ids from different channels cannot collide. Channel adapters may expose channel-native
  history tools, but the model must see the same behavioral core.
- Telegram employee access is fail-closed. A profile with no active allowlist entries answers with
  an explicit denial and never calls Hermes or MCP. Access rows may link a stable Telegram user id
  to a Bitrix user id. Actions on behalf of an employee require that explicit link; usernames alone
  never authorize impersonation.
- Telegram profile-bot updates, turns and outbound replies are durable PostgreSQL stages. Provider
  offsets advance only after the raw update is committed. Known delivery failures retry the stored
  result; timeout/connection ambiguity stops for review rather than blind resend.
- Automation destinations are typed as `{channel, profile, conversation_id}`. A request defaults to
  the originating channel and conversation. Brain result and delivery retry remain separate; the
  Telegram adapter sends with the profile's Telegram identity.
- The existing IU customer workspace remains isolated and zero-tool. Native Hermes Telegram
  transport is retired explicitly after profile-bot cutover; the gateway process may remain for
  reviewed scheduler jobs, which must enter Albery's shared heavy-process limit.

## Alternatives considered

- Keep two prompts synchronized manually: rejected because drift already occurred and tests cannot
  prove future edits are applied in both places.
- Reuse the IU workspace tables for employee agents: rejected because funnel control modes, CRM
  transitions and customer zero-tool policy are different domain semantics.
- Treat a bare numeric destination as Telegram or Bitrix heuristically: rejected because ids overlap
  and misdelivery is worse than an explicit failure.
- Keep username-only access: rejected because usernames can change and cannot safely identify a
  Bitrix actor for delegated writes.

## Consequences

- Presentation remains channel-specific: Bitrix BBCode/reactions and Telegram HTML/plain text are
  adapters around one behavioral prompt, not duplicated business logic.
- A newly attached Telegram bot is intentionally unavailable until at least one person is allowed.
- Existing Telegram-only profiles can be migrated in place; duplicate built-in Telegram profiles
  require an explicit merge plan and are not automatically combined by name.
- PostgreSQL migrations and new worker stages add operational complexity, but remove replay and
  silent-delivery failure classes.
- Native Hermes transport retirement is reversible configuration, not credential rotation. A
  rejected token is removed from the active gateway environment without guessing or revoking a bot.

## Verification / revisit trigger

Verify prompt equivalence across channels, profile/tool/skill parity, channel-isolated history,
fail-closed first contact, mapped and unmapped actor behavior, restart at every inbox/brain/outbox
stage, ambiguous delivery review, typed automation delivery, per-profile sender identity and
unchanged IU customer behavior. Revisit if Telegram provides a provider idempotency key or Albery
introduces another employee channel.
