# Owner weekly report + overdue reminders — 2026-06-08..2026-06-12

Session pattern worth reusing for Friday weekly automation.

## What the job required

- Build a weekly owner report for Monday–Friday, save it, and present an approval draft for PDF delivery.
- Do **not** send the PDF automatically; only after explicit owner approval (`отправляй`).
- The report format was strict: Russian, table-heavy, no recommendations section, and `recommendations=""` on save.
- Prepare overdue-task reminder drafts only for an explicit manager allowlist:
  - Сергей Виноградов — 31157
  - Наталья Горюнова — 30237
  - Артур Степанян — 29039
  - Александр Никитенко — 31195
- Евгений Палей must not receive overdue-reminder messages.
- Reminder drafts are personal Bitrix messages only, not owner-daily recommendation tasks.

## Durable workflow lessons

1. Weekly owner runs need the same live-instruction/readiness discipline as daily owner reports, but use `owner_weekly_report_creation` context and save through `save_owner_weekly_report`.
2. If weekly readiness reports missing Zoom reports, generate those first from the `zoom_processing` contract before compiling the owner report.
3. Use saved Zoom analytical notes as the primary source for:
   - meeting rhythm/regulation compliance;
   - behavioral factors with Zoom date and timecode;
   - Friday/weekly C-1 section when present.
4. Daily chat reports may be disabled; for weekly chat activity/silence, use raw chat transcript tools or period/chat indexes rather than trying to create chat reports.
5. In cron final output, provide the owner-facing approval message directly. Do not call Telegram/send-message tools; the cron delivery wrapper sends the final response.
6. Weekly PDF delivery is a separate later step: call `send_owner_weekly_report_pdf(confirm=true)` only after the owner approves the exact report.
7. Overdue leader reminders are a separate track from owner-daily recommendations. Do not use `list_pending_owner_recommendations` or `send_owner_recommendations_to_bitrix` for them.
8. For reminders, search overdue open tasks by the allowlisted responsible user IDs, draft one message per person with overdue tasks, put `Итоги созвона`/`ознакомьтесь` tasks first, and omit people with no overdue tasks.

## Owner-facing approval structure used

```text
Недельный отчёт собственнику за <period> готов.

<verdict + 2–3 key points>

Отправляю PDF Евгению в Битрикс? Ответь: отправляй / не отправляй / правки: <текст>.

---

Напоминания руководителям по просроченным задачам:

<recipient draft>

———

<recipient draft>

Отправляю напоминания руководителям? Ответь: отправляй / не отправляй / правки по <имя>: <текст>.
```

If there are no overdue tasks for the allowlisted managers, omit the reminder block entirely.
