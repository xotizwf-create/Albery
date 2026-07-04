# Owner daily 2026-06-09 — recommendation extraction pitfall

Session pattern:

- User asked whether the current-day owner daily report existed; readiness showed the report was missing but all source prerequisites were ready.
- The report was generated and saved successfully.
- Addressable recommendations were first saved in `manager_recommendations` with fields such as `manager_full_name`, `manager_bitrix_user_id`, `recommendation_text`, `subject`, `due_date`, and `priority`.
- `list_pending_owner_recommendations(report_date="2026-06-09")` returned `recommendations_count: 0` even though the human-readable report contained recommendation text.
- Re-saving with `manager_recommendations` using `manager_name` also returned zero pending recommendations.

Lesson:

For owner daily reports where recommendations must become pending owner-approved Bitrix tasks, use `manager_messages` with the documented object shape and put the final dispatchable recommendation body in `message_text`. Treat `manager_recommendations` as analytical/report content unless the live MCP instructions explicitly say otherwise.

Verification rule:

After saving, always call `list_pending_owner_recommendations`. If recommendations were intended but count is zero, do not tell the owner the recommendation-dispatch stage is ready; fix the save payload shape and verify again.
