from __future__ import annotations

from pathlib import Path


def test_scope_contains_only_requested_private_connectors():
    from shared.hermes_mcp_scope import connector_allowlist, scoped_env_for_command

    assert connector_allowlist("agent-main,web") == "agent-main"
    assert connector_allowlist("automation-agent-main,web,agent-main") == (
        "automation-agent-main,agent-main"
    )
    assert connector_allowlist("web") == ""
    env = scoped_env_for_command(["hermes", "-z", "x", "-t", "agent-sklad,web"], {})
    assert env["HERMES_MCP_SERVER_ALLOWLIST"] == "agent-sklad"


def test_installed_hermes_patch_is_idempotent_and_pinned():
    from scripts.hermes_mcp_scope_patch import GATEWAY_MARKER, MCP_MARKER, patched_sources

    mcp = """def discover_mcp_tools():
    servers = _load_mcp_config()
    if not servers:
        return []
"""
    gateway = """async def start_gateway(foo):
    # MCP tool discovery — run in an executor so the asyncio event loop
    # stays responsive even when a configured MCP server is slow or
    # unreachable.
    try:
        from tools.mcp_tool import discover_mcp_tools
        _loop = asyncio.get_running_loop()
        await _loop.run_in_executor(None, discover_mcp_tools)
    except Exception as e:
        logger.debug("MCP tool discovery failed: %s", e)

    # Start the gateway
    success = True
"""
    patched_mcp, patched_gateway = patched_sources(mcp, gateway)
    assert MCP_MARKER in patched_mcp and "HERMES_MCP_SERVER_ALLOWLIST" in patched_mcp
    assert GATEWAY_MARKER in patched_gateway and "HERMES_GATEWAY_SCHEDULER_ONLY" in patched_gateway
    assert "print('Albery scheduler-only gateway: MCP discovery skipped'" in patched_gateway
    assert patched_sources(patched_mcp, patched_gateway) == (patched_mcp, patched_gateway)
    old_deployment = patched_gateway.replace(
        "        print('Albery scheduler-only gateway: MCP discovery skipped', flush=True)\n", "",
    )
    assert "print('Albery scheduler-only gateway: MCP discovery skipped'" in patched_sources(
        patched_mcp, old_deployment,
    )[1]


def test_materialization_prunes_inactive_managed_connectors():
    from scripts.migrate_private_mcp import private_config

    original = """mcp_servers:
  agent-old:
    url: http://127.0.0.1:5004/mcp-agent/old
    enabled: true
  automation-agent-old:
    url: http://127.0.0.1:5004/mcp-agent/old
    enabled: true
"""
    updated = private_config(
        original, [{"slug": "main", "token": "safe-test-token"}], "http://127.0.0.1:5004",
    )
    assert "agent-old:" not in updated
    assert "automation-agent-old:" not in updated
    assert "agent-main:" in updated and "automation-agent-main:" in updated


def test_selfcheck_understands_scheduler_only_gateway():
    source = Path("scripts/albery_selfcheck.py").read_text(encoding="utf-8")
    assert "Albery scheduler-only gateway: MCP discovery skipped" in source
    assert "unexpectedly opened MCP connectors" in source


def test_agent_prompt_forbids_echoing_historical_export_urls():
    source = Path("b24bot.py").read_text(encoding="utf-8")
    assert "НИКОГДА не копируй и не возвращай URL с /zoom-export/" in source
    assert "[[DELIVER_STORED: " in source
