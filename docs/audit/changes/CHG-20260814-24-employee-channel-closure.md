# CHG-20260814-24: Close remaining employee-channel acceptance

- Status: in_progress
- Date opened: 2026-08-14
- Related decisions: [ADR-0005](../decisions/ADR-0005-channel-neutral-agent-runtime.md), [ADR-0006](../decisions/ADR-0006-channel-native-artifact-delivery.md)
- Approval: owner approved roadmap items 1, 3 and 4 on 2026-08-14
- Bitrix engineering task: pending

## Goal

Close the two remaining employee-channel acceptance boundaries: prove one exact native attachment
delivery from the owning bot into the approved Bitrix conversation, and either bind the approved
Telegram stable identity to the exact same Bitrix person or explicitly retain fail-closed delegated
Bitrix actions.

## Before

- Employee Telegram message/reply, reaction lifecycle and native file delivery are verified.
- A fresh durable Bitrix text reply is verified, but native Bitrix file delivery has only fake/unit
  evidence.
- The active Telegram access row has a stable Telegram id but no confirmed `bitrix_user_id`, so
  Telegram-originated delegated Bitrix actions correctly fail closed.

## Target / after

- Use an existing approved Bitrix conversation and an innocuous uniquely named text attachment.
  Preview exact target/name/bytes before the provider call and prove one provider message/journal
  identity without replay.
- Resolve both channel identities from immutable provider/database evidence. Never infer a person
  from display name or username. Apply a mapping only if the exact Bitrix user id is unambiguous and
  separately confirmed; otherwise preserve the null mapping and record the reason.
- Promote only the evidence-backed roadmap/change status; retain any untested delegated action as
  an explicit boundary.

## Changed boundaries and files

- Read-only production identity and provider inventory.
- Existing Bitrix native adapter and attachment store; code changes only if verification exposes a
  defect.
- `telegram_bot_access.bitrix_user_id` only after immutable identity confirmation and a protected
  targeted database backup.
- Audit roadmap, current architecture and master diagram after verified evidence.

## Safety and privacy

No employee file/message or identity grant is sent/applied from a guessed target. Audit output
contains identifiers only where required for exact acceptance and never includes credentials or
conversation content. The acceptance file contains no business or personal data.

## Verification plan and evidence

Read-only inventory first. Run focused native-file/identity tests and the full suite if code changes.
Before any provider/database mutation require exact preview, empty relevant queues, private backup
and a named rollback. Verify provider identity, one journal row, no duplicate delivery, active
services, smoke/self-check and fresh journals.

## Risks

- Sending to the wrong dialog/person.
- A lost provider response could make delivery ambiguous.
- An incorrect cross-channel mapping could authorize writes as another employee.

## Rollback

File acceptance is additive and is not deleted; record its provider identity. A mapping rollback
restores the targeted pre-change `telegram_bot_access` row under a transaction after checking no
delegated turn is active. Any code rollback uses the protected code/database backups and normal
empty-work restart gate.

## Known gaps and follow-up

Read-only production evidence resolves the existing private acceptance route to main Bitrix bot
`24`, dialog/user `16`, and the active Telegram stable identity to `1451982360`; the Telegram access
row still has `bitrix_user_id = NULL`. The cross-provider person equivalence cannot be proven from a
display name and therefore still needs the owner's exact confirmation. The native file target is
known, but provider send and delegated mapping remain blocked until their exact preview is approved.
