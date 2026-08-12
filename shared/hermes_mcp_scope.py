"""Per-process MCP discovery scope for Albery's Hermes subprocesses.

Hermes keeps every configured server in one user config.  Its discovery is global by default, so a
one-shot requesting one private connector otherwise opens every live and automation alias.  Albery
always exports an explicit allowlist; built-in-only turns export the empty string and therefore
discover no MCP server.
"""
from __future__ import annotations

import os
from collections.abc import Mapping

ENV_NAME = "HERMES_MCP_SERVER_ALLOWLIST"


def connector_allowlist(toolsets: str | None) -> str:
    names: list[str] = []
    for raw in str(toolsets or "").split(","):
        name = raw.strip()
        if name.startswith(("agent-", "automation-agent-")) and name not in names:
            names.append(name)
    return ",".join(names)


def scoped_env(toolsets: str | None, base: Mapping[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    env[ENV_NAME] = connector_allowlist(toolsets)
    return env


def scoped_env_for_command(command: list[object], base: Mapping[str, str] | None = None) -> dict[str, str]:
    args = [str(value) for value in command]
    toolsets = ""
    for flag in ("-t", "--toolsets"):
        try:
            toolsets = args[args.index(flag) + 1]
            break
        except (ValueError, IndexError):
            continue
    return scoped_env(toolsets, base)
