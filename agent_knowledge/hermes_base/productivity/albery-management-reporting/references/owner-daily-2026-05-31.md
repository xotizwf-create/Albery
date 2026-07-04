# Owner daily example — 2026-05-31

Session pattern: scheduled Albery owner-daily cron run where the day had little/no chat or Zoom activity, and the useful management signal came from Bitrix task status + human comments + previous owner context.

## Durable lessons

- When active chats and Zoom calls are empty for the report date, do **not** make the final output a technical "no data" inventory. Treat it as a task/comment-driven management day: summarize what moved, what remains unaccepted, and which risks need owner attention.
- Human comments on tasks can be the main evidence source. In this session, several "completed" tasks still required management interpretation because comments said variants of "not my zone", "already exists", or "not required". That should become a recommendation about task-owner validation and acceptance criteria, not merely a status update.
- If a task is marked completed but the acceptance artifact is unclear, report it as "formal completion pending acceptance". Example classes: a created workflow/kanban/checklist that still needs a process owner to accept fields/stages/checkpoints; a document migration task closed with an alternative storage/import proposal that still needs an explicit chosen norm.
- If the job instruction gives an allowlist and separately uses an inconsistent count (e.g. says "allowed four" but later "allowed five"), use the explicit names as the allowlist. Do not infer extra recipients.
- Excluding the owner from daily manager dispatch can coexist with mentioning owner-facing dependencies in `report_text`; do not put the owner in `manager_messages` when the job says daily automation must not send to them.
- After `save_owner_daily_report`, use `list_pending_owner_recommendations` as the source of truth for the Telegram approval text. The order returned may differ from the save payload; preserve the returned `recommendation_text` verbatim and do not add recipient headings, IDs, priorities, or explanations.

## Example evidence-to-recommendation transforms

- Evidence: legal funnel projects/kanban/checklist were created, but status is awaiting control.  
  Recommendation shape: ask the process owner/legal reviewer to accept against concrete criteria (stages, required fields, checklists, approval points) or return a short fix list.

- Evidence: working documents remain on a coordinator drive and an import-file alternative is proposed for "О компании".  
  Recommendation shape: ask the operational owner to choose one explicit norm and name the maintenance owner, rather than treating the task as closed by workaround.

- Evidence: prior payment was outside the payment calendar, and a regulation was expected.  
  Recommendation shape: request a one-page payment-calendar regulation covering who enters payments, deadlines, two-week review, allowed variance, urgent exceptions, and the person accountable for final weekly actuality.

- Evidence: tasks from call summaries are closed as wrong owner/already done/not required, while a supply-related item has no human answer.  
  Recommendation shape: ask the call-task owner to validate assignees before dispatch, avoid duplicates, distinguish "do" from "review", and escalate critical supply risks to a real owner.
