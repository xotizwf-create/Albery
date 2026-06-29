# Employee Context MCP

Domain-scoped MCP server for Claude Code / Claude Desktop. It exposes the local PostgreSQL analytics database through domain tools instead of unrestricted SQL, and also contains explicitly confirmation-gated workflow tools that can write reports/instructions or perform external Bitrix actions.

Safety rule: tools with side effects must follow `review → preview → explicit user confirmation → call with confirm=true`. Read-only labels are not enough; check each tool contract and handler before treating it as safe.

Important permission model: Albery MCP must not ask for 1000 confirmations during normal analysis. A user request to analyze, prepare a report, inspect tasks, or collect recommendations gives the assistant permission to use the relevant fast read-only route inside that work scope. Extra confirmation is needed only for external actions and high-impact current-state changes.

## Fast route / permission model

For analysis and report work, use short routes instead of broad random exploration:

1. `start_here_always_read_ai_instructions` — read the live execution contract once.
2. `get_context_guide(intent=...)` — pick the shortest workflow for the current task.
3. Use index tools before detail tools: `get_period_index`, `get_report_readiness`, `list_chats`, `list_zoom_calls`, `list_company_files`.
4. Read exact evidence with bounded arguments: date range, `dialog_id`, `call_id`, `bitrix_task_id`, `limit`, `offset`.
5. Build a preview for any action.
6. Call external action tools only after the owner approves the exact preview and pass `confirm=true`.

The MCP registry exposes risk metadata for every tool: `risk_class`, `permission_scope`, `side_effects`, `requires_confirm`, `writes_db`, `external_action`, and `route_hint`. This metadata is for routing and safety checks; it is not a new argument the assistant has to pass.

## Tools

- `start_here_always_read_ai_instructions` - mandatory first tool for any company analysis/report/recommendation/answer; reads live rules from "Настройки -> Инструкции для ИИ" and returns the execution contract.
- `health` - PostgreSQL connectivity check.
- `get_context_guide` - navigation rules for using the database systematically after `start_here_always_read_ai_instructions`. Pass `intent` (e.g. `owner_daily_report_creation`) to get only the workflow and sources for the current task; without it you get the full guide plus a compact index of instruction folders.
- `get_ai_instructions` - re-read live instructions; `start_here_always_read_ai_instructions` already returns the full text, so pass `path` to fetch only one folder (e.g. `path="Формирование отчетов / Ежедневный отчет по компании"`).
- `get_report_contract` - read the active report-generation contract; for daily chat reports use `category_key=chat_analysis`.
- `list_available_sources` - table availability and row counts.
- `list_periods` - recent dates with chat messages/reports.
- `get_company_profile` - editable company profile text from PostgreSQL.
- `list_company_files` - list every file/folder available in the company knowledge section.
- `get_company_file` - read the full text and source metadata for one company knowledge file.
- `search_company_knowledge` - search "О компании", including Google Drive mirrored documents.
- `get_period_index` - compact period manifest with counts and top chats.
- `get_report_readiness` - one-call readiness for daily/weekly/owner reports: per day, which active chats have messages and still need a daily report, which Zoom calls still need an `analytical_note`, and whether the current/previous owner daily reports exist. Use it before report building instead of probing each chat/Zoom separately.
- `get_org_structure` - departments, users, managers, memberships.
- `search_tasks` - Bitrix task search; rows include `comments_total_count` and `comments_human_count`.
- `get_task_comments` - read the comment thread of one task by `bitrix_task_id`, with author names and BB-code-cleaned text (system notifications excluded unless `include_service=true`).
- `create_bitrix_task` - create one Bitrix task only after previewing the exact title/responsible/deadline/observers/periodicity and receiving explicit user confirmation; `confirm=true` is mandatory.
- `delete_bitrix_task` - delete one exact Bitrix task only after showing task details and receiving explicit user confirmation; `confirm=true` is mandatory.
- `list_chats` - active chat listing.
- `search_messages` - raw chat message search.
- `get_chat_transcript` - raw chat transcript by `dialog_id` and period, including OCR for image/PDF attachments.
- `get_chat_ocr_status` - check whether image/PDF attachments have OCR before a report.
- `process_chat_ocr` - run OCR processing for image/PDF attachments through the local app workflow.
- `list_zoom_calls` - Zoom recordings/calls with dates, technical topics, participants, and transcript counts.
- `get_zoom_call_transcript` - one Zoom call with participants and raw transcript segments.
- `search_zoom_transcripts` - search inside Zoom transcript segments.
- `get_previous_owner_daily_context` - read only the previous calendar day's current owner daily report for continuity when creating a new owner daily report.
- `get_owner_reports` - read recent current owner daily/weekly reports for recommendation continuity.
- `save_owner_daily_report` - write a generated daily owner report to `owner_daily_reports`.
- `save_owner_weekly_report` - write a generated weekly owner report to `owner_weekly_reports`.
- `upsert_ai_instruction` - create or update an instruction folder under "Настройки -> Инструкции для ИИ" only after previewing the exact path/content and receiving explicit confirmation; `confirm=true` is mandatory, and overwrites can use `expected_current_content` to reject stale previews.
- `get_compact_export` - compact bundle generated from live PostgreSQL data.

## Recommended Usage Pattern

For the full agent workflow, confirmation rules, and instruction map, see `docs/playbooks/mcp-agent-workflow.md`.

For any company analysis, report, recommendation, or answer, start with `start_here_always_read_ai_instructions`. It reads the live editable rules from "Настройки -> Инструкции для ИИ" and returns the mandatory execution contract.

For new analysis requests, call `get_context_guide` after `start_here_always_read_ai_instructions`. Pass `intent` to get only the route for the current task (for example `get_context_guide(intent="owner_daily_report_creation")`); without `intent` it returns the full guide. The routes it covers:

- company rules, regulations, and documents: `search_company_knowledge`, `list_company_files`, `get_company_file`, then `get_company_profile` if the full tree is needed;
- employees, managers, and departments: `get_org_structure`;
- date-based analysis: `get_period_index`, then `search_tasks`, `search_messages`, and Zoom tools;
- chat evidence: `list_chats`, then `get_chat_transcript`;
- meeting evidence: `list_zoom_calls`, then `get_zoom_call_transcript` or `search_zoom_transcripts`.
- owner daily report creation: call `get_report_readiness(date_from=report_date, date_to=report_date)` once to learn which chat/Zoom sources are missing and whether the previous owner daily exists; then close the missing sources, read the previous day through `get_previous_owner_daily_context(report_date=YYYY-MM-DD)`, and use relevant chat reports, Zoom reports, company knowledge, and concrete Bitrix tasks before saving.
- recommendations and management answers: the instructions already arrived from `start_here_always_read_ai_instructions`; read prior owner context, relevant chat reports, company knowledge, and concrete Bitrix tasks before answering.

This keeps the model from scanning sources randomly and gives it a stable map of the system.

Complex requests may take as long as needed for quality, especially when many sources and tools are genuinely required. Still, the assistant should optimize the route: prefer indexes, narrow queries, explicit periods, exact ids, and pagination instead of unbounded random scans. Use `get_compact_export` only for bounded periods and only when a bundled read is genuinely faster than separate targeted reads.

When the user asks an underspecified question, the agent should ask a clarifying question instead of inventing the missing scope. Answers must use concrete task names, not bare ids: write `task 318099: Сформировать реестр платежей`, with owner/status/deadline/source when available.

## Local Protocol Test

```powershell
@'
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"local-test","version":"0"}}}
{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"health","arguments":{}}}
'@ | .\.venv\Scripts\python.exe .\mcp\context_server.py
```

## Claude Code

This repo includes `.mcp.json`, so Claude Code should discover the server when opened from the project root. In Claude Code, run:

```text
/mcp
```

If you prefer installing it into your local Claude config explicitly:

```powershell
claude mcp add --transport stdio employee-context -- .\.venv\Scripts\python.exe .\mcp\context_server.py
```

Then ask Claude:

```text
Use employee-context health.
Use employee-context start_here_always_read_ai_instructions.
Use employee-context get_period_index for 2026-04-30 to 2026-05-06.
Find raw messages from 2026-04-30 to 2026-05-06.
Use employee-context list_zoom_calls for 2026-05-01 to 2026-05-09.
Use employee-context get_zoom_call_transcript for the Zoom call_id from list_zoom_calls.
Use employee-context search_zoom_transcripts query "платежный календарь" from 2026-05-01 to 2026-05-09.
```

## Notes

- The server reads `DATABASE_URL` from the environment first, then from the project `.env`.
- For the Flask HTTP endpoint, set `MCP_SHARED_SECRET` and connect Claude Web to `/mcp/<secret>`.
- The same secret is also accepted as `Authorization: Bearer <secret>`.
- It does not expose arbitrary SQL.
- Zoom calls are read from `zoom_calls`, `zoom_call_participants`, and `zoom_call_transcript_segments`.
