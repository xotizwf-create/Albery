from __future__ import annotations

"""Second update to the active zoom_processing prompt: add a Friday-only
weekly-review block for Наталья and Артур (facts done, unfinished by their own
words, next-week goals with KPI weights). Applies ONLY when the call is the
Friday weekly control meeting.

Deactivate the current active prompt and insert a new version (full history).
"""

import os

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row


MARKER = "Итоги недели для Натальи и Артура"

# 1) Conditional 13th section note in the "Структура строго" list.
OLD_STRUCT = "12. Что контролировать на следующем созвоне\n\nОформление report_text:"
NEW_STRUCT = (
    "12. Что контролировать на следующем созвоне\n"
    "13. Итоги недели: Наталья и Артур — добавляется ТОЛЬКО если созвон определён как контрольная встреча "
    "итогов недели (пятница); в остальных отчётах этого раздела нет.\n\nОформление report_text:"
)

# 2) weekly_review object in the returned JSON schema (inserted before "notes").
OLD_JSON = "  \"notes\": []\n}"
NEW_JSON = (
    "  \"weekly_review\": {\n"
    "    \"applies\": false,\n"
    "    \"people\": [\n"
    "      {\n"
    "        \"person_name\": \"\",\n"
    "        \"done_facts\": [{\"text\": \"\", \"timecode\": \"\", \"evidence\": \"\"}],\n"
    "        \"unfinished\": [{\"text\": \"\", \"reason\": \"\", \"timecode\": \"\"}],\n"
    "        \"next_week_goals\": [{\"goal\": \"\", \"kpi_weight\": \"\", \"timecode\": \"\"}]\n"
    "      }\n"
    "    ]\n"
    "  },\n"
    "  \"notes\": []\n}"
)

# 3) Full Friday-only instruction section, inserted before the operational-tasks appendix.
ANCHOR_APPENDIX = "## Строгий контракт операционных задач Zoom\n\nЭтот блок обязателен для каждого отчета"
FRIDAY_SECTION = (
    "## Итоги недели для Натальи и Артура (только пятничная контрольная встреча)\n\n"
    "Этот блок применяется ТОЛЬКО если в блоке \"Соответствие регламенту\" созвон определён как контрольная "
    "встреча итогов недели (пятница). Во всех остальных созвонах этого блока в отчёте нет, "
    "а в JSON weekly_review.applies = false.\n\n"
    "Если созвон — пятничная контрольная встреча, добавь в report_text раздел "
    "\"13. Итоги недели: Наталья и Артур\" и заполни его отдельно по каждому из двух человек — "
    "Наталья Горюнова и Артур Степанян (сопоставь их с org_context.users):\n\n"
    "- Сделано за неделю (факт): только реально выполненное, как это прозвучало на встрече "
    "(\"сделал\", \"отправил\", \"закрыл\", \"запустил\", \"собрал\"), с конкретикой и таймкодом. "
    "Не записывай планы, намерения и обсуждения как факт. Если человек о фактах недели не отчитался — "
    "напиши \"по фактам недели не отчитался\".\n"
    "- Не завершено (по его словам): что сам человек назвал незавершённым, перенесённым или заблокированным, "
    "с причиной и таймкодом. Не додумывай за него.\n"
    "- Цели на следующую неделю с весом в KPI: по каждой цели укажи вес/долю в KPI, ЕСЛИ она прозвучала на "
    "встрече. Если вес не назван — напиши \"доля в KPI не выделена\". Цель без озвученного веса не выбрасывай.\n\n"
    "Если человек на встрече не присутствовал или данных по нему нет — так и напиши, не выдумывай факты, "
    "незавершённое, цели и веса.\n\n"
    "В JSON заполни объект weekly_review:\n"
    "- applies = true только для пятничной контрольной встречи, иначе false;\n"
    "- people: по объекту на Наталью и Артура с массивами done_facts, unfinished, next_week_goals;\n"
    "- в next_week_goals поле kpi_weight = озвученный вес или строка \"доля в KPI не выделена\".\n"
    "Если созвон не пятничная контрольная встреча — верни weekly_review = {\"applies\": false, \"people\": []}.\n\n"
)
NEW_APPENDIX = FRIDAY_SECTION + ANCHOR_APPENDIX


def normalize_postgres_url(database_url: str) -> str:
    normalized = database_url.strip()
    for prefix in ("postgresql+psycopg2://", "postgresql+psycopg://"):
        if normalized.startswith(prefix):
            return "postgresql://" + normalized[len(prefix):]
    return normalized


def apply_edits(text: str) -> str:
    for anchor, label in ((OLD_STRUCT, "OLD_STRUCT"), (OLD_JSON, "OLD_JSON"), (ANCHOR_APPENDIX, "ANCHOR_APPENDIX")):
        if anchor not in text:
            raise RuntimeError(f"{label} anchor not found in active prompt")
    text = text.replace(OLD_STRUCT, NEW_STRUCT, 1)
    text = text.replace(OLD_JSON, NEW_JSON, 1)
    text = text.replace(ANCHOR_APPENDIX, NEW_APPENDIX, 1)
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
                    print("Active zoom_processing prompt already has the Friday block; nothing changed.")
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
