# Owner weekly cron 2026-06-13 — Friday-off calendar, Bitrix staleness, PDF timeout

Session pattern for Albery Friday weekly automation.

## Durable lessons

- The live job may define the company week as **Monday–Thursday** because Friday is a day off. In that case:
  - `period_end` is Thursday, not the cron run Friday.
  - The rhythm table should still show Friday as `— выходной —`.
  - Do not write “Friday control meeting missing” as a violation; evaluate the weekly/control meeting on Thursday.
- When the job asks for a deep v3-style weekly report, section 6 is the value center: for each Наталья/Артур task, compare `Google/table status ↔ Zoom evidence ↔ artifact/blocker` and give a per-task verdict.
- Bitrix freshness must be checked before using counts/reminders. If an active Zoom week has zero new Bitrix tasks and the latest synced task predates the period, report the task line as stale/suspect (likely Marketplace/REST sync stopped), add an owner risk/decision, and skip overdue reminder drafts for that run.
- In cron delivery wrappers, do not use chat `send_message`; the final response is delivered automatically. If the live job explicitly requests a preview PDF via an Albery PDF tool and that tool times out, do not claim success. Provide a text fallback and state that the production PDF was not sent.

## Final fallback wording that worked

When PDF preview timed out after the report had been saved:

```text
Недельный отчёт собственнику за <period> готов и сохранён.

PDF-превью владельцу отправить не удалось: подтверждение формирования/доставки PDF не пришло в отведённое время. Поэтому даю краткое содержание текстом. Евгению Палею PDF не отправлял.

<short verdict + 3–5 key points>
```

Avoid naming MCP/tool/report ids in the owner-facing text.