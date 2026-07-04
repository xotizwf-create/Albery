# Owner daily report — 2026-06-03 cron example

Use this as a compact example for scheduled Albery owner-daily runs that save a report plus recommendation-task drafts and then ask the owner for approval.

## Durable workflow lessons

- The daily automation may ask for Bitrix **tasks**, not personal messages. In that mode, `manager_messages[].message_text` must be only the numbered recommendation body. Do not include greetings, `Рекомендации:`, or the system task header; the dispatch tool adds that header itself.
- The scheduled run must save the report first, then call `list_pending_owner_recommendations(report_date=...)` and use those returned `recommendation_text` values verbatim in the final approval message.
- If `recommendations_count == 0`, apply the wrapper silence rule exactly. In this session the outer cron wrapper required `[SILENT]` for true silence, while the Albery job text said “empty response”; the outer delivery wrapper controls the final suppression token.
- Never auto-create recommendation tasks in the report-generation pass. Wait for the owner's later explicit approval (`ставь` / equivalent).
- Enforce the recipient allowlist literally. If prose says the wrong count (e.g. “разрешённая пятёрка”) but lists four names, use the listed names and exclude everyone else, especially Евгений Палей when the job says daily automation must not send to him.

## Source-handling notes from the run

- Readiness can be ready even when a chat attachment OCR is missing. Attempt OCR if the job asks to close missing sources; if OCR remains missing, state the limitation in the saved report and do not infer image contents.
- Saved Zoom analytical notes are sufficient for owner-daily synthesis when ready; do not reread transcripts unless generating missing Zoom reports.
- Bitrix recommendation tasks from the prior day are useful continuity: read `search_tasks` and `get_task_comments` to distinguish formal task closure from a substantive manager reaction.

## Final approval message shape used

```text
Согласуйте, какие рекомендации ставим задачами (каждому — задача «Рекомендации ДД.ММ», дедлайн до 10:00 следующего дня):

<ФИО>:
1. <verbatim recommendation item>
2. <verbatim recommendation item>

———

<ФИО>:
1. <verbatim recommendation item>

Ставлю задачи в Битрикс? Ответь: ставь / не ставь / правки по <имя>: <текст>.
```
