"""MCP tool-registry contract + consistency between the full and FAQ servers.

The repo has one implementation (mcp/context_server.py) exposed as the full
server (stdio + HTTP /mcp, all tools) and the FAQ server (HTTP /mcp-faq, a
subset via FAQ_TOOL_NAMES). These tests pin: every tool is well-formed, the FAQ
set is a genuine subset, and the shared `handle_request` dispatch lists the
expected tools for each server.
"""
from __future__ import annotations


def test_every_tool_is_well_formed(ctx):
    for name, spec in ctx.TOOLS.items():
        assert isinstance(name, str) and name, "tool name must be a non-empty string"
        assert isinstance(spec.get("description"), str) and spec["description"].strip(), f"{name}: missing description"
        schema = spec.get("inputSchema")
        assert isinstance(schema, dict), f"{name}: inputSchema must be a dict"
        assert schema.get("type") == "object", f"{name}: inputSchema.type must be 'object'"
        assert callable(spec.get("handler")), f"{name}: handler must be callable"


def test_faq_is_a_genuine_subset_of_full(ctx):
    full = set(ctx.TOOLS)
    faq = set(ctx.FAQ_TOOL_NAMES)
    assert faq, "FAQ tool set must not be empty"
    missing = faq - full
    assert not missing, f"FAQ references tools not in the full registry: {sorted(missing)}"
    assert faq < full, "FAQ must be a strict subset of the full server"


def test_core_tools_present(ctx):
    # Tools the workflows and instructions depend on must exist.
    required = {
        "start_here_always_read_ai_instructions",
        "get_context_guide",
        "get_ai_instructions",
        "get_report_contract",
        "get_report_readiness",
        "search_tasks",
        "get_org_structure",
        "get_company_profile",
        "health",
    }
    missing = required - set(ctx.TOOLS)
    assert not missing, f"missing core tools: {sorted(missing)}"


def test_task_mutation_tools_registered(ctx):
    required = {
        "add_bitrix_task_comment",
        "reopen_bitrix_task",
        "delete_bitrix_task",
    }
    missing = required - set(ctx.TOOLS)
    assert not missing, f"missing task mutation tools: {sorted(missing)}"
    assert {"add_bitrix_task_comment", "reopen_bitrix_task"} <= set(ctx.CORE_TOOL_NAMES)


def test_task_management_and_attachment_tools_registered(ctx):
    # Full task lifecycle + attachment handling for the team rollout (2026-07-07).
    required = {
        "complete_bitrix_task",
        "attach_files_to_task",
        "get_attachment_text",
    }
    missing = required - set(ctx.TOOLS)
    assert not missing, f"missing task/attachment tools: {sorted(missing)}"
    # All reachable via the chat bot's core toolset.
    assert required <= set(ctx.CORE_TOOL_NAMES)
    # Reading an attachment is read-only -> safe on the FAQ tier (token-gated, unguessable).
    assert "get_attachment_text" in ctx.FAQ_TOOL_NAMES
    # Mutating tools must NOT be on the read-only FAQ tier.
    assert "complete_bitrix_task" not in ctx.FAQ_TOOL_NAMES
    assert "attach_files_to_task" not in ctx.FAQ_TOOL_NAMES


def test_crm_funnel_tools_registered(ctx):
    # Full funnel management for the team rollout (2026-07-08).
    required = {
        "list_crm_pipelines",
        "create_crm_pipeline",
        "update_crm_pipeline",
        "delete_crm_pipeline",
        "manage_crm_pipeline_stage",
        "list_crm_deal_fields",
        "manage_crm_deal_field",
        "list_crm_deals",
        "get_crm_deal",
        "create_crm_deal",
        "update_crm_deal",
        "delete_crm_deal",
    }
    missing = required - set(ctx.TOOLS)
    assert not missing, f"missing CRM funnel tools: {sorted(missing)}"
    # Everyday funnel work is reachable from the chat bot's core toolset.
    assert {"list_crm_pipelines", "list_crm_deals", "get_crm_deal",
            "create_crm_deal", "update_crm_deal"} <= set(ctx.CORE_TOOL_NAMES)
    # Destroying a funnel or a deal is admin-class.
    assert {"delete_crm_pipeline", "delete_crm_deal"} <= set(ctx.OWNER_ONLY_TOOL_NAMES)
    # CRM is private business data — never on the FAQ tier.
    assert not (required & set(ctx.FAQ_TOOL_NAMES))


def test_crm_delete_tools_require_confirm(ctx):
    for tool, arguments in (
        ("delete_crm_pipeline", {"category_id": 1}),
        ("delete_crm_deal", {"deal_id": 1}),
        ("manage_crm_pipeline_stage", {"action": "delete", "category_id": 1, "stage": "X"}),
        ("manage_crm_deal_field", {"action": "delete", "field_code": "UF_CRM_X"}),
    ):
        resp = ctx.handle_request(
            {"jsonrpc": "2.0", "id": 30, "method": "tools/call",
             "params": {"name": tool, "arguments": arguments}},
            tool_names={tool},
            allow_owner_tools=True,
        )
        assert resp["error"]["code"] == -32602, f"{tool} must refuse without confirm=true"
        assert "подтвержд" in resp["error"]["message"], f"{tool} refusal must explain the confirm gate"


def test_add_comment_supports_author_and_result(ctx):
    schema = ctx.TOOLS["add_bitrix_task_comment"]["inputSchema"]["properties"]
    for field in ("author_bitrix_user_id", "author_name", "attachment_ids", "as_result"):
        assert field in schema, f"add_bitrix_task_comment missing {field}"
    # comment_text is no longer required (a comment may carry only attachments).
    assert "comment_text" not in ctx.TOOLS["add_bitrix_task_comment"]["inputSchema"]["required"]


def test_reopen_supports_new_deadline(ctx):
    schema = ctx.TOOLS["reopen_bitrix_task"]["inputSchema"]["properties"]
    assert "new_deadline" in schema
    assert "on_behalf_bitrix_user_id" in schema


def test_ops_core_does_not_list_owner_only_delete(ctx):
    resp = ctx.handle_request(
        {"jsonrpc": "2.0", "id": 20, "method": "tools/list"},
        tool_names=ctx.OPS_TOOL_NAMES,
        core=True,
    )
    listed = {t["name"] for t in resp["result"]["tools"]}
    assert "add_bitrix_task_comment" in listed
    assert "reopen_bitrix_task" in listed
    assert "delete_bitrix_task" not in listed


def test_handle_request_initialize(ctx):
    resp = ctx.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert resp["id"] == 1
    assert "serverInfo" in resp["result"]
    assert resp["result"]["capabilities"]["tools"] == {}


def test_handle_request_ping_returns_empty_result(ctx):
    # hermes >=0.17 pings each connector every keepalive interval and treats any
    # failure as a dead connection (finite reconnect budget) — ping MUST succeed.
    resp = ctx.handle_request({"jsonrpc": "2.0", "id": 7, "method": "ping"})
    assert resp["id"] == 7
    assert resp["result"] == {}
    assert "error" not in resp


def test_notifications_are_one_way(ctx):
    # Any notifications/* must never be answered — not even with an error object.
    assert ctx.handle_request({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert ctx.handle_request({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {}}) is None


def test_full_server_lists_all_tools(ctx):
    resp = ctx.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    listed = {t["name"] for t in resp["result"]["tools"]}
    assert listed == set(ctx.TOOLS)
    for tool in resp["result"]["tools"]:
        assert tool["description"].strip()
        assert isinstance(tool["inputSchema"], dict)


def test_faq_server_lists_only_faq_tools(ctx):
    resp = ctx.handle_request(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, tool_names=ctx.FAQ_TOOL_NAMES
    )
    listed = {t["name"] for t in resp["result"]["tools"]}
    assert listed == set(ctx.FAQ_TOOL_NAMES)
    assert listed <= set(ctx.TOOLS)


def test_unknown_tool_call_is_rejected(ctx):
    resp = ctx.handle_request(
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "definitely_not_a_tool", "arguments": {}}}
    )
    assert resp["error"]["code"] == -32601


def test_faq_cannot_call_a_full_only_tool(ctx):
    full_only = sorted(set(ctx.TOOLS) - set(ctx.FAQ_TOOL_NAMES))
    assert full_only, "expected at least one full-only tool"
    resp = ctx.handle_request(
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
         "params": {"name": full_only[0], "arguments": {}}},
        tool_names=ctx.FAQ_TOOL_NAMES,
    )
    assert resp["error"]["code"] == -32601


def test_agent_scoped_connector_can_call_enabled_owner_tool_when_allowed(ctx):
    request = {
        "jsonrpc": "2.0",
        "id": 6,
        "method": "tools/call",
        "params": {"name": "delete_bitrix_task", "arguments": {"bitrix_task_id": 1}},
    }

    public_scoped = ctx.handle_request(request, tool_names={"delete_bitrix_task"})
    assert public_scoped["error"]["code"] == -32601

    per_agent = ctx.handle_request(
        request,
        tool_names={"delete_bitrix_task"},
        allow_owner_tools=True,
    )
    assert per_agent["error"]["code"] == -32602
    assert "подтверждения" in per_agent["error"]["message"]
