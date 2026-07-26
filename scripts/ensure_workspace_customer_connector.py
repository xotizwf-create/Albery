#!/usr/bin/env python3
"""Provision the dedicated zero-tool Hermes connector used by customer turns.

Run after database migrations and before enabling ``FUNNEL_WORKSPACE_AI_ENABLED``.  The script
never prints the connector token, does not register a Bitrix/Telegram bridge and refuses to
reuse a row that unexpectedly has one.  The committed manifest is the hard tool cap.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import sys
import time
import urllib.parse
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent_knowledge import load_manifest  # noqa: E402
from shared.db import connect  # noqa: E402


DEFAULT_SLUG = "iu-customer-runtime"
DEFAULT_NAME = "ИУ — безопасный клиентский runtime"
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,79}$")


def connector_slug() -> str:
    slug = (
        os.getenv("FUNNEL_WORKSPACE_CUSTOMER_TOOLSET_SLUG") or DEFAULT_SLUG
    ).strip() or DEFAULT_SLUG
    if not _SLUG_RE.fullmatch(slug):
        raise RuntimeError("Customer connector slug has an unsafe format.")
    return slug


def assert_zero_tool_manifest(slug: str) -> None:
    manifest = load_manifest(slug)
    if "tools" not in manifest or manifest.get("tools"):
        raise RuntimeError(
            f"Refusing to provision agent-{slug}: its manifest is not capped to zero tools."
        )


def assert_reusable_agent_row(row: dict) -> None:
    if row.get("bitrix_bot_id") or row.get("telegram_bot_token"):
        raise RuntimeError("Existing customer runtime has an external messaging bridge.")
    tools = row.get("tools")
    tools_empty = tools in (None, "", "{}", []) or tools == {}
    if (
        str(row.get("name") or "") != DEFAULT_NAME
        or str(row.get("tier") or "") != "faq"
        or not bool(row.get("tools_customized"))
        or not tools_empty
        or str(row.get("role_prompt") or "")
    ):
        raise RuntimeError(
            "Refusing to overwrite an unexpected existing agent row; "
            "choose a new dedicated customer runtime slug."
        )


def ensure_database_agent(slug: str) -> str:
    """Return the secret connector token without logging it."""

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT mcp_token, bitrix_bot_id, telegram_bot_token,
                       name, role_prompt, tier, tools, tools_customized
                  FROM agents
                 WHERE slug = %s
                 FOR UPDATE
                """,
                (slug,),
            )
            row = cur.fetchone()
            if row:
                assert_reusable_agent_row(dict(row))
                token = str(row.get("mcp_token") or "").strip()
                if not token:
                    token = secrets.token_urlsafe(32)
                cur.execute(
                    """
                    UPDATE agents
                       SET name = %s,
                           role_prompt = '',
                           tier = 'faq',
                           tools = '{}',
                           tools_customized = TRUE,
                           mcp_token = %s,
                           is_active = TRUE,
                           updated_at = now()
                     WHERE slug = %s
                    """,
                    (DEFAULT_NAME, token, slug),
                )
                return token

            token = secrets.token_urlsafe(32)
            cur.execute(
                """
                INSERT INTO agents (
                    slug, name, role_prompt, tier, tools, tools_customized,
                    mcp_token, is_active, color
                )
                VALUES (%s, %s, '', 'faq', '{}', TRUE, %s, TRUE, 'GRAY')
                """,
                (slug, DEFAULT_NAME, token),
            )
            return token


def connector_block(slug: str, token: str, public_base: str) -> str:
    raw_base = str(public_base or "").strip()
    parsed = urllib.parse.urlparse(raw_base)
    if (
        not raw_base
        or "\r" in raw_base
        or "\n" in raw_base
        or parsed.scheme.lower() != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.params
    ):
        raise RuntimeError(
            "AGENT_MCP_PUBLIC_BASE must be an explicit HTTPS base without credentials, "
            "query or fragment."
        )
    base = urllib.parse.urlunparse(
        ("https", parsed.netloc, parsed.path.rstrip("/"), "", "", "")
    )
    return (
        f"  agent-{slug}:\n"
        f"    url: {base}/mcp-agent/{slug}/{token}\n"
        "    enabled: true\n"
        "    timeout: 300\n"
    )


def replace_connector_block(text: str, slug: str, block: str) -> str:
    """Replace/add one top-level ``mcp_servers`` child without re-dumping the config."""

    lines = text.splitlines(keepends=True)
    marker = f"  agent-{slug}:"
    start = next((index for index, line in enumerate(lines) if line.rstrip() == marker), None)
    if start is not None:
        end = start + 1
        while end < len(lines):
            line = lines[end]
            if line.startswith("    ") or not line.strip():
                end += 1
                continue
            break
        return "".join(lines[:start]) + block + "".join(lines[end:])

    insert_at = next(
        (index + 1 for index, line in enumerate(lines) if line.rstrip() == "mcp_servers:"),
        None,
    )
    if insert_at is None:
        raise RuntimeError("Hermes config has no mcp_servers section.")
    return "".join(lines[:insert_at]) + block + "".join(lines[insert_at:])


def ensure_hermes_config(slug: str, token: str) -> Path:
    config_path = Path(
        os.getenv("HERMES_CONFIG", "/root/.hermes/config.yaml")
    ).expanduser()
    if not config_path.is_file():
        raise RuntimeError(f"Hermes config does not exist: {config_path}")
    original = config_path.read_text(encoding="utf-8")
    public_base = os.getenv("AGENT_MCP_PUBLIC_BASE", "").strip()
    updated = replace_connector_block(
        original,
        slug,
        connector_block(slug, token, public_base),
    )
    yaml.safe_load(updated)
    if updated == original:
        return config_path

    backup = config_path.with_name(
        f"{config_path.name}.bak-workspace-{int(time.time())}"
    )
    shutil.copy2(config_path, backup)
    temp = config_path.with_name(f".{config_path.name}.workspace.tmp")
    temp.write_text(updated, encoding="utf-8")
    os.chmod(temp, 0o600)
    os.replace(temp, config_path)
    os.chmod(config_path, 0o600)
    return config_path


def main() -> int:
    slug = connector_slug()
    assert_zero_tool_manifest(slug)
    token = ensure_database_agent(slug)
    ensure_hermes_config(slug, token)
    print(f"agent-{slug}: active, bridge-free, manifest tool cap = 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
