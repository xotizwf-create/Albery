# Owner daily cron run — 2026-06-01

Session-specific example for Monday owner daily report generation.

## Durable lessons

- For a Monday `report_date`, the previous continuity report should be the previous working day (usually Friday), not Sunday.
- `get_previous_owner_daily_context(report_date=...)` can return the previous calendar day and no report when that day is Sunday. In that case, check recent owner daily reports and use the latest working-day report instead of creating weekend reports.
- Weekend reports/recommendations are not generated unless the owner explicitly requests a weekend report.
- When the job has an allowlist, save dispatchable `manager_messages` only for those employees. Non-allowlisted observations can remain in owner-facing `report_text`/`recommendations`.
- After saving, `list_pending_owner_recommendations` is the source of truth for approval copy; paste `recommendation_text` verbatim into the Telegram approval format.

## Example outcome

For 2026-06-01, the job saved an owner daily report and produced three approval blocks for allowlisted recipients: Артур Степанян, Наталья Горюнова, Сергей Виноградов. Александр Никитенко had no sufficient same-day factual basis, and Евгений Палей was explicitly excluded from daily dispatch.
