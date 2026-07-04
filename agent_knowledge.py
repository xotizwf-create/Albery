"""GitHub-backed registry of agent knowledge — the single source of truth for
company instructions and skills, versioned as plain files under ``agent_knowledge/``
so they can be managed and audited on GitHub (history, review) instead of living
only in the database or the Hermes skills directory.

Layout (see scripts/export_knowledge_to_git.py which generates it):

    agent_knowledge/
      instructions/<path>.md          company instructions (was ai_instruction_folders)
      instructions/<path>/<child>.md  nested instruction (folder that also has children)
      skills/<slug>/SKILL.md          shared skills, connectable to any agent
      hermes_base/<slug>/SKILL.md     backup of the gateway's built-in Hermes skills
      agents/<slug>.yaml              per-agent manifest (connected instructions/skills)
      agents/<slug>/learned/<slug>.md personal, self-learned skills (with attribution)

Every reader here is *fallback-safe*: when the registry directory is absent (e.g.
right after deploying the code but before the export has run) the loaders return
``None`` so callers keep using the legacy DB / skills-dir source. That makes the
switch to git a no-op until the files actually exist.

Instruction ``path`` uses the same " / " join as
``mcp.context_server.load_ai_instructions`` so per-agent scoping (Phase 2) filters
identically whether the source is the DB or these files.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

# Root of the registry. context_server.py lives in mcp/, agent_center.py at the repo
# root — both resolve to the same checkout (/var/www/albery on prod).
KNOWLEDGE_DIR = Path(
    os.getenv("AGENT_KNOWLEDGE_DIR", str(Path(__file__).resolve().parent / "agent_knowledge"))
)
INSTRUCTIONS_DIR = KNOWLEDGE_DIR / "instructions"
SKILLS_DIR = KNOWLEDGE_DIR / "skills"
HERMES_BASE_DIR = KNOWLEDGE_DIR / "hermes_base"
AGENTS_DIR = KNOWLEDGE_DIR / "agents"

# Instruction scope values (frontmatter `scope:`). universal = injected for every
# agent (base behaviour); optional = only when a manifest connects it.
SCOPE_UNIVERSAL = "universal"
SCOPE_OPTIONAL = "optional"


# --- frontmatter -----------------------------------------------------------------

def parse_doc(text: str) -> tuple[dict[str, str], str]:
    """Split a `--- frontmatter --- body` markdown document. Frontmatter is simple
    ``key: value`` lines (no nested YAML needed here). Returns (meta, body)."""
    meta: dict[str, str] = {}
    if not text.startswith("---"):
        return meta, text
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return meta, text
    body_start = len(lines)
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            body_start = i + 1
            break
        m = re.match(r"^([A-Za-z0-9_]+):\s?(.*)$", lines[i])
        if m:
            meta[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    body = "\n".join(lines[body_start:]).strip("\n")
    return meta, body


# --- instructions ----------------------------------------------------------------

def _instruction_path_from_file(md: Path) -> str:
    """`instructions/Формирование отчетов/Еженедельный отчет.md`
    -> `Формирование отчетов / Еженедельный отчет` (matches the DB tree path)."""
    rel = md.relative_to(INSTRUCTIONS_DIR)
    parts = list(rel.parts[:-1]) + [rel.stem]
    return " / ".join(parts)


def load_instructions() -> list[dict[str, Any]] | None:
    """All company instructions from the registry, or ``None`` if the registry is
    absent (caller falls back to the DB). Each item:
    ``{id, path, name, parent, content, scope, updated_at}``. ``id`` == ``path``
    (stable, human-readable, survives regeneration — unlike a DB uuid)."""
    if not INSTRUCTIONS_DIR.is_dir():
        return None
    from datetime import datetime, timezone

    out: list[dict[str, Any]] = []
    for md in sorted(INSTRUCTIONS_DIR.rglob("*.md")):
        try:
            meta, body = parse_doc(md.read_text(encoding="utf-8", errors="replace"))
            path = _instruction_path_from_file(md)
            parts = path.split(" / ")
            scope = (meta.get("scope") or SCOPE_UNIVERSAL).strip().lower()
            if scope not in (SCOPE_UNIVERSAL, SCOPE_OPTIONAL):
                scope = SCOPE_UNIVERSAL
            out.append({
                "id": path,
                "path": path,
                "name": meta.get("name") or parts[-1],
                "parent": " / ".join(parts[:-1]),
                "content": body,
                "scope": scope,
                "sort_order": _safe_int(meta.get("sort_order")),
                "updated_at": datetime.fromtimestamp(md.stat().st_mtime, tz=timezone.utc),
            })
        except Exception:  # noqa: BLE001 — one bad file must not sink the whole load
            import logging
            logging.exception("agent_knowledge: instruction parse failed for %s", md)
    out.sort(key=lambda r: (r["sort_order"], r["path"]))
    return out


def universal_instruction_paths() -> set[str]:
    """Paths of instructions that go to EVERY agent regardless of its manifest."""
    items = load_instructions() or []
    return {i["path"] for i in items if i["scope"] == SCOPE_UNIVERSAL}


# --- skills ----------------------------------------------------------------------

def _load_skill_dir(base: Path, kind: str) -> list[dict[str, Any]]:
    from datetime import datetime, timezone

    out: list[dict[str, Any]] = []
    if not base.is_dir():
        return out
    for skill_md in sorted(base.rglob("SKILL.md")):
        try:
            rel = skill_md.relative_to(base).parts
            meta, _body = parse_doc(skill_md.read_text(encoding="utf-8", errors="replace"))
            name = meta.get("name") or skill_md.parent.name
            desc = re.sub(r"\s+", " ", meta.get("description") or "").strip()
            out.append({
                "id": "skill:" + "/".join(rel[:-1]),
                "slug": skill_md.parent.name,
                "title": name,
                "parent": rel[0] if len(rel) > 2 else "",
                "description": (desc[:160].rstrip() + "…") if len(desc) > 160 else desc,
                "kind": kind,  # "shared" | "hermes_base"
                "custom": kind == "shared",
                "updated_at": datetime.fromtimestamp(skill_md.stat().st_mtime, tz=timezone.utc),
            })
        except Exception:  # noqa: BLE001
            import logging
            logging.exception("agent_knowledge: skill parse failed for %s", skill_md)
    return out


def load_skills() -> list[dict[str, Any]] | None:
    """Shared skills + Hermes base skills from the registry, or ``None`` if the
    registry has neither directory (caller falls back to the live skills dir)."""
    if not SKILLS_DIR.is_dir() and not HERMES_BASE_DIR.is_dir():
        return None
    return _load_skill_dir(SKILLS_DIR, "shared") + _load_skill_dir(HERMES_BASE_DIR, "hermes_base")


# --- per-agent manifests ---------------------------------------------------------
# agent_knowledge/agents/<slug>.yaml lists the OPTIONAL instructions and skills an
# agent is connected to. Universal instructions are always on regardless. This file
# is the source of truth for the capability panel and for enforcement, so a doc an
# agent is not connected to is neither injected nor returned by start_here.

def _manifest_path(slug: str) -> Path:
    return AGENTS_DIR / f"{slug}.yaml"


def load_manifest(slug: str) -> dict[str, list[str]]:
    """Connected instructions/skills for one agent. Missing file -> empty lists."""
    path = _manifest_path(slug)
    if not path.is_file():
        return {"instructions": [], "skills": []}
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        import logging
        logging.exception("agent_knowledge: manifest load failed for %s", slug)
        return {"instructions": [], "skills": []}
    instr = [str(x) for x in (data.get("instructions") or []) if str(x).strip()]
    skills = [str(x) for x in (data.get("skills") or []) if str(x).strip()]
    return {"instructions": instr, "skills": skills}


def save_manifest(slug: str, instructions: list[str], skills: list[str]) -> Path:
    """Persist an agent's connected instructions/skills as a readable yaml manifest.
    Written to the working tree; a watchdog commits+pushes it to GitHub (history)."""
    import yaml
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    path = _manifest_path(slug)
    payload = {
        "slug": slug,
        "instructions": sorted(set(instructions)),
        "skills": sorted(set(skills)),
    }
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    return path


def allowed_instruction_paths(slug: str) -> set[str] | None:
    """Instruction paths this agent may receive = every universal instruction PLUS the
    optional ones its manifest connects. ``None`` when the registry is absent (caller
    then applies no scoping = full tree, preserving legacy behaviour)."""
    items = load_instructions()
    if items is None:
        return None
    all_paths = {i["path"] for i in items}
    universal = {i["path"] for i in items if i["scope"] == SCOPE_UNIVERSAL}
    connected = set(load_manifest(slug)["instructions"]) & all_paths
    return universal | connected


def _instruction_file_for_path(path: str) -> Path:
    parts = [p.strip() for p in path.split(" / ") if p.strip()]
    return INSTRUCTIONS_DIR.joinpath(*parts).with_suffix(".md")


def set_instruction_scope(path: str, scope: str) -> bool:
    """Flip a library instruction between universal (all agents) and optional
    (per-agent). Rewrites only the ``scope:`` frontmatter line of its file."""
    scope = (scope or "").strip().lower()
    if scope not in (SCOPE_UNIVERSAL, SCOPE_OPTIONAL):
        raise ValueError("scope must be 'universal' or 'optional'")
    md = _instruction_file_for_path(path)
    if not md.is_file():
        return False
    text = md.read_text(encoding="utf-8", errors="replace")
    meta, body = parse_doc(text)
    meta["scope"] = scope
    ordered = ["name", "scope", "sort_order", "db_id"]
    keys = [k for k in ordered if k in meta] + [k for k in meta if k not in ordered]
    front = "---\n" + "".join(f"{k}: {meta[k]}\n" for k in keys) + "---\n\n"
    md.write_text(front + body.strip("\n") + "\n", encoding="utf-8")
    return True


# --- per-agent personal instructions ("Личные инструкции") -----------------------
# An agent's own instructions/skills — either taught by the owner (source=owner) or
# distilled by the agent itself after good work (source=self). Stored as git files with
# full attribution frontmatter (who created, who last edited, from which dialog) so every
# change is trackable, plus git history on top.

LEARNED_DIRNAME = "learned"


def _msk_now_iso() -> str:
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone(timedelta(hours=3))).strftime("%Y-%m-%dT%H:%M:%S+03:00")


def _learned_dir(slug: str) -> Path:
    return AGENTS_DIR / slug / LEARNED_DIRNAME


def _learned_file(slug: str, name: str) -> Path:
    return _learned_dir(slug) / f"{_safe_component(name)[:80]}.md"


def _safe_component(name: str) -> str:
    cleaned = (name or "").replace("/", "∕").replace("\\", "∖").strip().strip(".")
    return cleaned or "unnamed"


def load_agent_learned(slug: str) -> list[dict[str, Any]] | None:
    """Personal instructions of one agent, or ``None`` if the agent has no learned dir
    yet (caller may fall back to the legacy agent_instructions table)."""
    d = _learned_dir(slug)
    if not d.is_dir():
        return None
    out: list[dict[str, Any]] = []
    for md in sorted(d.glob("*.md")):
        try:
            meta, body = parse_doc(md.read_text(encoding="utf-8", errors="replace"))
            out.append({
                "id": md.stem,
                "name": meta.get("name") or md.stem,
                "content": body,
                "source": (meta.get("source") or "owner").strip().lower(),
                "created_by": meta.get("created_by") or "",
                "created_at": meta.get("created_at") or "",
                "updated_by": meta.get("updated_by") or "",
                "updated_at": meta.get("updated_at") or "",
                "origin_dialog": meta.get("origin_dialog") or "",
            })
        except Exception:  # noqa: BLE001
            import logging
            logging.exception("agent_knowledge: learned parse failed for %s", md)
    return out


def count_agent_self_learned(slug: str) -> int:
    return sum(1 for i in (load_agent_learned(slug) or []) if i["source"] == "self")


def save_agent_learned(slug: str, name: str, content: str, source: str,
                       actor: str, dialog: str | None = None) -> dict[str, Any]:
    """Create or update a personal instruction as a git file with attribution.
    Preserves created_by/created_at on update and stamps updated_by/updated_at."""
    source = "self" if str(source).strip().lower() == "self" else "owner"
    path = _learned_file(slug, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = _msk_now_iso()
    created_by, created_at, origin = actor, now, (dialog or "")
    if path.is_file():
        prev, _ = parse_doc(path.read_text(encoding="utf-8", errors="replace"))
        created_by = prev.get("created_by") or created_by
        created_at = prev.get("created_at") or created_at
        origin = prev.get("origin_dialog") or origin
    meta = {
        "name": name.strip(),
        "source": source,
        "created_by": created_by,
        "created_at": created_at,
        "updated_by": actor,
        "updated_at": now,
    }
    if origin:
        meta["origin_dialog"] = origin
    front = "---\n" + "".join(f"{k}: {v}\n" for k, v in meta.items()) + "---\n\n"
    path.write_text(front + content.strip() + "\n", encoding="utf-8")
    return {"id": path.stem, "name": meta["name"], "source": source}


PROMOTED_FOLDER = "Навыки агентов"


def promote_learned_to_library(slug: str, inst_id: str) -> dict[str, Any] | None:
    """Promote an agent's personal instruction into the SHARED library as an optional
    instruction, so the owner can connect it to any agent. Returns the new library path,
    or None if the source wasn't found. Idempotent by name."""
    learned = load_agent_learned(slug) or []
    src = next((i for i in learned if i["id"] == inst_id or i["name"] == inst_id), None)
    if not src:
        return None
    name = src["name"].strip()
    dest = INSTRUCTIONS_DIR / _safe_component(PROMOTED_FOLDER) / f"{_safe_component(name)}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "name": name,
        "scope": SCOPE_OPTIONAL,
        "promoted_from": f"{slug} · {src.get('created_by') or ''}".strip(" ·"),
        "promoted_at": _msk_now_iso(),
    }
    front = "---\n" + "".join(f"{kk}: {vv}\n" for kk, vv in meta.items()) + "---\n\n"
    dest.write_text(front + (src["content"] or "").strip() + "\n", encoding="utf-8")
    return {"path": f"{PROMOTED_FOLDER} / {name}", "name": name}


def delete_agent_learned(slug: str, name: str, only_self: bool = False) -> bool:
    """Delete a personal instruction by name (matched via its file slug). When
    ``only_self`` is set, owner-authored ones are protected (the agent can only remove
    what it taught itself)."""
    path = _learned_file(slug, name)
    if not path.is_file():
        # also try matching by exact name inside files (slug collisions / renames)
        for md in _learned_dir(slug).glob("*.md") if _learned_dir(slug).is_dir() else []:
            meta, _ = parse_doc(md.read_text(encoding="utf-8", errors="replace"))
            if (meta.get("name") or "").strip() == name.strip():
                path = md
                break
        else:
            return False
    if only_self:
        meta, _ = parse_doc(path.read_text(encoding="utf-8", errors="replace"))
        if (meta.get("source") or "owner").strip().lower() != "self":
            return False
    path.unlink()
    return True


# --- helpers ---------------------------------------------------------------------

def _safe_int(value: Any) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def registry_present() -> bool:
    return INSTRUCTIONS_DIR.is_dir() or SKILLS_DIR.is_dir() or HERMES_BASE_DIR.is_dir()
