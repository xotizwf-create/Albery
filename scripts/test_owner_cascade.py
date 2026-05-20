"""One-off test: run owner-daily cascade for 30.03.2026 and print result.

Usage:
    .\.venv\Scripts\python.exe scripts/test_owner_cascade.py
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as backend  # type: ignore  # noqa: E402


def main() -> int:
    target = date(2026, 3, 30)
    print(f"== cascade_owner_daily({target.isoformat()}) ==")
    result = backend.cascade_owner_daily(target, force=False)
    owner = result.get("owner_daily") or {}
    chat_daily = result.get("chat_daily") or {}
    overall = result.get("chat_overall_daily") or {}
    print("chat_daily:", json.dumps({k: v for k, v in chat_daily.items() if k != "errors"}, ensure_ascii=False))
    if chat_daily.get("errors"):
        print("chat_daily errors:", json.dumps(chat_daily["errors"], ensure_ascii=False))
    print("chat_overall_daily.report_id:", overall.get("report_id"))
    print("chat_overall_daily.summary:", overall.get("summary"))
    print()
    print("== Owner daily report ==")
    print("report_id:", owner.get("report_id"))
    print("version:", owner.get("version"))
    print("generated_at_text:", owner.get("generated_at_text"))
    print()
    print("--- summary ---")
    print(owner.get("summary"))
    print()
    print("--- dynamics_summary ---")
    print(owner.get("dynamics_summary"))
    print()
    print("--- risks_summary ---")
    print(owner.get("risks_summary"))
    print()
    print("--- recommendations ---")
    print(owner.get("recommendations"))
    print()
    print("--- report_text ---")
    print(owner.get("report_text"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
