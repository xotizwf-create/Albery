"""Private per-agent HTTP boundary and retired shared connector contract."""
from __future__ import annotations

import json

import pytest


def _tools_list(client, path, headers=None, environ=None):
    return client.post(
        path,
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
        content_type="application/json",
        headers=headers or {},
        environ_overrides=environ or {},
    )


@pytest.fixture
def private_agent(monkeypatch):
    import agent_center

    agent = {
        "slug": "private-test",
        "name": "Private test",
        "mcp_token": "agent-header-secret",
        "is_active": True,
    }
    monkeypatch.setattr(agent_center, "_agent_by_slug", lambda slug: agent if slug == agent["slug"] else None)
    monkeypatch.setattr(agent_center, "_agent_tool_names", lambda _agent: {"health"})
    monkeypatch.setattr(agent_center, "_agent_self_tool_names", lambda _agent: set())
    monkeypatch.setattr("agent_knowledge.allowed_instruction_paths", lambda _slug: None)
    return agent


def test_private_agent_uses_header_and_exact_tools(client, private_agent):
    response = _tools_list(
        client,
        "/mcp-agent/private-test",
        {"Authorization": "Bearer agent-header-secret"},
    )
    assert response.status_code == 200, response.data
    assert {tool["name"] for tool in response.get_json()["result"]["tools"]} == {"health"}


def test_private_agent_rejects_missing_or_wrong_header(client, private_agent):
    assert _tools_list(client, "/mcp-agent/private-test").status_code == 403
    assert _tools_list(
        client,
        "/mcp-agent/private-test",
        {"Authorization": "Bearer wrong"},
    ).status_code == 403


def test_path_token_is_not_a_route(client, private_agent):
    response = _tools_list(client, "/mcp-agent/private-test/agent-header-secret")
    assert response.status_code == 404


def test_path_token_compatibility_requires_explicit_rollout_flag(client, private_agent, monkeypatch):
    monkeypatch.setenv("MCP_ALLOW_PATH_TOKEN", "1")
    response = _tools_list(client, "/mcp-agent/private-test/agent-header-secret")
    assert response.status_code == 200


def test_forwarded_public_request_is_hidden_even_with_valid_header(client, private_agent):
    response = _tools_list(
        client,
        "/mcp-agent/private-test",
        {"Authorization": "Bearer agent-header-secret", "X-Real-IP": "203.0.113.10"},
    )
    assert response.status_code == 404


@pytest.mark.parametrize(
    "path",
    ("/mcp", "/mcp-faq", "/mcp-ops", "/mcp-core", "/mcp-ops-core", "/sse", "/sse-faq", "/sse-ops"),
)
def test_shared_and_sse_routes_are_retired(client, path):
    assert _tools_list(client, path).status_code == 404
