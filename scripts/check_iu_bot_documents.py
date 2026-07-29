#!/usr/bin/env python3
"""Read-only health check for the three PDFs used by the IU client bot."""
from __future__ import annotations

import os
import sys

os.environ.setdefault("B24_TASK_OFFER", "0")
os.environ.setdefault("B24_TASK_CHECKIN", "0")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import iu_bot_documents


def main() -> int:
    for kind in ("terms", "contract", "faq"):
        data = iu_bot_documents.pdf_bytes(kind)
        if not data.startswith(b"%PDF-") or len(data) < 1000:
            raise RuntimeError(f"{kind}: некорректный PDF ({len(data)} байт)")
        print(f"{kind}: ok, {len(data)} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
