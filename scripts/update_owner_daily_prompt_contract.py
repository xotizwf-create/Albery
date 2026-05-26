from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app  # noqa: E402


def upsert_active_owner_daily_prompt() -> int:
    prompt_text = app.OWNER_DAILY_PROMPT.rstrip() + app.OWNER_DAILY_STRICT_FORMAT_CONTRACT
    with app.pg_connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                app.ensure_prompt_categories(cur)
                cur.execute(
                    """
                    SELECT id
                    FROM ai_prompt_categories
                    WHERE category_key = 'owner_daily_report'
                    """,
                )
                category = cur.fetchone()
                if not category:
                    raise RuntimeError("Prompt category owner_daily_report was not created.")
                category_id = category["id"]
                cur.execute(
                    """
                    SELECT COALESCE(MAX(version), 0) + 1 AS version
                    FROM ai_prompts
                    WHERE category_id = %s
                    """,
                    (category_id,),
                )
                version = int(cur.fetchone()["version"])
                cur.execute(
                    """
                    UPDATE ai_prompts
                    SET is_active = FALSE
                    WHERE category_id = %s AND is_active = TRUE
                    """,
                    (category_id,),
                )
                cur.execute(
                    """
                    INSERT INTO ai_prompts (
                        category_id, prompt_key, title, prompt_text, version, is_active
                    )
                    VALUES (%s, 'owner_daily', 'Ежедневный отчет для собственника', %s, %s, TRUE)
                    RETURNING version
                    """,
                    (category_id, prompt_text, version),
                )
                return int(cur.fetchone()["version"])


def upsert_owner_daily_instruction() -> None:
    app.ensure_ai_instruction_schema()
    with app.pg_connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO ai_instruction_folders (parent_id, name, content, sort_order)
                    SELECT NULL, %s, '', %s
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM ai_instruction_folders
                        WHERE parent_id IS NULL AND lower(name) = lower(%s)
                    )
                    RETURNING id
                    """,
                    (
                        app.OWNER_REPORT_PIPELINE_INSTRUCTION_PARENT_NAME,
                        3,
                        app.OWNER_REPORT_PIPELINE_INSTRUCTION_PARENT_NAME,
                    ),
                )
                parent = cur.fetchone()
                if parent:
                    parent_id = parent["id"]
                else:
                    cur.execute(
                        """
                        SELECT id
                        FROM ai_instruction_folders
                        WHERE parent_id IS NULL AND lower(name) = lower(%s)
                        LIMIT 1
                        """,
                        (app.OWNER_REPORT_PIPELINE_INSTRUCTION_PARENT_NAME,),
                    )
                    parent_id = cur.fetchone()["id"]
                cur.execute(
                    """
                    INSERT INTO ai_instruction_folders (parent_id, name, content, sort_order)
                    SELECT %s, %s, %s, %s
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM ai_instruction_folders
                        WHERE parent_id = %s AND lower(name) = lower(%s)
                    )
                    RETURNING id
                    """,
                    (
                        parent_id,
                        app.OWNER_REPORT_PIPELINE_INSTRUCTION_NAME,
                        app.OWNER_REPORT_PIPELINE_INSTRUCTION_CONTENT,
                        10,
                        parent_id,
                        app.OWNER_REPORT_PIPELINE_INSTRUCTION_NAME,
                    ),
                )
                inserted = cur.fetchone()
                if not inserted:
                    cur.execute(
                        """
                        UPDATE ai_instruction_folders
                        SET content = %s, updated_at = now()
                        WHERE parent_id = %s AND lower(name) = lower(%s)
                        """,
                        (
                            app.OWNER_REPORT_PIPELINE_INSTRUCTION_CONTENT,
                            parent_id,
                            app.OWNER_REPORT_PIPELINE_INSTRUCTION_NAME,
                        ),
                    )


def main() -> None:
    if not app.postgres_enabled():
        raise RuntimeError("PostgreSQL is required to update owner daily prompt contract.")
    version = upsert_active_owner_daily_prompt()
    upsert_owner_daily_instruction()
    print(f"Updated owner_daily_report prompt to version {version} and refreshed MCP AI instruction.")


if __name__ == "__main__":
    main()
