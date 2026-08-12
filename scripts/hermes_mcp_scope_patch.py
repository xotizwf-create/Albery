#!/usr/bin/env python3
"""Idempotently make installed Hermes honor Albery's process MCP allowlist.

The patch is re-applied by the gateway systemd unit after Hermes upgrades.  It never edits user
configuration or credentials.  A scheduler-only gateway skips MCP discovery completely; ordinary
Albery one-shots connect only names in HERMES_MCP_SERVER_ALLOWLIST.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

MCP_TARGET = Path(os.getenv("HERMES_MCP_TOOL", "/usr/local/lib/hermes-agent/tools/mcp_tool.py"))
GATEWAY_TARGET = Path(os.getenv("HERMES_GATEWAY_RUN", "/usr/local/lib/hermes-agent/gateway/run.py"))
MCP_MARKER = "# PATCH albery-mcp-server-allowlist"
GATEWAY_MARKER = "# PATCH albery-scheduler-only-gateway"


def patch_text(source: str, anchor: str, replacement: str, marker: str) -> str:
    if marker in source:
        return source
    if source.count(anchor) != 1:
        raise RuntimeError(f"Hermes patch anchor count is {source.count(anchor)}, expected 1")
    return source.replace(anchor, replacement, 1)


def apply(path: Path, updated: str) -> None:
    original = path.read_text(encoding="utf-8")
    if original == updated:
        return
    temporary = path.with_name(f".{path.name}.albery-scope-{os.getpid()}.tmp")
    temporary.write_text(updated, encoding="utf-8")
    temporary.chmod(path.stat().st_mode & 0o777)
    os.replace(temporary, path)


def patched_sources(mcp_source: str, gateway_source: str) -> tuple[str, str]:
    mcp_anchor = "    servers = _load_mcp_config()\n    if not servers:\n"
    mcp_replacement = (
        "    servers = _load_mcp_config()\n"
        f"    {MCP_MARKER}\n"
        "    _scope_raw = os.environ.get('HERMES_MCP_SERVER_ALLOWLIST')\n"
        "    if _scope_raw is not None:\n"
        "        _scope = {name.strip() for name in _scope_raw.split(',') if name.strip()}\n"
        "        servers = {name: cfg for name, cfg in servers.items() if name in _scope}\n"
        "    if not servers:\n"
    )
    gateway_anchor = (
        "    # MCP tool discovery — run in an executor so the asyncio event loop\n"
        "    # stays responsive even when a configured MCP server is slow or\n"
    )
    gateway_replacement = (
        f"    {GATEWAY_MARKER}\n"
        "    if os.environ.get('HERMES_GATEWAY_SCHEDULER_ONLY', '').strip() == '1':\n"
        "        logger.info('Albery scheduler-only gateway: MCP discovery skipped')\n"
        "    else:\n"
        "        await _albery_discover_gateway_mcp()\n\n"
        "    # MCP tool discovery — run in an executor so the asyncio event loop\n"
        "    # stays responsive even when a configured MCP server is slow or\n"
    )
    gateway_source = patch_text(gateway_source, gateway_anchor, gateway_replacement, GATEWAY_MARKER)
    # Move the existing discovery body into a helper without changing its behavior for non-Albery
    # gateways.  The exact block is deliberately pinned so an upstream change fails closed.
    old_block = (
        "    try:\n"
        "        from tools.mcp_tool import discover_mcp_tools\n"
        "        _loop = asyncio.get_running_loop()\n"
        "        await _loop.run_in_executor(None, discover_mcp_tools)\n"
        "    except Exception as e:\n"
        "        logger.debug(\"MCP tool discovery failed: %s\", e)\n\n"
        "    # Start the gateway\n"
    )
    helper_block = (
        "    # Discovery is performed above through _albery_discover_gateway_mcp.\n\n"
        "    # Start the gateway\n"
    )
    if "# Discovery is performed above through _albery_discover_gateway_mcp." not in gateway_source:
        if gateway_source.count(old_block) != 1:
            raise RuntimeError("Hermes gateway discovery block changed; refusing partial patch")
        gateway_source = gateway_source.replace(old_block, helper_block, 1)
        insertion = "async def start_gateway("
        helper = (
            "async def _albery_discover_gateway_mcp() -> None:\n"
            "    try:\n"
            "        from tools.mcp_tool import discover_mcp_tools\n"
            "        _loop = asyncio.get_running_loop()\n"
            "        await _loop.run_in_executor(None, discover_mcp_tools)\n"
            "    except Exception as e:\n"
            "        logger.debug(\"MCP tool discovery failed: %s\", e)\n"
            "\n\nasync def start_gateway("
        )
        if gateway_source.count(insertion) != 1:
            raise RuntimeError("Hermes start_gateway anchor changed")
        gateway_source = gateway_source.replace(insertion, helper, 1)
    mcp_source = patch_text(mcp_source, mcp_anchor, mcp_replacement, MCP_MARKER)
    return mcp_source, gateway_source


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.check == args.apply:
        parser.error("choose exactly one of --check or --apply")
    mcp_original = MCP_TARGET.read_text(encoding="utf-8")
    gateway_original = GATEWAY_TARGET.read_text(encoding="utf-8")
    mcp_updated, gateway_updated = patched_sources(mcp_original, gateway_original)
    if args.apply:
        apply(MCP_TARGET, mcp_updated)
        apply(GATEWAY_TARGET, gateway_updated)
    print("Hermes MCP scope contract: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
