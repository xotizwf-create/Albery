# Owner daily report cron example — 2026-05-28

This reference captures reusable details from a successful Albery owner daily report generation run. Treat it as an example, not a replacement for live MCP instructions/contracts.

## Scenario

- Scheduled evening cron job; no user present.
- Required output: save owner daily report for the current Moscow date and return owner approval text for pending Bitrix recommendation drafts.
- Local restriction: do not call `upsert_ai_instruction`; do not mention technical IDs, MCP, tools, or internal pipeline in the Telegram-facing output.
- Bitrix sending was explicitly forbidden in the cron pass; only save report + show final texts for approval.

## Sequence used

1. Read live instructions with `start_here_always_read_ai_instructions`.
2. Read `get_context_guide(intent="owner_daily_report_creation")`.
3. Determine Moscow date (`2026-05-28`) with a date command.
4. Call `get_report_readiness(date_from="2026-05-28", date_to="2026-05-28")`.
5. Readiness showed:
   - owner daily did not exist;
   - previous owner report existed;
   - daily chat reports disabled;
   - no chats with messages;
   - 1 Zoom call and 1 ready Zoom analytical report;
   - no missing Zoom reports;
   - ready for owner daily.
6. Gathered sources:
   - `list_zoom_calls` for the date, using `analytical_note` rather than raw transcript;
   - `get_previous_owner_daily_context` / recent owner report context;
   - `get_org_structure` for exact names and Bitrix user IDs;
   - `search_tasks` for task status/progress;
   - `search_company_knowledge` for recommendation principles/regulations;
   - `get_report_contract(category_key="owner_daily")`.
7. Saved with `save_owner_daily_report`, including `manager_messages` only for allowed recipients with factual day-specific grounds.
8. Called `list_pending_owner_recommendations` and used returned `recommendation_text` verbatim to format the final Telegram approval request.

## Report content pattern

The saved owner report emphasized:

- Main signal: operational contour became clearer through P&L/Zoom review and task creation, but sales for new SKUs had not started due to shipment delays.
- Movement: tax payments were closed; Zoom decisions became visible; some tasks were assigned.
- Stuck areas: factory shipments, turnover table, decision matrix control, financial model/OПиУ, dividend account, and same-day open tasks.
- Dynamics versus previous day: Zoom visibility improved, but the recurring issue remained — decisions without precise owners/dates/artifacts.
- Risks: shipment dates 03.06/10.06, inability to distinguish report-task overdue from operational overdue, unapproved financial model, blocked dividend task.

## Recommendation draft pattern

For each allowed recipient with a real factual basis:

- Start with direct greeting: `<Имя>, приветствую!`
- Use `Рекомендации:` followed by a numbered list.
- Each item should contain concrete action + result criterion + date when possible.
- For the owner, include `Главный вывод дня — ...` before `Рекомендации:`.

Examples of good grounds from the run:

- Артур Степанян: Zoom tasks on factories, turnover table, decision matrix, financial model, report-task tags.
- Наталья Горюнова: new order/minimum lots, designer, control purchases, contractor list, dividend task blocker.
- Александр Никитенко: task follow-up and daily stock screenshot control.
- Евгений Палей: owner decisions needed on dividend account, financial model tax scenario, and accounting for owner involvement in OПиУ.

## Owner approval final output

Use exactly:

```text
Согласуйте что мы отправляем:

<recommendation_text 1>

———

<recommendation_text 2>

———

<recommendation_text N>

Отправляю в Битрикс? Ответь: отправляй / не отправляй / правки по <имя>: <текст>.
```

Do not add recipient headers, priorities, IDs, report IDs, or explanatory prose.
