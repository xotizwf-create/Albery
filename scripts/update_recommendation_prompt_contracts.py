from __future__ import annotations

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row


ROOT = Path(__file__).resolve().parents[1]
MARKER = "Адресные рекомендации и обратная связь"

CHAT_ANALYSIS_APPENDIX = """

## Адресные рекомендации и обратная связь

Во входных данных может быть блок `Адресные рекомендации и обратная связь`.
Он содержит активные рекомендации, факты отправки, предыдущие события и текущие статусы.

Обязательные правила:
1. Анализируй реакцию на рекомендации по переписке предыдущего и текущего дня.
2. Сопоставляй ответ с конкретным `recommendation_id`, а не только с человеком.
3. Если человек ответил понятно, верни объект в `recommendation_feedback`.
4. Если ответ не дает ясного статуса, верни объект в `strange_feedback` с `label = "Странная обратная связь"` и `requires_manager_review = true`.
5. Не ставь `done`, если нет явного факта выполнения результата. "Ок", "принял", "посмотрю", "сделаю", "занимаюсь" не являются выполнением.
6. Для неясных ответов используй `needs_clarification` или `requires_manager_review`.

Дополнительные поля верхнего уровня в JSON:

`recommendation_feedback`:
[
  {
    "recommendation_id": "uuid рекомендации",
    "person_name": "кто отреагировал",
    "understood_status": "accepted|in_progress|needs_clarification|disagreed|delegated|done|no_response|overdue|requires_manager_review",
    "summary": "что человек ответил и как это связано с рекомендацией",
    "commitment": "обязательство или null",
    "deadline": "YYYY-MM-DD или null",
    "next_action": "что нужно сделать дальше",
    "requires_manager_review": false,
    "confidence": "direct|inferred|weak",
    "evidence_message_ids": [123],
    "source_type": "chat|ocr|mixed"
  }
]

`strange_feedback`:
[
  {
    "recommendation_id": "uuid рекомендации",
    "label": "Странная обратная связь",
    "person_name": "кто ответил",
    "reason": "почему ответ неясный, странный или требует управленческой проверки",
    "raw_answer_summary": "короткая суть ответа",
    "recommended_status": "needs_clarification|requires_manager_review|disagreed|delegated",
    "requires_manager_review": true,
    "evidence_message_ids": [123],
    "source_type": "chat|ocr|mixed"
  }
]
"""


def normalize_postgres_url(database_url: str) -> str:
    normalized = database_url.strip()
    for prefix in ("postgresql+psycopg2://", "postgresql+psycopg://"):
        if normalized.startswith(prefix):
            return "postgresql://" + normalized[len(prefix):]
    return normalized


def main() -> int:
    load_dotenv(ROOT / ".env")
    database_url = normalize_postgres_url(os.getenv("DATABASE_ADMIN_URL", "").strip() or os.getenv("DATABASE_URL", "").strip())
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
                    WHERE c.category_key = 'chat_analysis'
                      AND c.is_active = TRUE
                      AND p.is_active = TRUE
                    ORDER BY p.version DESC, p.created_at DESC
                    LIMIT 1
                    """
                )
                active = cur.fetchone()
                if not active:
                    print("No active chat_analysis prompt found; nothing changed.")
                    return 0
                prompt_text = str(active["prompt_text"] or "")
                if MARKER in prompt_text:
                    print("Active chat_analysis prompt already contains recommendation feedback rules.")
                    return 0
                new_text = prompt_text.rstrip() + CHAT_ANALYSIS_APPENDIX
                cur.execute("UPDATE ai_prompts SET is_active = FALSE WHERE id = %s", (active["id"],))
                cur.execute(
                    """
                    INSERT INTO ai_prompts (category_id, prompt_key, title, prompt_text, version, is_active, created_by_user_id)
                    VALUES (%s, %s, %s, %s, %s, TRUE, NULL)
                    RETURNING id, version
                    """,
                    (
                        active["category_id"],
                        active["prompt_key"] or "chat_analysis",
                        active["title"] or "Chat analysis",
                        new_text,
                        int(active["version"] or 1) + 1,
                    ),
                )
                inserted = cur.fetchone()
                print(f"Updated chat_analysis prompt: {inserted['id']} version {inserted['version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
