"""Allowlisted in-process calls for deterministic Albery services.

This is not an MCP server and has no network route. It lets trusted deterministic code reuse the
same validated tool handlers without acquiring a broad shared HTTP credential. Model-facing calls
must always go through the exact per-agent connector instead.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections.abc import Collection
from typing import Any


TELEGRAM_RUNTIME_TOOLS = frozenset({
    "add_deal_comment",
    "create_crm_deal",
    "delete_crm_deal",
    "get_crm_deal",
    "list_crm_lead_contacts",
    "notify_iu_group",
    "update_crm_deal",
})

MAINTENANCE_TOOLS = frozenset({
    "delete_zoom_call_report",
    "upsert_ai_instruction",
})

PROFILES = {
    "telegram-runtime": TELEGRAM_RUNTIME_TOOLS,
    "maintenance": MAINTENANCE_TOOLS,
}


class InternalToolError(RuntimeError):
    pass


def call_internal_tool(name: str, arguments: dict[str, Any], *, allowed: Collection[str]) -> dict[str, Any]:
    """Invoke one registered synchronous handler after an explicit caller allowlist check."""
    tool_name = str(name or "").strip()
    if tool_name not in set(allowed):
        raise InternalToolError(f"internal tool is not allowed for this caller: {tool_name or '<empty>'}")
    if not isinstance(arguments, dict):
        raise InternalToolError("internal tool arguments must be an object")

    from mcp.context_server import TOOLS

    spec = TOOLS.get(tool_name)
    handler = spec.get("handler") if isinstance(spec, dict) else None
    if not callable(handler):
        raise InternalToolError(f"registered tool has no callable handler: {tool_name}")

    started = time.monotonic()
    try:
        result = handler(arguments)
    except Exception:
        logging.exception("internal_tool name=%s status=error", tool_name)
        raise
    if not isinstance(result, dict):
        raise InternalToolError(f"internal tool returned a non-object: {tool_name}")
    logging.info(
        "internal_tool name=%s status=ok duration_ms=%s",
        tool_name,
        int((time.monotonic() - started) * 1000),
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one allowlisted in-process Albery tool")
    parser.add_argument("--profile", choices=sorted(PROFILES), required=True)
    args = parser.parse_args(argv)
    try:
        payload = json.load(sys.stdin)
        result = call_internal_tool(
            payload.get("tool"),
            payload.get("arguments") or {},
            allowed=PROFILES[args.profile],
        )
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False, default=str))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)[:300]}},
                ensure_ascii=False,
            ),
            file=sys.stdout,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
