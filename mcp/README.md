# Employee Context MCP

Read-only MCP server for Claude Code / Claude Desktop. It exposes the local PostgreSQL analytics database through domain tools instead of unrestricted SQL.

## Tools

- `start_here_always_read_ai_instructions` - mandatory first tool for any company analysis/report/recommendation/answer; reads live rules from "Настройки -> Инструкции для ИИ" and returns the execution contract.
- `health` - PostgreSQL connectivity check.
- `get_context_guide` - navigation rules for using the database systematically after `start_here_always_read_ai_instructions`.
- `get_report_contract` - read the active report-generation contract; for daily chat reports use `category_key=chat_analysis`.
- `list_available_sources` - table availability and row counts.
- `list_periods` - recent dates with chat messages/reports.
- `get_company_profile` - editable company profile text from PostgreSQL.
- `list_company_files` - list every file/folder available in the company knowledge section.
- `get_company_file` - read the full text and source metadata for one company knowledge file.
- `search_company_knowledge` - search "О компании", including Google Drive mirrored documents.
- `get_period_index` - compact period manifest with counts and top chats.
- `get_org_structure` - departments, users, managers, memberships.
- `search_tasks` - Bitrix task search; rows include `comments_total_count` and `comments_human_count`.
- `get_task_comments` - read the comment thread of one task by `bitrix_task_id`, with author names and BB-code-cleaned text (system notifications excluded unless `include_service=true`).
- `list_chats` - active chat listing.
- `search_messages` - raw chat message search.
- `get_chat_transcript` - raw chat transcript by `dialog_id` and period, including OCR for image/PDF attachments.
- `get_chat_ocr_status` - check whether image/PDF attachments have OCR before a report.
- `process_chat_ocr` - run OCR processing for image/PDF attachments through the local app workflow.
- `list_zoom_calls` - Zoom recordings/calls with dates, technical topics, participants, and transcript counts.
- `get_zoom_call_transcript` - one Zoom call with participants and raw transcript segments.
- `search_zoom_transcripts` - search inside Zoom transcript segments.
- `get_chat_daily_report` - read the saved daily report for one chat/date.
- `save_chat_daily_report` - write a generated daily report for one chat/date and its structured items.
- `get_chat_weekly_report` - read the saved weekly report for one chat and period.
- `save_chat_weekly_report` - write a generated weekly report for one chat/period.
- `get_previous_owner_daily_context` - read only the previous calendar day's current owner daily report for continuity when creating a new owner daily report.
- `get_owner_reports` - read recent current owner daily/weekly reports for recommendation continuity.
- `save_owner_daily_report` - write a generated daily owner report to `owner_daily_reports`.
- `save_owner_weekly_report` - write a generated weekly owner report to `owner_weekly_reports`.
- `upsert_ai_instruction` - create or update an instruction folder under "Настройки -> Инструкции для ИИ".
- `get_compact_export` - compact bundle generated from live PostgreSQL data.

## Recommended Usage Pattern

For any company analysis, report, recommendation, or answer, start with `start_here_always_read_ai_instructions`. It reads the live editable rules from "Настройки -> Инструкции для ИИ" and returns the mandatory execution contract.

For new analysis requests, call `get_context_guide` after `start_here_always_read_ai_instructions`. It tells the model which source to inspect first:

- company rules, regulations, and documents: `search_company_knowledge`, `list_company_files`, `get_company_file`, then `get_company_profile` if the full tree is needed;
- employees, managers, and departments: `get_org_structure`;
- date-based analysis: `get_period_index`, then `search_tasks`, `search_messages`, and Zoom tools;
- chat evidence: `list_chats`, then `get_chat_transcript`;
- meeting evidence: `list_zoom_calls`, then `get_zoom_call_transcript` or `search_zoom_transcripts`.
- owner daily report creation: read the previous day through `get_previous_owner_daily_context(report_date=YYYY-MM-DD)`, then use relevant chat reports, Zoom reports, company knowledge, and concrete Bitrix tasks before saving.
- recommendations and management answers: read prior owner context, relevant chat reports, company knowledge, and concrete Bitrix tasks before answering.
- daily chat report creation: `get_ai_instructions`, active `get_report_contract(category_key=chat_analysis)`, `get_chat_ocr_status`, `process_chat_ocr` if OCR is missing, `get_chat_transcript` with `include_ocr=true`, `list_zoom_calls`, `search_zoom_transcripts` with keywords from chat/OCR/tasks/risks, `get_zoom_call_transcript` for matches and for the only same-day call, previous `get_chat_daily_report`, then `save_chat_daily_report`.
- weekly chat report creation: `get_report_contract(category_key=chat_weekly_report)`, verify/generate required daily reports, check existing report with `get_chat_weekly_report`, then save to `chat_weekly_reports` with `save_chat_weekly_report`.

Zoom relevance for daily chat reports must be determined from transcript content, participants, and keywords from the chat/OCR context. Do not declare Zoom irrelevant after searching only the chat title, company name, or call topic.

This keeps the model from scanning sources randomly and gives it a stable map of the system.

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
