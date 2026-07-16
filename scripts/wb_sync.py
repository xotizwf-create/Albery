#!/usr/bin/env python3
"""Run WB analytics sync with the shared advisory lock.

Usage: wb_sync.py            # incremental (cron, every 30 min)
       wb_sync.py --initial 182   # one-off history backfill (sequential, hours!)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

# Import app first: it initializes Flask/routes and avoids circular partial imports.
from app import pg_connect  # noqa: E402
import wb_cabinet  # noqa: E402

WB_SYNC_ADVISORY_LOCK_KEY = 984312077


def main() -> int:
    initial = None
    if "--initial" in sys.argv:
        idx = sys.argv.index("--initial")
        initial = int(sys.argv[idx + 1]) if len(sys.argv) > idx + 1 else 182
    started = time.time()
    with pg_connect() as lock_conn:
        with lock_conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s) AS locked", (WB_SYNC_ADVISORY_LOCK_KEY,))
            row = cur.fetchone() or {}
            if not bool(row.get("locked")):
                print(json.dumps({"ok": False, "busy": True, "message": "WB sync already running."}, ensure_ascii=False))
                return 0
            try:
                result = wb_cabinet.sync_all(initial_days=initial)
            finally:
                cur.execute("SELECT pg_advisory_unlock(%s)", (WB_SYNC_ADVISORY_LOCK_KEY,))
    print(json.dumps({"ok": True, "initial_days": initial, "elapsed_sec": round(time.time() - started, 1),
                      "result": result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
