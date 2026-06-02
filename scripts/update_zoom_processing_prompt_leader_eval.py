from __future__ import annotations

"""Install zoom_processing prompt v9 (leader evaluation + dispatch summary).

What v9 adds on top of the active prompt:
1. dispatch_summary — короткая нейтральная выжимка для рассылки участникам.
2. Участникам в JSON добавлены is_leader / role_on_call.
3. Новый раздел report_text "12. Оценка руководителей" + JSON leader_evaluations
   (role host/co_leader, verdict, result_for_owner, message_for_leader).
4. Операционные задачи: ответственный = только участник звонка; задача про
   отсутствующего переадресуется участнику-инициатору (delegate_to).

The full v9 contract text lives in the sibling file
``zoom_processing_prompt_v9.md`` so the whole prompt is reviewable as one artifact.

Pattern mirrors scripts/update_zoom_processing_prompt.py: deactivate the current
active prompt and insert a new version, keeping full history.

Usage:
    python scripts/update_zoom_processing_prompt_leader_eval.py            # apply
    python scripts/update_zoom_processing_prompt_leader_eval.py --dry-run  # no DB write
"""

import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row


# Distinctive markers present only in v9 — guard against double-insert.
MARKERS = ("Оценка руководителей", "dispatch_summary", "leader_evaluations")

PROMPT_FILE = Path(__file__).with_name("zoom_processing_prompt_v9.md")


def normalize_postgres_url(database_url: str) -> str:
    normalized = database_url.strip()
    for prefix in ("postgresql+psycopg2://", "postgresql+psycopg://"):
        if normalized.startswith(prefix):
            return "postgresql://" + normalized[len(prefix):]
    return normalized


def load_new_prompt_text() -> str:
    text = PROMPT_FILE.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError(f"{PROMPT_FILE.name} is empty")
    missing = [m for m in MARKERS if m not in text]
    if missing:
        raise RuntimeError(f"v9 prompt file missing required markers: {missing}")
    return text + "\n"


def main() -> int:
    dry_run = "--dry-run" in sys.argv[1:]
    new_text = load_new_prompt_text()

    load_dotenv("/var/www/albery/.env")
    database_url = normalize_postgres_url(
        os.getenv("DATABASE_ADMIN_URL", "").strip() or os.getenv("DATABASE_URL", "").strip()
    )
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set")

    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT c.id AS category_id, p.*
                    FROM ai_prompt_categories c
                    JOIN ai_prompts p ON p.category_id = c.id
                    WHERE c.category_key = %s
                      AND c.is_active = TRUE
                      AND p.is_active = TRUE
                    ORDER BY p.version DESC, p.created_at DESC
                    LIMIT 1
                    """,
                    ("zoom_processing",),
                )
                active = cur.fetchone()
                if not active:
                    print("No active zoom_processing prompt found; nothing changed.")
                    return 1
                current_text = str(active["prompt_text"] or "")
                if all(m in current_text for m in MARKERS):
                    print("Active zoom_processing prompt already at v9 (markers present); nothing changed.")
                    return 0
                new_version = int(active["version"] or 1) + 1
                if dry_run:
                    print(
                        f"[dry-run] would deactivate version {active['version']} (id {active['id']}) and "
                        f"insert version {new_version} (len {len(current_text)} -> {len(new_text)})."
                    )
                    conn.rollback()
                    return 0
                cur.execute("UPDATE ai_prompts SET is_active = FALSE WHERE id = %s", (active["id"],))
                cur.execute(
                    """
                    INSERT INTO ai_prompts (category_id, prompt_key, title, prompt_text, version, is_active, created_by_user_id)
                    VALUES (%s, %s, %s, %s, %s, TRUE, NULL)
                    RETURNING id, version
                    """,
                    (
                        active["category_id"],
                        active["prompt_key"] or "zoom_processing",
                        active["title"] or "Обработка Зумов",
                        new_text,
                        new_version,
                    ),
                )
                inserted = cur.fetchone()
                print(
                    f"Installed zoom_processing v9: id {inserted['id']} version {inserted['version']} "
                    f"(prev version {active['version']}, len {len(current_text)} -> {len(new_text)})"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
