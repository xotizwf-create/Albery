"""One-shot exporter: materialise the agent knowledge registry into git files.

Reads the LIVE sources on the prod box —
  * ai_instruction_folders  (Postgres)  -> agent_knowledge/instructions/**.md
  * /root/.hermes/skills/**  (built-in Hermes skills) -> agent_knowledge/hermes_base/**
  * custom skills (scripts/hermes_skills / flagged) -> agent_knowledge/skills/**

so that GitHub becomes the source of truth (versioned, reviewable). Idempotent:
regenerates `instructions/` and `hermes_base/` from scratch each run; adds custom
skills into `skills/` only if missing (never clobbers promoted / hand-edited ones);
never touches `agents/` (manifests + learned skills live there).

Run on 186:  cd /var/www/albery && .venv/bin/python scripts/export_knowledge_to_git.py [--dry-run]
The generated files are then committed + pushed from the box (deploy key) or reviewed first.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from shared.db import database_url  # noqa: E402

KNOWLEDGE = ROOT / "agent_knowledge"
INSTR_DIR = KNOWLEDGE / "instructions"
SKILLS_DIR = KNOWLEDGE / "skills"
HERMES_BASE_DIR = KNOWLEDGE / "hermes_base"
LIVE_SKILLS = Path("/root/.hermes/skills")
LEGACY_REPO_SKILLS = ROOT / "scripts" / "hermes_skills"

DRY = "--dry-run" in sys.argv


def _safe_component(name: str) -> str:
    """Filesystem-safe path component: keep readable names, drop only separators."""
    cleaned = name.replace("/", "∕").replace("\\", "∖").strip().strip(".")
    return cleaned or "unnamed"


def _fetch_instruction_rows() -> list[dict]:
    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH RECURSIVE t AS (
                    SELECT id, parent_id, name, content, sort_order,
                           ARRAY[name]::text[] AS path
                    FROM ai_instruction_folders WHERE parent_id IS NULL
                    UNION ALL
                    SELECT c.id, c.parent_id, c.name, c.content, c.sort_order,
                           t.path || c.name
                    FROM ai_instruction_folders c JOIN t ON t.id = c.parent_id
                )
                SELECT id, parent_id, name, content, sort_order, path,
                       (SELECT count(*) FROM ai_instruction_folders k WHERE k.parent_id = t.id) AS n_children
                FROM t ORDER BY path
                """
            )
            return cur.fetchall()


def export_instructions() -> int:
    rows = _fetch_instruction_rows()
    if not DRY:
        if INSTR_DIR.exists():
            shutil.rmtree(INSTR_DIR)
        INSTR_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    for r in rows:
        content = (r["content"] or "").strip()
        # A pure container (no own content, has children) needs no file — its
        # directory is created implicitly when its children are written.
        if not content:
            continue
        parts = [_safe_component(p) for p in r["path"]]
        rel = Path(*parts).with_suffix(".md")
        dest = INSTR_DIR / rel
        name = r["name"].replace("\n", " ").strip()
        front = (
            "---\n"
            f"name: {name}\n"
            "scope: universal\n"  # start universal → zero behaviour regression; owner narrows later
            f"sort_order: {int(r['sort_order'] or 0)}\n"
            f"db_id: {r['id']}\n"
            "---\n\n"
        )
        if DRY:
            print(f"[instr] {rel}  ({len(content)} chars)")
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(front + content + "\n", encoding="utf-8")
        written += 1
    return written


def _custom_skill_names() -> set[str]:
    if LEGACY_REPO_SKILLS.is_dir():
        return {p.name for p in LEGACY_REPO_SKILLS.iterdir() if p.is_dir()}
    return set()


def export_skills() -> tuple[int, int]:
    custom = _custom_skill_names()
    base_count = 0
    shared_count = 0
    if not DRY:
        if HERMES_BASE_DIR.exists():
            shutil.rmtree(HERMES_BASE_DIR)
        HERMES_BASE_DIR.mkdir(parents=True, exist_ok=True)
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    if not LIVE_SKILLS.is_dir():
        print(f"WARNING: {LIVE_SKILLS} not found — skipping skill snapshot")
        return base_count, shared_count
    for entry in sorted(LIVE_SKILLS.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith("."):
            # Hermes runtime/state dirs (.hub, .curator_backups, …) — not skills.
            continue
        if entry.name in custom:
            # Custom skill -> shared library (connectable). Add only if missing so a
            # promoted / hand-edited version is never clobbered.
            dest = SKILLS_DIR / entry.name
            if dest.exists():
                print(f"[skill:shared] {entry.name} — already present, keep")
            elif DRY:
                print(f"[skill:shared] {entry.name} (would copy)")
                shared_count += 1
            else:
                shutil.copytree(entry, dest)
                shared_count += 1
        else:
            dest = HERMES_BASE_DIR / entry.name
            if DRY:
                n = len(list(entry.rglob("SKILL.md")))
                print(f"[skill:base] {entry.name} ({n} SKILL.md)")
            else:
                shutil.copytree(entry, dest)
            base_count += len(list(entry.rglob("SKILL.md")))
    return base_count, shared_count


def main() -> None:
    print(f"ROOT={ROOT}  DRY={DRY}")
    n_instr = export_instructions()
    n_base, n_shared = export_skills()
    print(f"\nSUMMARY: instructions={n_instr}  hermes_base_skills={n_base}  shared_skills={n_shared}")
    if DRY:
        print("(dry run — nothing written)")


if __name__ == "__main__":
    main()
