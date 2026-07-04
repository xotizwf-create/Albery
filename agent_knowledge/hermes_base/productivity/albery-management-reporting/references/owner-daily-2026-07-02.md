# Owner daily 2026-07-02 — missing previous report, no same-day chats/Zoom

Session pattern for scheduled owner-daily reporting.

## Situation

- Target date from Moscow time was `2026-07-02`.
- Initial readiness for target date had no chats and no Zoom calls, but `ready_for_owner_daily=false` because the previous owner daily report did not exist.
- `list_periods` showed the previous available business source day was `2026-07-01` with Zoom calls and no chats.
- Readiness for `2026-07-01` was ready: Zoom reports already existed and previous owner report (`2026-06-30`) existed.

## Useful workflow

1. Do not stop just because the target date has no same-day messages/Zoom and readiness is false.
2. If the only blocker is missing previous owner daily, identify the previous available source day via `list_periods` / readiness checks.
3. Generate and save the missing previous owner report first, using existing Zoom analytical notes and Bitrix task evidence.
4. Re-check target-date readiness. It should become ready once the previous report exists.
5. For a no-activity target day, build the owner daily mostly from Bitrix movement plus previous-owner continuity, not from empty chats/Zoom.
6. Save `manager_messages` only when there is a fresh factual basis for allowlisted recipients.
7. Verify with `list_pending_owner_recommendations` and use returned `recommendation_text` verbatim for approval text.

## Evidence pattern in this session

- For `2026-07-01`, the report focused on two saved Zoom notes, task status/comments, and previous owner report continuity.
- For `2026-07-02`, no chats/Zoom existed; Bitrix task evidence showed Наталья had positive movement, while Артур had two unresolved 01.07 tasks without human comments and additional 03.07 workload.
- Only Артур received a dispatchable recommendation draft; Наталья, Сергей, and Александр were omitted from `manager_messages` due to lack of fresh negative factual basis for the target day.

## Pitfall

If a target day is quiet, do not manufacture recommendations for every allowlisted manager. The allowlist is a ceiling, not a quota. Recommendations still need a fresh fact: task status, comments, previous recommendation response, chat/Zoom evidence, or a repeated unresolved risk visible in current sources.
