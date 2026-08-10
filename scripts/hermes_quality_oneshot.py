#!/usr/bin/env python3
"""Hermes/Codex JSON runner with an intentionally empty tool surface.

Run this script with the Hermes virtualenv Python. Input is read from stdin; only the final
model response is written to stdout. The private Hermes helper is pinned by a deploy self-check
so an upstream API change fails closed instead of silently restoring the default CLI toolsets.
"""
from __future__ import annotations

import os
import sys


HERMES_SOURCE = os.getenv("HERMES_SOURCE", "/usr/local/lib/hermes-agent")
NO_TOOLS_SENTINEL = "albery-quality-no-tools"


def _bootstrap() -> None:
    if HERMES_SOURCE not in sys.path:
        sys.path.insert(0, HERMES_SOURCE)
    os.environ.pop("HERMES_KANBAN_TASK", None)
    os.chdir(os.getenv("QUALITY_LLM_CWD", "/tmp"))


def self_check() -> int:
    _bootstrap()
    from model_tools import get_tool_definitions

    tools = get_tool_definitions(enabled_toolsets=[NO_TOOLS_SENTINEL], quiet_mode=True)
    if tools:
        print(f"quality runner unsafe: {len(tools)} tools resolved", file=sys.stderr)
        return 2
    print("quality runner ok: tool_count=0")
    return 0


def main() -> int:
    if "--self-check" in sys.argv:
        return self_check()
    prompt = sys.stdin.read()
    if not prompt.strip():
        print("quality runner: empty prompt", file=sys.stderr)
        return 2
    _bootstrap()
    from hermes_cli.oneshot import _run_agent

    response = _run_agent(
        prompt,
        model=os.getenv("QUALITY_LLM_MODEL", "").strip() or None,
        provider=None,
        toolsets=[NO_TOOLS_SENTINEL],
        use_config_toolsets=False,
    )
    if not (response or "").strip():
        print("quality runner: no final response", file=sys.stderr)
        return 1
    print(response.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
