"""Chunked company-knowledge index for RAG-style retrieval (stage A: lexical over chunks).

Documents in ``company_folders`` are split into ~400-token passages stored in
``company_knowledge_chunks`` so that ``search_company_knowledge`` can return a few
relevant PASSAGES instead of whole documents (which average ~24 KB and were burning
the model's context / Codex limit).

The index is **self-maintaining**: ``ensure_fresh`` compares a cheap corpus signature
(count + max(updated_at) + total length of company_folders) against the stored one and,
only when it changed, re-chunks the documents whose content hash changed. A
transaction-scoped advisory lock makes concurrent searches safe — at most one rebuilds,
the others read the existing chunks.

Stage B (later) will add an ``embedding`` column and fuse vector similarity into the
ranking for semantic recall; nothing here needs to change structurally for that.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

# ~400 tokens for Russian text ≈ 1400 chars; keep a hard cap so a single huge paragraph
# still gets split, and a small overlap so context isn't lost at chunk boundaries.
CHUNK_TARGET_CHARS = 1400
CHUNK_OVERLAP_CHARS = 200
CHUNK_HARD_MAX_CHARS = 2200

# Arbitrary constant key for the transaction advisory lock guarding a rebuild.
_ADVISORY_LOCK_KEY = 815234071
_SIGNATURE_KEY = "chunk_signature"

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")

# Folders whose name starts with these are sync artifacts (an interrupted Drive sync
# leaves "__sync_tmp__<doc>" orphans that duplicate the real document). They are excluded
# from the chunk index so search never returns a document twice.
_EXCLUDE_NAME_PREFIXES = ("__sync_tmp__",)

_BR_TAG = re.compile(r"<br\s*/?>", re.IGNORECASE)
_EMPTY_CELLS = re.compile(r"(?:\|[ \t]*){2,}")  # runs of empty table cells: "|  |  |  |"


def _is_excluded(name: str) -> bool:
    return (name or "").startswith(_EXCLUDE_NAME_PREFIXES)


def _normalize_for_index(text: str) -> str:
    """Light denoising of Drive-mirrored sheet text before chunking.

    Flattened Google Sheets carry empty-cell noise ("|  |  |  |"), <br> tags and a few
    "∅" placeholders. Collapsing them keeps real content and trims wasted tokens without
    touching the source document (this runs only on the search index)."""
    if not text:
        return text
    text = _BR_TAG.sub("\n", text)
    text = text.replace("∅", "")
    # Drop auto-generated empty column headers ("Колонка 5:") — real columns are named
    # ("Встречи:", "Участники:"), never "Колонка N". Then collapse the empty cells left behind.
    text = re.sub(r"Колонка \d+:\s*", "", text)
    text = _EMPTY_CELLS.sub("| ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text


def _hard_split(text: str, target: int, overlap: int) -> list[str]:
    out: list[str] = []
    i = 0
    n = len(text)
    step = max(1, target - overlap)
    while i < n:
        out.append(text[i : i + target].strip())
        if i + target >= n:
            break
        i += step
    return [c for c in out if c]


def chunk_text(text: str, target: int = CHUNK_TARGET_CHARS,
               overlap: int = CHUNK_OVERLAP_CHARS,
               hard_max: int = CHUNK_HARD_MAX_CHARS) -> list[str]:
    """Split text into ~``target``-char chunks on paragraph boundaries, with overlap.

    Oversized paragraphs (e.g. big tables) are hard-split. Returns [] for empty text.
    """
    text = (text or "").strip()
    if not text:
        return []
    chunks: list[str] = []
    buf = ""
    for para in _PARAGRAPH_SPLIT.split(text):
        para = para.strip()
        if not para:
            continue
        if len(para) > hard_max:
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.extend(_hard_split(para, target, overlap))
            continue
        if buf and len(buf) + 1 + len(para) > target:
            chunks.append(buf)
            tail = buf[-overlap:] if overlap else ""
            buf = (tail + "\n" + para).strip() if tail else para
        else:
            buf = (buf + "\n" + para) if buf else para
    if buf:
        chunks.append(buf)
    return [c for c in chunks if c.strip()]


def _content_hash(content: str) -> str:
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


def _folder_paths(cur: Any) -> dict[Any, str]:
    cur.execute(
        """
        WITH RECURSIVE ft AS (
            SELECT id, parent_id, ARRAY[name]::text[] AS path
            FROM company_folders WHERE parent_id IS NULL
            UNION ALL
            SELECT c.id, c.parent_id, ft.path || c.name
            FROM company_folders c JOIN ft ON ft.id = c.parent_id
        )
        SELECT id, array_to_string(path, ' / ') AS path FROM ft
        """
    )
    return {row["id"]: row["path"] for row in cur.fetchall()}


def _corpus_signature(cur: Any) -> str:
    cur.execute(
        """
        SELECT count(*) AS n,
               coalesce(max(updated_at)::text, '') AS mx,
               coalesce(sum(length(coalesce(content, ''))), 0) AS s
        FROM company_folders
        """
    )
    row = cur.fetchone()
    return f"{row['n']}:{row['mx']}:{row['s']}"


def rebuild(conn: Any, force: bool = False) -> dict[str, Any]:
    """(Re)build chunks for documents whose content changed. Idempotent.

    Operates on the passed connection within its current transaction; the caller's
    ``with connect() as conn`` commits on block exit. Safe to call on every search.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_xact_lock(%s) AS got", (_ADVISORY_LOCK_KEY,))
        if not cur.fetchone()["got"]:
            return {"status": "locked"}

        signature = _corpus_signature(cur)
        if not force:
            cur.execute("SELECT value FROM company_knowledge_meta WHERE key = %s", (_SIGNATURE_KEY,))
            row = cur.fetchone()
            if row and row["value"] == signature:
                return {"status": "fresh"}

        cur.execute("SELECT id, name, coalesce(content, '') AS content FROM company_folders")
        folders = cur.fetchall()
        cur.execute("SELECT folder_id, content_hash FROM company_knowledge_chunk_state")
        state = {row["folder_id"]: row["content_hash"] for row in cur.fetchall()}
        paths = _folder_paths(cur)

        built = skipped = excluded = 0
        for f in folders:
            fid = f["id"]
            content = f["content"] or ""
            h = _content_hash(content)
            if not force and state.get(fid) == h:
                skipped += 1
                continue
            # Sync artifacts (__sync_tmp__*) duplicate a real doc — index nothing for them,
            # but still record state so incremental runs skip them by hash next time.
            if _is_excluded(f["name"]):
                pieces: list[str] = []
                excluded += 1
            else:
                pieces = chunk_text(_normalize_for_index(content))
                built += 1
            cur.execute("DELETE FROM company_knowledge_chunks WHERE folder_id = %s", (fid,))
            for idx, piece in enumerate(pieces):
                cur.execute(
                    """
                    INSERT INTO company_knowledge_chunks (folder_id, chunk_index, name, path, content)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (fid, idx, f["name"], paths.get(fid), piece),
                )
            cur.execute(
                """
                INSERT INTO company_knowledge_chunk_state (folder_id, content_hash, chunk_count, updated_at)
                VALUES (%s, %s, %s, now())
                ON CONFLICT (folder_id)
                DO UPDATE SET content_hash = EXCLUDED.content_hash,
                              chunk_count = EXCLUDED.chunk_count,
                              updated_at = now()
                """,
                (fid, h, len(pieces)),
            )
            built += 1

        cur.execute(
            """
            INSERT INTO company_knowledge_meta (key, value) VALUES (%s, %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """,
            (_SIGNATURE_KEY, signature),
        )
        return {"status": "rebuilt", "built": built, "skipped": skipped,
                "excluded": excluded, "folders": len(folders)}


def ensure_fresh(conn: Any) -> dict[str, Any]:
    """Cheap freshness check + targeted re-chunk. Never raises into the caller's search."""
    try:
        return rebuild(conn, force=False)
    except Exception as exc:  # pragma: no cover - search must survive a rebuild hiccup
        return {"status": "error", "error": str(exc)}


def _broaden_fts(query: str, min_word_len: int = 3) -> str | None:
    """Turn a multi-word query into an OR-of-words FTS string for a wider recall pass.

    websearch_to_tsquery treats spaces as AND, so "график зум созвонов" needs ALL three
    stems and misses a doc that only contains "Зум". Joining with OR lets the strongest
    word still match. Returns None for single-word queries (nothing to broaden)."""
    words = [w for w in re.findall(r"\w+", query, flags=re.UNICODE) if len(w) >= min_word_len]
    if len(words) < 2:
        return None
    return " OR ".join(dict.fromkeys(words))  # de-dupe, keep order


def search_chunks(conn: Any, query: str, limit: int = 6, offset: int = 0,
                  per_doc: int = 2, name_sim_threshold: float = 0.3,
                  fts_text: str | None = None) -> list[dict[str, Any]]:
    """Hybrid lexical retrieval over chunks: Russian FTS + pg_trgm + ILIKE.

    Returns at most ``per_doc`` chunks per document (diversity), ranked by a fused score.
    ``fts_text`` overrides the full-text query expression (used for the OR-broadened pass);
    ILIKE/similarity still use the raw ``query``.
    """
    like = f"%{query}%"
    params = {
        "q": query,
        "ftsq": fts_text if fts_text is not None else query,
        "like": like,
        "sim": name_sim_threshold,
        "per_doc": per_doc,
        "limit": limit,
        "offset": offset,
    }
    sql = """
        WITH scored AS (
            SELECT
                c.folder_id,
                c.chunk_index,
                c.name,
                c.path,
                c.content,
                (
                    ts_rank_cd(c.content_tsv, websearch_to_tsquery('russian', %(ftsq)s))
                    + 0.4 * similarity(c.content, %(q)s)
                    + 0.5 * similarity(c.name, %(q)s)
                    + CASE WHEN c.name ILIKE %(like)s THEN 0.3 ELSE 0 END
                ) AS score
            FROM company_knowledge_chunks c
            WHERE
                c.content_tsv @@ websearch_to_tsquery('russian', %(ftsq)s)
                OR c.name ILIKE %(like)s
                OR c.content ILIKE %(like)s
                OR similarity(c.name, %(q)s) >= %(sim)s
        ),
        ranked AS (
            SELECT *,
                   row_number() OVER (PARTITION BY folder_id ORDER BY score DESC, chunk_index) AS rn
            FROM scored
        )
        SELECT folder_id, chunk_index, name, path, content, score
        FROM ranked
        WHERE rn <= %(per_doc)s
        ORDER BY score DESC, name, chunk_index
        LIMIT %(limit)s OFFSET %(offset)s
    """
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def search_expanded(conn: Any, query: str, limit: int = 6, offset: int = 0,
                    per_doc: int = 2) -> tuple[list[dict[str, Any]], str]:
    """Query-expansion wrapper: strict phrase search first, then an OR-of-words pass.

    Returns (rows, mode) where mode is 'strict' | 'broad' | 'empty'. Lets the lexical
    layer recover recall for phrasings like 'график зум созвонов' (0 strict hits) by
    matching the single discriminating word ('зум'); true synonym gaps are still handed
    back to the agent (mode='empty') to rephrase.
    """
    rows = search_chunks(conn, query, limit=limit, offset=offset, per_doc=per_doc)
    if rows:
        return rows, "strict"
    broad = _broaden_fts(query)
    if broad:
        rows = search_chunks(conn, query, limit=limit, offset=offset, per_doc=per_doc, fts_text=broad)
        if rows:
            return rows, "broad"
    return [], "empty"
