#!/usr/bin/env python3
"""Print the complete reviewed MCP capability inventory as Markdown or JSON."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

# Include conditionally enabled production tools in the audited potential registry.
os.environ.setdefault("ALBERY_ALLOW_SHEET_WRITE", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp.context_server import TOOLS  # noqa: E402
from mcp.tool_policy import REVIEWED_TOOL_NAMES, policy_for  # noqa: E402


SELF_DESCRIPTIONS = {
    "delete_my_automation": "Удалить собственную автоматизацию агента.",
    "delete_my_instruction": "Удалить собственную самообученную инструкцию агента.",
    "list_my_automations": "Показать автоматизации текущего агента.",
    "list_my_instructions": "Показать личные инструкции и навыки текущего агента.",
    "schedule_my_automation": "Создать или обновить расписание собственной автоматизации.",
    "upsert_my_instruction": "Создать или обновить личную самообученную инструкцию.",
}
OPTIONAL_DESCRIPTIONS = {
    "write_company_sheet": "Записать значения в разрешённую рабочую таблицу компании.",
}


def _short_description(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    sentence = text.split(". ", 1)[0].strip()
    return (sentence if sentence else text)[:220].replace("|", "\\|")


def rows() -> list[dict[str, Any]]:
    descriptions = {name: spec.get("description") for name, spec in TOOLS.items()}
    descriptions.update(SELF_DESCRIPTIONS)
    descriptions.update(OPTIONAL_DESCRIPTIONS)
    missing = set(REVIEWED_TOOL_NAMES) - set(descriptions)
    extra = set(descriptions) - set(REVIEWED_TOOL_NAMES)
    if missing or extra:
        raise RuntimeError(f"inventory drift: missing={sorted(missing)}, extra={sorted(extra)}")
    out = []
    for name in sorted(REVIEWED_TOOL_NAMES):
        policy = policy_for(name)
        out.append(
            {
                "name": name,
                "domain": policy.domain,
                "effect": policy.effect,
                "confirmation": policy.confirmation,
                "sensitive_data": policy.sensitive_data,
                "automation_effect_ledger": policy.automation_effect_ledger,
                "business_object_lock": policy.business_object_lock,
                "description": _short_description(descriptions[name]),
            }
        )
    return out


def markdown(items: list[dict[str, Any]]) -> str:
    lines = [
        "# MCP capability inventory",
        "",
        "Generated from the versioned runtime registry and `mcp.tool_policy`.",
        "The inventory includes 160 regular and 6 profile self-service tools.",
        "",
        "| Tool | Domain | Effect | Confirm | Sensitive | Automation ledger / lock | Human description |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in items:
        ledger = "yes / yes" if item["automation_effect_ledger"] else "no / no"
        lines.append(
            f"| `{item['name']}` | {item['domain']} | {item['effect']} | "
            f"{item['confirmation']} | {'yes' if item['sensitive_data'] else 'no'} | "
            f"{ledger} | {item['description']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    args = parser.parse_args()
    items = rows()
    if args.format == "json":
        print(json.dumps(items, ensure_ascii=False, indent=2))
    else:
        print(markdown(items), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
