#!/usr/bin/env python3
"""Run Albery company Google Drive documents sync with the shared advisory lock."""
from __future__ import annotations

import json
import time
from pathlib import Path
import sys

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

# Import app first: it initializes Flask/routes and avoids circular partial imports.
import app  # noqa: E402
from app import pg_connect  # noqa: E402
from gdrive import GOOGLE_DRIVE_SYNC_ADVISORY_LOCK_KEY, sync_google_drive_company_documents  # noqa: E402


def main() -> int:
    started = time.time()
    with pg_connect() as lock_conn:
        with lock_conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s) AS locked", (GOOGLE_DRIVE_SYNC_ADVISORY_LOCK_KEY,))
            row = cur.fetchone() or {}
            if not bool(row.get("locked")):
                print(json.dumps({"ok": False, "busy": True, "message": "Company Drive sync already running."}, ensure_ascii=False))
                return 0
            try:
                result = sync_google_drive_company_documents()
            finally:
                cur.execute("SELECT pg_advisory_unlock(%s)", (GOOGLE_DRIVE_SYNC_ADVISORY_LOCK_KEY,))

    print(json.dumps({"ok": True, "elapsed_sec": round(time.time() - started, 1), "result": result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
