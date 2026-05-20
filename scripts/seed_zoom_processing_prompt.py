from __future__ import annotations

import os
from pathlib import Path

import psycopg


ROOT = Path(__file__).resolve().parents[1]


def load_database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        env_path = ROOT / ".env"
        for raw_line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, raw_value = line.split("=", 1)
            if key.strip() == "DATABASE_URL":
                value = raw_value.strip().strip('"').strip("'")
                break
    for prefix in ("postgresql+psycopg2://", "postgresql+psycopg://"):
        if value.startswith(prefix):
            value = "postgresql://" + value[len(prefix):]
    if not value:
        raise RuntimeError("DATABASE_URL is not set")
    return value


PROMPT_TEXT = """Ты выполняешь управленческую обработку расшифровки Zoom-созвона.

Цель: превратить сырой транскрипт встречи в точный управленческий отчет и структурированный JSON, пригодный для контроля задач, ежедневных отчетов и последующей проверки.

Главный принцип: отчет должен давать конкретику. Не пересказывай разговор. Извлекай участников, упомянутых людей, факты, решения, задачи, владельцев, сроки, риски, открытые вопросы и контрольные точки. Если в задаче нет ответственного или срока, это не повод выбрасывать задачу: явно покажи пробел.

Входные данные
Ожидай JSON со следующими полями, если они доступны:
- call_date: дата созвона в YYYY-MM-DD.
- call_id / zoom_uuid: идентификатор созвона.
- topic / technical_topic: название созвона.
- participants: фактические участники из Zoom API или из разобранной расшифровки.
- transcript_segments: сегменты с временем, спикером и текстом.
- full_text: полный текст, если сегменты недоступны.
- org_context: сотрудники, роли, отделы, руководители и Bitrix user_id из оргструктуры.
- related_chats / related_tasks / previous_context: дополнительный контекст, если передан.

Обязательная идентификация людей
1. В начале report_text после источника всегда дай компактный блок "Участники и упомянутые люди".
2. В человекочитаемом report_text не пиши служебные поля JSON: raw_name, org_match, matched, ambiguous, not_found, bitrix_user_id. Эти поля должны быть только в JSON.
3. Формат видимого блока:
   - Участники созвона: Евгений Палей - генеральный директор; Координатор - не сопоставлен с оргструктурой, требуется уточнение.
   - Упоминались: Анастасия Андрусяк - бухгалтер; Денис Кузьменко - собственник; Елена - требуется уточнение; Виктория - не найдена в оргструктуре.
4. Для людей с однозначным совпадением пиши ФИО и короткую роль/отдел. Подробные отделы, руководителей, raw_name и evidence держи в JSON.
5. Если список упомянутых людей длинный, сгруппируй в 2 строки: "Сопоставлены" и "Требуют уточнения". Не делай по каждому человеку большой абзац.
6. Не смешивай фактических участников и упомянутых людей.
7. Не придумывай фамилию по одному имени. Если сказали "Елена", а в org_context нет однозначного совпадения, пиши "Елена - требуется уточнение".
8. Для каждого task/result/risk/question/decision, где есть человек, добавляй org_match: matched|ambiguous|not_found и org_person, если найден.

Правила дат и времени
- Все относительные даты нормализуй относительно call_date: сегодня = call_date, завтра = следующий календарный день, "на следующей неделе" = период следующей календарной недели с понедельника по воскресенье.
- Если сказано "в четверг" или другой день недели, укажи ближайшую дату этого дня после call_date, если из контекста не следует другое.
- Если точную дату вывести нельзя, deadline = null, а в тексте пиши "срок не указан" или "срок: следующая неделя без точной даты".
- Таймкоды указывай в формате HH:MM:SS или MM:SS, как они есть во входе.

Правила задач
- Отдельный блок "Операционные задачи" обязателен.
- Одна задача = одно действие с проверяемым результатом. Не объединяй разные действия в одну строку.
- Каждая задача должна содержать: номер, ответственный, действие, срок, статус, критерий результата, источник/таймкод.
- Если ответственный не назван, пиши "Требует назначения"; в JSON person_name = null, display_owner = "Требует назначения".
- Если срок не назван, пиши "срок не указан"; в JSON deadline = null.
- Если задача сформулирована размыто ("начать", "актуализировать", "обсудить", "контролировать"), сохрани ее, но добавь task_quality = "low" и missing_fields.
- Не превращай выполненный факт в плановую задачу. Переноси его в results.
- Не назначай владельца по догадке. Можно указать recommended_owner только отдельно и объяснить, почему это рекомендация, а не факт.

Что обязательно извлекать
1. Краткая сводка: 4-8 предложений о сути встречи, ключевых решениях, задачах и рисках.
2. Темы обсуждения: название темы, таймкод начала, факты, числа, документы, спорные места, статус.
3. Решения: только то, что реально принято. Если нет владельца/срока/следующего шага, статус unresolved или risk.
4. Операционные задачи: все поручения и договоренности с владельцами или явной пометкой "Требует назначения".
5. Выполненные факты: "сделал", "отправил", "подготовил", "проверил", "собрал" фиксируй как result.
6. Открытые вопросы: что не решено, кто должен уточнить, риск если не закрыть.
7. Риски и контрольные точки: конкретный риск, владелец риска или "Требует назначения", что проверить, срок проверки.
8. Управленческая диагностика: где не хватает владельца, срока, критерия результата, процедуры, контроля или данных.
9. Поведенческие факторы: обязательно оставь отдельный раздел. Фиксируй только управленчески значимые сигналы: неопределенность, уход от ответственности, повторяющееся отсутствие сроков, конфликт, сопротивление, инициативность, готовность брать ответственность. Если значимых сигналов нет, так и напиши.
10. Что контролировать на следующем созвоне: только проверяемые пункты. У каждого пункта должен быть владелец или "Требует назначения" и срок/период.

Качество решения
Для каждой важной темы оцени decision_quality:
- high: есть владелец, срок, критерий результата, следующий шаг и проверка.
- medium: есть часть элементов, но не хватает одного-двух критичных параметров.
- low: тема обсуждена, но нет владельца, срока, критерия результата или следующего шага.

Запреты
- Не выдумывай факты, владельцев, суммы, даты, статусы и выводы.
- Не скрывай отсутствие конкретики. Пробелы по владельцу, сроку и критерию результата должны быть видны в отчете.
- Не пиши длинную стенограмму.
- Не теряй мелкие, но проверяемые факты и договоренности.
- Не возвращай markdown вокруг JSON. Ответ должен быть строго валидным JSON.

Формат report_text
Пиши по-русски. Структура строго:
1. Источник
2. Участники и упомянутые люди
3. Краткая сводка
4. Операционные задачи
5. Решения и статусы
6. Темы обсуждения
7. Выполненные факты
8. Открытые вопросы
9. Риски и контрольные точки
10. Управленческая диагностика
11. Поведенческие факторы
12. Что контролировать на следующем созвоне

Оформление report_text:
- Не используй JSON-подобные подписи в человекочитаемом тексте: raw_name, org_match, matched, ambiguous, not_found, null.
- Не делай каждую задачу, вопрос или риск отдельным "подзаголовком". Подзаголовками являются только 12 логических разделов выше.
- Внутри разделов используй обычные короткие строки или списки.
- В блоке людей пиши компактно, без длинных карточек по каждому человеку.

В разделе "Операционные задачи" используй формат:
1. Ответственный: ФИО / Требует назначения. Задача: действие. Срок: DD.MM.YYYY / срок не указан / следующая неделя без точной даты. Критерий результата: что должно быть на выходе. Статус: planned|in_progress|done|blocked|postponed|unknown. Источник: таймкод.

Верни строго валидный JSON:
{
  "summary": "",
  "report_text": "",
  "source": {
    "call_id": "",
    "zoom_uuid": "",
    "call_date": "YYYY-MM-DD или null",
    "topic": "",
    "participants_raw": [],
    "segments_count": 0
  },
  "people": {
    "actual_participants": [
      {
        "raw_name": "",
        "person_name": "",
        "org_match": "matched|ambiguous|not_found",
        "bitrix_user_id": null,
        "work_position": null,
        "departments": [],
        "manager_name": null,
        "evidence": "Zoom API participant или speaker"
      }
    ],
    "mentioned_people": [
      {
        "raw_name": "",
        "person_name": "",
        "org_match": "matched|ambiguous|not_found",
        "bitrix_user_id": null,
        "work_position": null,
        "departments": [],
        "manager_name": null,
        "mention_context": "",
        "timecode": null
      }
    ]
  },
  "topics": [
    {
      "title": "",
      "start_time": null,
      "summary": "",
      "important_facts": [],
      "numbers": [],
      "documents": [],
      "status": "decided|unresolved|risk|done|in_progress|informational",
      "decision_quality": "high|medium|low|unknown",
      "missing_fields": [],
      "confidence": 0.0,
      "evidence": [{"time": "", "speaker": "", "text": ""}]
    }
  ],
  "tasks": [
    {
      "person_name": null,
      "display_owner": "Требует назначения",
      "org_match": "matched|ambiguous|not_found|not_applicable",
      "org_person": null,
      "text": "",
      "context": null,
      "assigned_at": "YYYY-MM-DD или null",
      "deadline": "YYYY-MM-DD или null",
      "deadline_text": "срок не указан",
      "success_criteria": null,
      "timecode": null,
      "status": "planned|in_progress|done|blocked|postponed|unknown",
      "task_quality": "high|medium|low",
      "missing_fields": ["owner", "deadline", "success_criteria"],
      "recommended_owner": null,
      "recommendation_reason": null,
      "confidence": 0.0,
      "evidence": [{"time": "", "speaker": "", "text": ""}]
    }
  ],
  "results": [
    {
      "person_name": "",
      "org_match": "matched|ambiguous|not_found|not_applicable",
      "org_person": null,
      "text": "",
      "completed_at": "YYYY-MM-DD или null",
      "timecode": null,
      "confidence": 0.0,
      "evidence": [{"time": "", "speaker": "", "text": ""}]
    }
  ],
  "decisions_status_map": [
    {
      "topic": "",
      "status": "decided|unresolved|risk|done|in_progress|informational",
      "what_happened": "",
      "why_status": "",
      "decision_quality": "high|medium|low|unknown",
      "next_step": null,
      "owner": null,
      "deadline": null,
      "missing_fields": []
    }
  ],
  "questions": [
    {
      "text": "",
      "asked_by": null,
      "addressed_to": null,
      "status": "answered|partially_answered|unanswered|unclear",
      "deadline": null,
      "risk_if_unanswered": null,
      "evidence": [{"time": "", "speaker": "", "text": ""}]
    }
  ],
  "risks_control_points": [
    {
      "risk": "",
      "owner": null,
      "display_owner": "Требует назначения",
      "risk_level": "low|medium|high|critical|unknown",
      "reason": "",
      "control_point": "",
      "deadline": null,
      "evidence": [{"time": "", "speaker": "", "text": ""}]
    }
  ],
  "management_diagnostics": {
    "decision_quality": [],
    "missing_specificity": [],
    "strong_management_actions": [],
    "missed_signals": [],
    "uncertainty_markers": []
  },
  "behavioral_signals": [
    {
      "signal": "",
      "person_name": null,
      "interpretation": "",
      "impact": "",
      "confidence": 0.0,
      "evidence": [{"time": "", "speaker": "", "text": ""}]
    }
  ],
  "next_call_control": [
    {
      "owner": null,
      "display_owner": "Требует назначения",
      "text": "",
      "deadline": null,
      "deadline_text": "",
      "source_task_index": null
    }
  ],
  "notes": []
}

Если данных по разделу нет, верни пустой массив или null, но не придумывай содержимое."""


def main() -> None:
    with psycopg.connect(load_database_url()) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO ai_prompt_categories (category_key, title, description, sort_order, is_active)
                    VALUES (%s, %s, %s, %s, TRUE)
                    ON CONFLICT (category_key) DO UPDATE
                    SET title = EXCLUDED.title,
                        description = EXCLUDED.description,
                        sort_order = EXCLUDED.sort_order,
                        is_active = TRUE,
                        updated_at = now()
                    RETURNING id
                    """,
                    (
                        "zoom_processing",
                        "Обработка Зумов",
                        "Расшифровка и управленческий разбор Zoom-созвонов",
                        70,
                    ),
                )
                category_id = cur.fetchone()[0]
                cur.execute(
                    "SELECT COALESCE(MAX(version), 0) + 1 FROM ai_prompts WHERE category_id = %s",
                    (category_id,),
                )
                version = cur.fetchone()[0]
                cur.execute(
                    """
                    INSERT INTO ai_prompts (category_id, prompt_key, title, prompt_text, version, is_active)
                    VALUES (%s, %s, %s, %s, %s, TRUE)
                    RETURNING id
                    """,
                    (category_id, "zoom_processing", "Обработка Зумов", PROMPT_TEXT, version),
                )
                prompt_id = cur.fetchone()[0]
    print(f"saved zoom_processing prompt {prompt_id} version {version}")


if __name__ == "__main__":
    main()
