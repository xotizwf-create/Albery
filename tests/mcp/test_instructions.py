"""MCP instruction files stay consistent with the actual tool registry.

Guards the class of bug optimization.md already hit once (`get_ai_prompts` was
referenced but the real tool is `get_report_contract`): every tool name an
instruction tells the assistant to call must exist in the registry.

The daily/weekly chat report workflow was removed (tools and instruction files
deleted), so those instructions must not come back.
"""
from __future__ import annotations

import re
from pathlib import Path

INSTRUCTIONS_DIR = Path(__file__).resolve().parents[2] / "mcp" / "instructions"
EXPECTED_FILES = {
    "company_daily_report.md",
    "zoom_call_report.md",
}
# Chat daily/weekly reports were intentionally removed; these tools and their
# instruction files must stay gone.
REMOVED_TOOLS = {
    "get_chat_daily_report",
    "save_chat_daily_report",
    "get_chat_weekly_report",
    "save_chat_weekly_report",
}
REMOVED_FILES = {"daily_chat_report.md", "weekly_chat_report.md"}

TOOL_TOKEN = re.compile(r"`([a-z][a-z_]+)`")
TOOL_LIKE = re.compile(r"^(get|list|save|search|start|process|delete|upsert)_[a-z_]+$")


def test_instruction_files_are_exactly_expected():
    present = {p.name for p in INSTRUCTIONS_DIR.glob("*.md")}
    assert present == EXPECTED_FILES, f"unexpected instruction files: {present ^ EXPECTED_FILES}"


def test_removed_instruction_files_are_gone():
    for name in REMOVED_FILES:
        assert not (INSTRUCTIONS_DIR / name).exists(), f"{name} should have been deleted"


def test_instruction_tool_references_exist(ctx):
    """Every tool-like token in every instruction must be a real registered tool."""
    valid = set(ctx.TOOLS)
    broken: dict[str, set[str]] = {}
    for path in INSTRUCTIONS_DIR.glob("*.md"):
        refs = {tok for tok in TOOL_TOKEN.findall(path.read_text(encoding="utf-8")) if TOOL_LIKE.match(tok)}
        unknown = refs - valid
        if unknown:
            broken[path.name] = unknown
    assert not broken, f"instructions reference non-existent tools: {broken}"


def test_old_broken_tool_name_absent():
    # `get_ai_prompts` never existed; the correct tool is `get_report_contract`.
    for path in INSTRUCTIONS_DIR.glob("*.md"):
        assert "get_ai_prompts" not in path.read_text(encoding="utf-8")


def test_removed_chat_report_tools_stay_absent(ctx):
    still_present = REMOVED_TOOLS & set(ctx.TOOLS)
    assert not still_present, f"removed chat-report tools reappeared: {sorted(still_present)}"


def test_no_instruction_mentions_removed_chat_report_tools():
    offenders: dict[str, set[str]] = {}
    for path in INSTRUCTIONS_DIR.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        hits = {tool for tool in REMOVED_TOOLS if tool in text}
        if hits:
            offenders[path.name] = hits
    assert not offenders, f"instructions still mention removed tools: {offenders}"
