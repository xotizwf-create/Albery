# Deep Friday owner weekly report (v3-quality pattern)

Use this reference when a Friday Albery cron asks for a deep owner weekly report comparable to the 05.06.2026 v3 report.

## Quality bar

The report is not a short summary. It should read as a 6+ page management note with numbered sections and detailed acceptance of C-1 work. The highest-value section is the per-person acceptance of Наталья Горюнова and Артур Степанян by **each weekly task**.

Required emphasis:
- Sections 1, 2, 6, 7, 8, and 10 must be substantive; do not collapse them to a few lines.
- Section 6 should contain one numbered item per task: `Факт: <table/source> ↔ <call evidence>. → <принимать / частично / заблокировано / не принимать>. Артефакт/контроль: ...`.
- A Google-table status such as `выполнено` is not enough by itself. Cross-check against Zoom discussions and require a verifiable artifact/link or an explicitly cleared blocker.
- If the Friday control call is absent, say so plainly and downgrade acceptance; do not infer a full weekly acceptance from a short Thursday check-in.

## Source-gathering rules that mattered

- Start with live instructions and the `owner_weekly_report_creation` context guide, then check readiness for the whole Monday-Friday period.
- Generate only missing Zoom reports reported by readiness, using the `zoom_processing` contract.
- Gather saved Zoom analytical notes for every day of the week, not just the Friday call.
- Pull the weekly-task source from the Google sheet / company file named like `Операционная встреча. Албери 2.0`, sheet/list `Задачи недели`. If unavailable, fall back to the Friday/weekly Zoom report section about Наталья and Артур and explicitly mark the fallback.
- Read regulation/company files by filename through `list_company_files` + `get_company_file`; do not conclude a regulation is absent after one keyword-search miss. Important filenames include meeting rhythm, decision matrix, reporting rules, task-setting rules, and result-fixation rules.
- For Bitrix numbers, collect both week-created/closed activity and end-of-week open/overdue state. Separately notice overdue `Итоги созвона ...` / `ознакомьтесь` tasks.
- For leader chats, count activity/silence by day from raw chat transcripts; if no messages are found, state `нет данных`/silence rather than inventing chat activity.
- Use the previous weekly report to describe dynamics; if unavailable, label dynamics as limited.

## Identity and responsibility pitfalls

- Identify people only from transcript speaker labels and factual speech content. Never accuse someone of not being under their real name based on Zoom account metadata or the meeting topic (for example, `Координатор` or `Зал персональной конференции`).
- Александр Никитенко is the owner/requester in these reports. Do **not** put him as the executor/responsible person in owner decision tables. Use Евгений Палей, Наталья Горюнова, Артур Степанян, or Дмитрий Строгонов as executors when appropriate.
- Owner-facing text must not mention MCP, internal IDs, tool names, or pipeline mechanics.

## Delivery/approval discipline

When the cron wrapper says final output is automatically delivered, do not use Telegram/send_message yourself. Prepare the owner-facing approval draft in the final response.

Do not automatically send the weekly PDF to Евгений Палей. The Friday flow is:
1. Save the weekly report.
2. Show the owner the draft/preview and ask `отправляй / не отправляй / правки`.
3. Only after explicit approval in a later turn, send the PDF to the exact approved Bitrix recipient(s).

For separate overdue-manager reminders:
- Enforce the allowlist exactly.
- Draft one message per allowlisted manager with open overdue tasks only; no coaching or development recommendations.
- Put `Итоги созвона ...` / `ознакомьтесь` overdue tasks first.
- Show drafts to the owner separated by `———` and do not send until explicit approval.
