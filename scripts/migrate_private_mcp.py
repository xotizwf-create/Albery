#!/usr/bin/env python3
"""Atomically migrate Hermes connectors to private header-authenticated per-agent MCP.

The script never prints credentials. Use `--dry-run` first. Before `--apply`, create an external
`pg_dump` of the agents table; this script backs up and atomically rewrites only Hermes config.
"""
from __future__ import annotations

import argparse
import os
import secrets
import shutil
import stat
import sys
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ensure_workspace_customer_connector import (  # noqa: E402
    connector_block,
    internal_base,
    replace_connector_block,
)
from shared.db import connect  # noqa: E402


SHARED_CONNECTORS = frozenset({
    "albery",
    "albery-faq",
    "albery-ops",
    "albery-core",
    "albery-ops-core",
})


def remove_connector_block(text: str, name: str) -> str:
    lines = text.splitlines(keepends=True)
    marker = f"  {name}:"
    start = next((index for index, line in enumerate(lines) if line.rstrip() == marker), None)
    if start is None:
        return text
    end = start + 1
    while end < len(lines) and (lines[end].startswith("    ") or not lines[end].strip()):
        end += 1
    return "".join(lines[:start]) + "".join(lines[end:])


def automation_connector_block(slug: str, token: str, base: str) -> str:
    return (
        f"  automation-agent-{slug}:\n"
        f"    url: {base.rstrip('/')}/mcp-agent/{slug}\n"
        "    headers:\n"
        f"      Authorization: \"Bearer {token}\"\n"
        "      X-Albery-Automation: \"1\"\n"
        "    enabled: true\n"
        "    timeout: 300\n"
    )


def replace_named_connector_block(text: str, name: str, block: str) -> str:
    without = remove_connector_block(text, name)
    lines = without.splitlines(keepends=True)
    insert_at = next(
        (index + 1 for index, line in enumerate(lines) if line.rstrip() == "mcp_servers:"), None
    )
    if insert_at is None:
        raise RuntimeError("Hermes config has no mcp_servers section")
    return "".join(lines[:insert_at]) + block + "".join(lines[insert_at:])


def private_config(original: str, agents: list[dict[str, str]], base: str) -> str:
    updated = original
    for name in SHARED_CONNECTORS:
        updated = remove_connector_block(updated, name)
    allowed_managed = {
        name
        for agent in agents
        for name in (f"agent-{agent['slug']}", f"automation-agent-{agent['slug']}")
    }
    existing = (yaml.safe_load(updated) or {}).get("mcp_servers") or {}
    for name in set(existing) - allowed_managed:
        if name.startswith(("agent-", "automation-agent-")):
            updated = remove_connector_block(updated, name)
    for agent in agents:
        updated = replace_connector_block(
            updated,
            agent["slug"],
            connector_block(agent["slug"], agent["token"], base),
        )
        updated = replace_named_connector_block(
            updated,
            f"automation-agent-{agent['slug']}",
            automation_connector_block(agent["slug"], agent["token"], base),
        )
    parsed = yaml.safe_load(updated) or {}
    servers = parsed.get("mcp_servers") or {}
    if SHARED_CONNECTORS & set(servers):
        raise RuntimeError("shared connectors remain after migration")
    managed = {
        name for name in servers if name.startswith(("agent-", "automation-agent-"))
    }
    if managed != allowed_managed:
        raise RuntimeError("managed connector set does not exactly match active agents")
    for agent in agents:
        item = servers.get(f"agent-{agent['slug']}")
        if not isinstance(item, dict):
            raise RuntimeError(f"missing connector for agent-{agent['slug']}")
        if agent["token"] in str(item.get("url") or ""):
            raise RuntimeError(f"credential remained in URL for agent-{agent['slug']}")
        if (item.get("headers") or {}).get("Authorization") != f"Bearer {agent['token']}":
            raise RuntimeError(f"missing bearer header for agent-{agent['slug']}")
        automation_item = servers.get(f"automation-agent-{agent['slug']}")
        if not isinstance(automation_item, dict):
            raise RuntimeError(f"missing automation connector for agent-{agent['slug']}")
        automation_headers = automation_item.get("headers") or {}
        if automation_headers.get("Authorization") != f"Bearer {agent['token']}":
            raise RuntimeError(f"missing automation bearer header for agent-{agent['slug']}")
        if str(automation_headers.get("X-Albery-Automation") or "") != "1":
            raise RuntimeError(f"missing automation marker for agent-{agent['slug']}")
        if automation_item.get("url") != item.get("url") or agent["token"] in str(automation_item.get("url") or ""):
            raise RuntimeError(f"invalid automation URL for agent-{agent['slug']}")
    return updated


def atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.private-mcp-{os.getpid()}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
    os.replace(temporary, path)
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    load_dotenv(ROOT / ".env")
    config_path = Path(os.getenv("HERMES_CONFIG", "/root/.hermes/config.yaml")).expanduser()
    if not config_path.is_file():
        raise RuntimeError("Hermes config is unavailable")
    original = config_path.read_text(encoding="utf-8")
    base = internal_base(ROOT / ".env")

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT slug FROM agents ORDER BY slug")
            slugs = [str(row["slug"]) for row in cur.fetchall()]
    agents = [{"slug": slug, "token": secrets.token_urlsafe(32)} for slug in slugs]
    updated = private_config(original, agents, base)
    print(
        f"private MCP migration validated: agents={len(agents)}, "
        f"shared_removed={len(SHARED_CONNECTORS)}, credentials_in_urls=0"
    )
    if args.dry_run:
        return 0

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    backup = config_path.with_name(f"{config_path.name}.bak-private-mcp-{timestamp}")
    shutil.copy2(config_path, backup)
    backup.chmod(stat.S_IRUSR | stat.S_IWUSR)

    wrote_config = False
    try:
        with connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("LOCK TABLE agents IN SHARE ROW EXCLUSIVE MODE")
                    cur.execute("SELECT slug FROM agents ORDER BY slug")
                    locked_slugs = [str(row["slug"]) for row in cur.fetchall()]
                    if locked_slugs != slugs:
                        raise RuntimeError("agents changed during migration; retry from dry-run")
                    for agent in agents:
                        cur.execute(
                            "UPDATE agents SET mcp_token=%s, updated_at=now() WHERE slug=%s",
                            (agent["token"], agent["slug"]),
                        )
                atomic_write(config_path, updated)
                wrote_config = True
    except Exception:
        if wrote_config:
            atomic_write(config_path, original)
        raise

    print(f"private MCP migration applied: agents_rotated={len(agents)}, config_backup={backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
