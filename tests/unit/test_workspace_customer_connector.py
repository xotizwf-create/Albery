from __future__ import annotations

import pytest

import tg_agent
from scripts import ensure_workspace_customer_connector as connector


def test_replace_connector_block_adds_under_mcp_servers():
    source = "model: x\nmcp_servers:\n  other:\n    url: https://example.test\n"

    result = connector.replace_connector_block(
        source,
        "iu-customer-runtime",
        connector.connector_block(
            "iu-customer-runtime",
            "secret-token",
            "https://mcp.example.test/",
        ),
    )

    assert "mcp_servers:\n  agent-iu-customer-runtime:" in result
    assert "https://mcp.example.test/mcp-agent/iu-customer-runtime/secret-token" in result
    assert result.count("agent-iu-customer-runtime:") == 1


def test_replace_connector_block_is_idempotent_and_updates_token():
    old = (
        "mcp_servers:\n"
        "  agent-iu-customer-runtime:\n"
        "    url: https://mcp.test/mcp-agent/iu-customer-runtime/old\n"
        "    enabled: true\n"
        "    timeout: 300\n"
        "  other:\n"
        "    url: https://example.test\n"
    )
    block = connector.connector_block(
        "iu-customer-runtime",
        "new",
        "https://mcp.test",
    )

    first = connector.replace_connector_block(old, "iu-customer-runtime", block)
    second = connector.replace_connector_block(first, "iu-customer-runtime", block)

    assert "/old" not in first
    assert "/new" in first
    assert first == second
    assert "  other:" in first


@pytest.mark.parametrize(
    "public_base",
    (
        "",
        "http://mcp.example.test",
        "https://user:secret@mcp.example.test",
        "https://mcp.example.test?target=other",
        "https://mcp.example.test/#fragment",
        "https://mcp.example.test/\n  injected: true",
    ),
)
def test_connector_block_rejects_unsafe_or_implicit_public_base(public_base):
    with pytest.raises(RuntimeError, match="explicit HTTPS base"):
        connector.connector_block(
            "iu-customer-runtime",
            "secret-token",
            public_base,
        )


def test_committed_manifest_is_zero_tool():
    connector.assert_zero_tool_manifest("iu-customer-runtime")


def test_customer_runtime_uses_only_dedicated_zero_tool_connector(monkeypatch):
    monkeypatch.delenv("FUNNEL_WORKSPACE_CUSTOMER_TOOLSET_SLUG", raising=False)

    assert tg_agent.customer_toolsets() == "agent-iu-customer-runtime"


def test_customer_role_defaults_to_existing_iu_agent(monkeypatch):
    monkeypatch.delenv("FUNNEL_WORKSPACE_AGENT_SLUG", raising=False)

    assert tg_agent.customer_agent_slug() == "agent-po-rabote-s-iu"


def test_customer_runtime_rejects_broad_role_agent_connector(monkeypatch):
    monkeypatch.setenv(
        "FUNNEL_WORKSPACE_CUSTOMER_TOOLSET_SLUG",
        "agent-po-rabote-s-iu",
    )

    with pytest.raises(RuntimeError, match="not capped to zero tools"):
        tg_agent.customer_toolsets()


def test_existing_unrelated_agent_row_is_never_repurposed():
    with pytest.raises(RuntimeError, match="unexpected existing agent row"):
        connector.assert_reusable_agent_row(
            {
                "name": "Чужой агент",
                "role_prompt": "",
                "tier": "faq",
                "tools": {},
                "tools_customized": True,
                "bitrix_bot_id": None,
                "telegram_bot_token": None,
            }
        )


def test_existing_dedicated_zero_tool_row_is_reusable():
    connector.assert_reusable_agent_row(
        {
            "name": connector.DEFAULT_NAME,
            "role_prompt": "",
            "tier": "faq",
            "tools": {},
            "tools_customized": True,
            "bitrix_bot_id": None,
            "telegram_bot_token": None,
        }
    )
