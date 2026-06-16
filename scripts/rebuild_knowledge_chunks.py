#!/usr/bin/env python3
"""(Re)build the company_knowledge_chunks index used by search_company_knowledge.

Run on deploy to warm the index, or manually to force a full rebuild:

    .venv/bin/python scripts/rebuild_knowledge_chunks.py            # incremental (changed docs)
    .venv/bin/python scripts/rebuild_knowledge_chunks.py --force    # rebuild every document

The MCP search tool also self-refreshes via shared.knowledge_chunks.ensure_fresh, so this
script is for an explicit/warm build; it is safe to run repeatedly.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.db import connect  # noqa: E402
from shared.knowledge_chunks import rebuild  # noqa: E402


def main(argv: list[str]) -> int:
    force = "--force" in argv
    with connect() as conn:
        stats = rebuild(conn, force=force)
    print(f"knowledge chunks rebuild ({'force' if force else 'incremental'}): {stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
