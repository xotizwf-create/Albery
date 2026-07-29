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


def test_public_base_is_read_from_the_env_file_not_only_the_process(monkeypatch, tmp_path):
    # Скрипт запускают руками при деплое, без окружения приложения: значение живёт в .env.
    monkeypatch.delenv("AGENT_MCP_PUBLIC_BASE", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DATABASE_URL=postgresql://ignored/db\nAGENT_MCP_PUBLIC_BASE=https://mcp.example.test\n",
        encoding="utf-8",
    )

    assert connector.public_base(env_file) == "https://mcp.example.test"


def test_public_base_prefers_an_explicit_process_variable(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_MCP_PUBLIC_BASE", "https://explicit.example.test")
    env_file = tmp_path / ".env"
    env_file.write_text("AGENT_MCP_PUBLIC_BASE=https://from-file.example.test\n", encoding="utf-8")

    assert connector.public_base(env_file) == "https://explicit.example.test"


def test_committed_manifest_is_zero_tool():
    connector.assert_zero_tool_manifest("iu-customer-runtime")


def test_customer_runtime_uses_only_dedicated_zero_tool_connector(monkeypatch):
    monkeypatch.delenv("FUNNEL_WORKSPACE_CUSTOMER_TOOLSET_SLUG", raising=False)

    assert tg_agent.customer_toolsets() == "agent-iu-customer-runtime"


def test_customer_role_comes_from_the_client_agent_card(monkeypatch):
    """Роль клиента — из карточки клиентского агента, а не внутреннего (владелец, 29.07.2026).

    У «Агента по работе с ИУ» роль написана под работу в группе Битрикса: «1–3 предложения»,
    «точку в конце реплики не ставь», «переспроси сотрудника в группе». В промпте она стоит
    выше правил хода и молча их отменяла — отсюда сухие обрывочные ответы клиенту."""
    monkeypatch.delenv("FUNNEL_WORKSPACE_AGENT_SLUG", raising=False)

    assert tg_agent.customer_agent_slug() == "iu-customer-runtime"


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
