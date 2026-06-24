from __future__ import annotations
"""Zoom-processing contract v14: replace the broken 'Сверка задач с матрицей решений' tail
(which forced duplicate tasks + injected text into task_text and broke the 1-14 report structure)
with a clean block: (1) 'Артефакт результата задачи' — every task names a confirmation artifact
(screenshot/link/file/comment/photo) + JSON expected_artifact; (2) 'Функционал и зона
ответственности' — Step 1 filters a person's standing function (no one-off trigger) out of tasks
into routine_functions_noted, keeps one-off tasks as control points; Step 2 turns a matrix/process
mismatch into an advisory highlight (responsibility_check.recommendation) — NO duplicate tasks, NO
task_text edits. Idempotent; new version kept in history. Block text: sibling
zoom_processing_v14_responsibility_block.md."""
import os
import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

HERE = os.path.dirname(os.path.abspath(__file__))
BLOCK_FILE = os.path.join(HERE, "zoom_processing_v14_responsibility_block.md")
ANCHOR = "## Сверка задач с матрицей решений и картой процессов"
MARKER = "## Функционал и зона ответственности"


def norm(url: str) -> str:
    url = url.strip()
    for p in ("postgresql+psycopg2://", "postgresql+psycopg://"):
        if url.startswith(p):
            return "postgresql://" + url[len(p):]
    return url


def main() -> int:
    load_dotenv("/var/www/albery/.env")
    dsn = norm(os.getenv("DATABASE_ADMIN_URL", "").strip() or os.getenv("DATABASE_URL", "").strip())
    block = open(BLOCK_FILE, encoding="utf-8").read().strip()
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT c.id AS category_id, p.* FROM ai_prompt_categories c
                       JOIN ai_prompts p ON p.category_id=c.id
                       WHERE c.category_key=%s AND c.is_active=TRUE AND p.is_active=TRUE
                       ORDER BY p.version DESC, p.created_at DESC LIMIT 1""",
                    ("zoom_processing",),
                )
                active = cur.fetchone()
                if not active:
                    print("no active prompt"); return 1
                text = str(active["prompt_text"] or "")
                if MARKER in text and ANCHOR not in text:
                    print("already applied (v14 block present)"); return 0
                if text.count(ANCHOR) != 1:
                    print("ABORT: anchor count =", text.count(ANCHOR)); return 2
                ia = text.find(ANCHOR)
                new_text = text[:ia].rstrip() + "\n\n" + block + "\n"
                cur.execute("UPDATE ai_prompts SET is_active=FALSE WHERE id=%s", (active["id"],))
                cur.execute(
                    """INSERT INTO ai_prompts (category_id, prompt_key, title, prompt_text, version, is_active, created_by_user_id)
                       VALUES (%s,%s,%s,%s,%s,TRUE,NULL) RETURNING id, version""",
                    (active["category_id"], active["prompt_key"] or "zoom_processing",
                     active["title"] or "Обработка Зумов", new_text, int(active["version"] or 1) + 1),
                )
                ins = cur.fetchone()
                print(f"v14 applied -> id {ins['id']} version {ins['version']} (len {len(text)} -> {len(new_text)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
