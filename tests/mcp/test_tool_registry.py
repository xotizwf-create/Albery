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



def test_every_tool_has_risk_metadata(ctx):
    required_fields = {
        "risk_class",
        "permission_scope",
        "side_effects",
        "requires_confirm",
        "writes_db",
        "external_action",
        "route_hint",
    }
    allowed_classes = ctx.TOOL_RISK_CLASSES
    for name, spec in ctx.TOOLS.items():
        metadata = spec.get("risk_metadata")
        assert isinstance(metadata, dict), f"{name}: missing risk_metadata"
        missing = required_fields - set(metadata)
        assert not missing, f"{name}: missing risk metadata fields: {sorted(missing)}"
        assert metadata["risk_class"] in allowed_classes, f"{name}: invalid risk_class"
        assert isinstance(metadata["side_effects"], list), f"{name}: side_effects must be a list"
        assert isinstance(metadata["requires_confirm"], bool), f"{name}: requires_confirm must be bool"
        assert isinstance(metadata["writes_db"], bool), f"{name}: writes_db must be bool"
        assert isinstance(metadata["external_action"], bool), f"{name}: external_action must be bool"
        assert isinstance(metadata["route_hint"], str) and metadata["route_hint"].strip(), f"{name}: route_hint missing"


def test_external_actions_require_confirm_in_schema(ctx):
    for name, spec in ctx.TOOLS.items():
        metadata = spec["risk_metadata"]
        schema = spec["inputSchema"]
        required = set(schema.get("required") or [])
        properties = schema.get("properties") or {}
        if metadata["external_action"]:
            assert metadata["risk_class"] == "external_action", f"{name}: external_action must use external_action risk class"
            assert metadata["requires_confirm"] is True, f"{name}: external actions must require confirm"
            assert "confirm" in required, f"{name}: confirm must be required in schema"
            assert properties.get("confirm", {}).get("type") == "boolean", f"{name}: confirm must be a boolean property"
        else:
            assert metadata["risk_class"] != "external_action", f"{name}: external_action risk class must set external_action=true"


def test_tool_list_exposes_short_route_contract(ctx):
    resp = ctx.handle_request({"jsonrpc": "2.0", "id": 20, "method": "tools/list"})
    for tool in resp["result"]["tools"]:
        description = tool["description"]
        assert "action_class=" in description, f"{tool['name']}: action_class not exposed"
        assert "scope=" in description, f"{tool['name']}: permission scope not exposed"
        assert "route=" in description, f"{tool['name']}: route hint not exposed"
