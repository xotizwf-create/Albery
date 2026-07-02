"""Smoke tests: the backend and MCP server import and stay free of undefined names.

These guard the exact failure that the audit cleanup introduced once already:
deleting still-referenced functions so `import app` / the MCP module raised
NameError and neither process could start.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _undefined_names(rel_path: str) -> list[str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pyflakes", rel_path],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    return [line for line in out.splitlines() if "undefined name" in line.lower()]


def test_app_imports_with_routes(app_module):
    rules = list(app_module.app.url_map.iter_rules())
    # Was 111 routes after repair; allow growth, guard against catastrophic loss.
    assert len(rules) >= 100


def test_b24bot_routes_registered(app_module):
    """The chat-bot lives in b24bot.py (extracted 2026-07-02) but must still register
    its routes on the shared Flask app when app.py is imported."""
    paths = {r.rule for r in app_module.app.url_map.iter_rules()}
    assert "/bitrix/imbot/<secret>" in paths
    assert "/api/agent-access" in paths


def test_mcp_imports_with_tools(ctx):
    assert len(ctx.TOOLS) >= 30
    assert callable(ctx.handle_request)


@pytest.mark.parametrize("rel_path", ["app.py", "b24bot.py", "bitrix.py", "config.py",
                                      "gdrive.py", "llm.py", "utils.py", "zoom.py",
                                      "mcp/context_server.py"])
def test_no_undefined_names(rel_path):
    offenders = _undefined_names(rel_path)
    assert offenders == [], f"{rel_path} has undefined names:\n" + "\n".join(offenders)
