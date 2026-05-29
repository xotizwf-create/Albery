from __future__ import annotations

"""Third update to the active zoom_processing prompt: in the Friday-only weekly
review (section 13), require EXHAUSTIVE extraction — list ALL facts and ALL
next-week goals as separate numbered items, never summarize or drop any.

Deactivate the current active prompt and insert a new version (full history).
"""

import os

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row


MARKER = "Требование полноты"

OLD_FACTS = "- Сделано за неделю (факт): только реально выполненное, как это прозвучало на встрече"
NEW_FACTS = (
    "Требование полноты: перечисли ВСЕ факты и ВСЕ цели до единого, каждый отдельным пронумерованным пунктом; "
    "ничего не объединяй, не сворачивай в общую формулировку и не пропускай. Лучше отдельный пункт, чем потерянный факт.\n"
    "- Сделано за неделю (факт): перечисли ВСЕ выполненные за неделю задачи, по одной в пункте — только реально "
    "выполненное, как это прозвучало на встрече"
)

OLD_GOALS = "- Цели на следующую неделю с весом в KPI: по каждой цели укажи вес/долю в KPI, ЕСЛИ она прозвучала на встрече."
NEW_GOALS = (
    "- Цели на следующую неделю с весом в KPI: перечисли ВСЕ цели на следующую неделю, каждую отдельным пунктом; "
    "по каждой укажи вес/долю в KPI, ЕСЛИ она прозвучала на встрече."
)


def normalize_postgres_url(database_url: str) -> str:
    normalized = database_url.strip()
    for prefix in ("postgresql+psycopg2://", "postgresql+psycopg://"):
        if normalized.startswith(prefix):
            return "postgresql://" + normalized[len(prefix):]
    return normalized


def apply_edits(text: str) -> str:
    for anchor, label in ((OLD_FACTS, "OLD_FACTS"), (OLD_GOALS, "OLD_GOALS")):
        if anchor not in text:
            raise RuntimeError(f"{label} anchor not found in active prompt")
    text = text.replace(OLD_FACTS, NEW_FACTS, 1)
    text = text.replace(OLD_GOALS, NEW_GOALS, 1)
    return text


def main() -> int:
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
                prompt_text = str(active["prompt_text"] or "")
                if MARKER in prompt_text:
                    print("Active zoom_processing prompt already enforces completeness; nothing changed.")
                    return 0
                new_text = apply_edits(prompt_text)
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
                        int(active["version"] or 1) + 1,
                    ),
                )
                inserted = cur.fetchone()
                print(
                    f"Updated zoom_processing prompt: {inserted['id']} version {inserted['version']} "
                    f"(prev version {active['version']}, len {len(prompt_text)} -> {len(new_text)})"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
