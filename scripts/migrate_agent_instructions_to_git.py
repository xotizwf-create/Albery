"""One-shot: migrate agent_instructions (DB) -> git files under
agent_knowledge/agents/<slug>/learned/*.md with attribution frontmatter.

Run on the box:  cd /var/www/albery && .venv/bin/python scripts/migrate_agent_instructions_to_git.py [--dry-run]
Idempotent per file: skips an instruction whose file already exists (so a re-run
after new self-learning never clobbers fresher git content).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from shared.db import database_url  # noqa: E402
import agent_knowledge as k  # noqa: E402

DRY = "--dry-run" in sys.argv


def _iso(dt) -> str:
    if dt is None:
        return k._msk_now_iso()
    try:
        return dt.astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
    except Exception:  # noqa: BLE001
        return str(dt)


def main() -> None:
    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT a.slug, i.name, i.content, i.source, i.created_at, i.updated_at"
                " FROM agent_instructions i JOIN agents a ON a.id = i.agent_id"
                " ORDER BY a.slug, i.created_at"
            )
            rows = cur.fetchall()
    migrated = skipped = 0
    for r in rows:
        slug, name, source = r["slug"], r["name"], (r["source"] or "owner")
        dest = k._learned_file(slug, name)
        if dest.exists():
            skipped += 1
            print(f"[skip] {slug}/{name} (file exists)")
            continue
        actor = "владелец (перенос из БД)" if source == "owner" else "агент (самообучение, перенос из БД)"
        if DRY:
            print(f"[migrate] {slug}/{name}  source={source}")
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            meta = {
                "name": name.strip(), "source": "self" if source == "self" else "owner",
                "created_by": actor, "created_at": _iso(r["created_at"]),
                "updated_by": actor, "updated_at": _iso(r["updated_at"]),
            }
            front = "---\n" + "".join(f"{kk}: {vv}\n" for kk, vv in meta.items()) + "---\n\n"
            dest.write_text(front + (r["content"] or "").strip() + "\n", encoding="utf-8")
        migrated += 1
    print(f"\nSUMMARY: migrated={migrated} skipped_existing={skipped} total_rows={len(rows)}")
    if DRY:
        print("(dry run — nothing written)")


if __name__ == "__main__":
    main()
