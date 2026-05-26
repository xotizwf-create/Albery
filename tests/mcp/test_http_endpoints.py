"""The HTTP MCP surfaces (/mcp full, /mcp-faq subset) expose the same tools as
the stdio dispatch core, and enforce their shared secret.

conftest sets MCP_SHARED_SECRET=test-mcp-secret and MCP_FAQ_SHARED_SECRET=
test-faq-secret.
"""
from __future__ import annotations

import json

FULL_AUTH = {"Authorization": "Bearer test-mcp-secret"}
FAQ_AUTH = {"Authorization": "Bearer test-faq-secret"}


def _tools_list(client, path, headers):
    resp = client.post(
        path,
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
        content_type="application/json",
        headers=headers,
    )
    return resp


def test_full_mcp_lists_all_tools(client, ctx):
    resp = _tools_list(client, "/mcp", FULL_AUTH)
    assert resp.status_code == 200, resp.data
    names = {t["name"] for t in resp.get_json()["result"]["tools"]}
    assert names == set(ctx.TOOLS)


def test_faq_mcp_lists_only_faq_tools(client, ctx):
    resp = _tools_list(client, "/mcp-faq", FAQ_AUTH)
    assert resp.status_code == 200, resp.data
    names = {t["name"] for t in resp.get_json()["result"]["tools"]}
    assert names == set(ctx.FAQ_TOOL_NAMES)
    assert names <= set(ctx.TOOLS)


def test_full_mcp_requires_secret(client):
    resp = _tools_list(client, "/mcp", {})
    assert resp.status_code in (401, 403)


def test_faq_mcp_requires_secret(client):
    resp = _tools_list(client, "/mcp-faq", {})
    assert resp.status_code in (401, 403)


def test_wrong_secret_rejected(client):
    resp = _tools_list(client, "/mcp", {"Authorization": "Bearer wrong"})
    assert resp.status_code in (401, 403)
