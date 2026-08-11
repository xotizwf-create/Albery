#!/usr/bin/env python3
"""Materialize live + automation aliases for every agent without rotating credentials."""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ensure_workspace_customer_connector import internal_base  # noqa: E402
from scripts.migrate_private_mcp import atomic_write, private_config  # noqa: E402
from shared.db import connect  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    load_dotenv(ROOT / ".env")
    config_path = Path(os.getenv("HERMES_CONFIG", "/root/.hermes/config.yaml")).expanduser()
    original = config_path.read_text(encoding="utf-8")
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT slug, mcp_token FROM agents ORDER BY slug")
            agents = [
                {"slug": str(row["slug"]), "token": str(row["mcp_token"] or "")}
                for row in cur.fetchall()
            ]
    if any(not row["token"] for row in agents):
        raise RuntimeError("one or more agents have no private MCP credential")
    updated = private_config(original, agents, internal_base(ROOT / ".env"))
    print(f"automation connector materialization validated: agents={len(agents)}")
    if args.dry_run or updated == original:
        return 0

    backup = config_path.with_name(
        f"{config_path.name}.bak-automation-connectors-{time.strftime('%Y%m%d_%H%M%S')}"
    )
    shutil.copy2(config_path, backup)
    backup.chmod(0o600)
    atomic_write(config_path, updated)
    print(f"automation connector aliases applied: agents={len(agents)}, backup={backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
