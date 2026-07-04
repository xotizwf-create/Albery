# Zoom report + Bitrix dispatch after stale MCP connection

Session pattern from 2026-06-30.

## Trigger
- User asks to generate a new report for the latest Zoom call.
- First Albery MCP tool call returns `MCP server 'albery' is not connected`, while `hermes mcp test albery` can still connect.
- User then asks to make it work and continue.

## Durable workflow
1. Do not conclude Albery is unavailable permanently. Treat it as a stale gateway/session MCP connection.
2. Verify configuration/connectivity from CLI if needed (`hermes mcp list`, `hermes mcp test albery`).
3. Restart the gateway/service, then retry `mcp_albery_start_here_always_read_ai_instructions`.
4. After MCP is live, continue the original Albery workflow instead of stopping at troubleshooting:
   - read Zoom instructions and `zoom_processing` contract;
   - `list_zoom_calls` to pick the latest call;
   - `get_zoom_call_transcript(include_full_text=true)`;
   - `get_org_structure`;
   - search company knowledge for matrix/process responsibility checks;
   - `save_zoom_call_report` with the full analysis JSON including `operational_tasks`, `dispatch_summary`, `people`, `leader_evaluations`, `responsibility_check`, and `expected_artifact`.
5. If the owner then says `отправь задачи по этому отчету в Битрикс` / `ставь` / `создавай`, this is explicit approval for the just-saved Zoom report:
   - call `list_pending_zoom_operational_dispatches` for the call date to find the exact pending `call_id`;
   - call `dispatch_zoom_operational_tasks(call_id, confirm=true)`;
   - report created task count, task IDs if returned, and skipped assignees if any.

## Pitfalls
- Do not use `create_bitrix_task` for Zoom operational tasks; use `dispatch_zoom_operational_tasks`, which groups tasks in the format Albery expects.
- Do not ask for another confirmation when the owner has just said to send/place the tasks for this report; the wording itself is the approval.
- Do not stop after fixing MCP connectivity; finish the original business task and verify the saved report or dispatch result.
