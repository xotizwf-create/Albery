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
        "get_answer_context",
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


def test_handle_request_initialize(ctx):
    resp = ctx.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert resp["id"] == 1
    assert "serverInfo" in resp["result"]
    assert resp["result"]["capabilities"]["tools"] == {}


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
