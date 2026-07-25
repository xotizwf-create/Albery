"""Security contract for the customer-facing ИУ consultant connector."""
from __future__ import annotations

import agent_center
import agent_knowledge


IU_AGENT_SLUG = "albery-ai-bot"
IU_SAFE_TOOLS = set()


def test_iu_manifest_is_small_and_sales_specific(ctx):
    manifest = agent_knowledge.load_manifest(IU_AGENT_SLUG)

    assert set(manifest["tools"]) == IU_SAFE_TOOLS
    assert manifest["tools"] == []
    assert manifest["skills"] == []
    assert set(manifest["instructions"]) == {
        "Формат ответа / Оформление сообщений клиенту в Telegram",
        "Работа с клиентами / Общение в переписке",
    }


def test_iu_manifest_has_no_admin_or_generic_write_tools(ctx):
    enabled = set(agent_knowledge.load_manifest(IU_AGENT_SLUG)["tools"])
    resource_unscoped = {
        "search_company_knowledge",
        "get_crm_deal",
        "send_terms",
    }
    forbidden = resource_unscoped | {
        "send_telegram_message",
        "send_contract",
        "create_bitrix_task",
        "notify_client_when_task_done",
        "create_crm_deal",
        "update_crm_deal",
        "delete_crm_deal",
        "delete_crm_pipeline",
        "delete_bitrix_task",
        "upsert_ai_instruction",
        "set_agent_tools",
    }

    assert enabled == set()
    assert enabled.isdisjoint(ctx.OWNER_ONLY_TOOL_NAMES)
    assert enabled.isdisjoint(forbidden)
    assert not {
        name
        for name in enabled
        if name.startswith(("create_", "update_", "delete_", "upsert_", "write_", "share_"))
    }


def test_manifest_cap_is_upper_bound_over_broad_db_whitelist(ctx):
    agent = {
        "slug": IU_AGENT_SLUG,
        "tier": "developer",
        "tools_customized": True,
        "tools": sorted(
            IU_SAFE_TOOLS
            | {
                "delete_crm_deal",
                "send_telegram_message",
                "update_crm_deal",
                "upsert_ai_instruction",
            }
        ),
    }

    exposed = agent_center._agent_tool_names(agent)

    assert exposed == IU_SAFE_TOOLS
    assert agent_center._agent_self_tool_names(agent) == set()
    for tool, arguments in {
        "search_company_knowledge": {"query": "внутренние пароли"},
        "get_crm_deal": {"deal_id": 1},
        "send_terms": {"deal_id": 1, "telegram_id": "999"},
        "delete_crm_deal": {"deal_id": 1, "confirm": True},
    }.items():
        denied = ctx.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            },
            tool_names=exposed,
            allow_owner_tools=True,
        )
        assert denied["error"]["code"] == -32601


def test_db_whitelist_can_narrow_but_not_expand_manifest():
    agent = {
        "slug": IU_AGENT_SLUG,
        "tier": "ops",
        "tools_customized": True,
        "tools": ["get_crm_deal", "delete_crm_deal"],
    }

    assert agent_center._agent_tool_names(agent) == set()
