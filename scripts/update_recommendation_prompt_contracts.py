from __future__ import annotations

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row


ROOT = Path(__file__).resolve().parents[1]
MARKER = "Адресные рекомендации и обратная связь"
ZOOM_MARKER = "Строгий контракт операционных задач Zoom"

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


ZOOM_PROCESSING_APPENDIX = """

## Строгий контракт операционных задач Zoom

Этот блок обязателен для каждого отчета `zoom_processing`.

Перед выделением задач используй входной `org_context.users` как источник реальных сотрудников Bitrix: ФИО, `bitrix_user_id`, должность, отделы и руководитель.

Раздел `4. Операционные задачи` обязателен в `report_text`. Каждая задача должна быть отдельной строкой строго в формате:

`N. Ответственный: <полное ФИО из org_context.users или точное имя из разговора>. Задача: <короткое человеческое поручение>. Срок: <ДД.ММ.ГГГГ или срок не указан>. Критерий результата: <проверяемый результат>. Статус: planned. Источник: <таймкод или фрагмент>.`

Правила сопоставления:
1. Ответственный должен быть реальным сотрудником из `org_context.users`, если совпадение можно определить однозначно.
2. Короткие имена и инициалы (`Настя`, `Артур С.`, `Дима`) сначала сопоставляй с оргструктурой. В JSON возвращай полное ФИО и `bitrix_user_id`, если совпадение однозначное.
3. Не теряй задачи для людей, которые упоминались в разговоре, но не были фактическими участниками Zoom.
4. Нельзя назначать задачу роли или группе без пользователя: `координатор`, `ответственный`, `команда`, `админ блок`, `юристы`, `фабрика`.
5. Если ответственный не найден или совпадение неоднозначно, оставь имя из разговора и верни `bitrix_user_id = null`, но задачу не удаляй.

Правила текста:
1. В `task_text` не пиши технические слова и поля: `Ответственный`, `Задача`, `Срок`, `Статус`, `Источник`, `операционная задача`, `рекомендация`, `ИИ`, `система`.
2. `task_text` должен быть обычным поручением.
3. `result_criteria` должен описывать, как понять, что задача выполнена.
4. Если срок не прозвучал, используй `deadline_text = "срок не указан"`.
5. Если задача должна быть сделана сегодня, используй дату созвона в формате `ДД.ММ.ГГГГ`.

В JSON обязательно верни массив `operational_tasks` в том же порядке и с теми же задачами, что в разделе 4:

```json
{
  "operational_tasks": [
    {
      "number": 1,
      "assignee_name": "Полное ФИО из org_context.users",
      "bitrix_user_id": 123,
      "task_text": "Проверить акции и отправить выводы на согласование",
      "deadline_text": "25.05.2026",
      "result_criteria": "Наталье отправлены выводы/скриншоты по акциям и скидкам на согласование",
      "status": "planned",
      "source": "00:05:15-00:05:34"
    }
  ]
}
```

Каждая задача должна иметь `assignee_name`, `bitrix_user_id`, `task_text`, `deadline_text`, `result_criteria`, `status`, `source`.
"""


def normalize_postgres_url(database_url: str) -> str:
    normalized = database_url.strip()
    for prefix in ("postgresql+psycopg2://", "postgresql+psycopg://"):
        if normalized.startswith(prefix):
            return "postgresql://" + normalized[len(prefix):]
    return normalized


def ensure_prompt_appendix(cur, category_key: str, marker: str, appendix: str, fallback_title: str) -> None:
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
        (category_key,),
    )
    active = cur.fetchone()
    if not active:
        print(f"No active {category_key} prompt found; nothing changed.")
        return
    prompt_text = str(active["prompt_text"] or "")
    if marker in prompt_text:
        print(f"Active {category_key} prompt already contains required rules.")
        return
    new_text = prompt_text.rstrip() + appendix
    cur.execute("UPDATE ai_prompts SET is_active = FALSE WHERE id = %s", (active["id"],))
    cur.execute(
        """
        INSERT INTO ai_prompts (category_id, prompt_key, title, prompt_text, version, is_active, created_by_user_id)
        VALUES (%s, %s, %s, %s, %s, TRUE, NULL)
        RETURNING id, version
        """,
        (
            active["category_id"],
            active["prompt_key"] or category_key,
            active["title"] or fallback_title,
            new_text,
            int(active["version"] or 1) + 1,
        ),
    )
    inserted = cur.fetchone()
    print(f"Updated {category_key} prompt: {inserted['id']} version {inserted['version']}")


def main() -> int:
    load_dotenv(ROOT / ".env")
    database_url = normalize_postgres_url(os.getenv("DATABASE_ADMIN_URL", "").strip() or os.getenv("DATABASE_URL", "").strip())
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set")

    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                ensure_prompt_appendix(cur, "chat_analysis", MARKER, CHAT_ANALYSIS_APPENDIX, "Chat analysis")
                ensure_prompt_appendix(cur, "zoom_processing", ZOOM_MARKER, ZOOM_PROCESSING_APPENDIX, "Обработка Зумов")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
