from __future__ import annotations
"""Append a 'decision matrix / process map cross-check' section to the active zoom_processing
prompt (new version, full history kept). For every operational task the model now compares the
on-call assignee against the documented responsible person (responsibility_reference in the
input) and, on a clear mismatch, (a) adds a recommendation line to the original task and
(b) emits a duplicate operational_task for the correct process/control owner. Review-gated:
duplicates flow through the normal owner dispatch confirmation."""
import os
import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

MARKER = "Сверка задач с матрицей решений и картой процессов"

SECTION = """

## Сверка задач с матрицей решений и картой процессов (ОБЯЗАТЕЛЬНО)
Во входных данных есть `responsibility_reference` — актуальные документы компании «Матрица решений» и «Карта процессов» (у каждого есть name и content). Это нормативный источник: кто за какой процесс/решение отвечает на уровне ИСПОЛНЕНИЯ (колонка «Исполняет») и на уровне КОНТРОЛЯ/процесса (колонки «Утверждает» / «Согласует», владелец процесса в карте).

Для КАЖДОЙ операционной задачи с назначенным на созвоне исполнителем выполни сверку:
1. Определи процесс/зону задачи (склад, приёмка, пересорт/расхождения, КИЗ/маркировка, фабрика/контроль качества, логистика, продажи, финансы и т.д.).
2. Найди в матрице/карте ответственного за этот процесс: исполнительный уровень и контрольный/процессный уровень. Фамилии из документов сопоставляй с полным ФИО и bitrix_user_id через `org_context.users`.
3. Сравни назначенного на созвоне исполнителя с ответственным по матрице/карте.

Если назначенный на созвоне совпадает с ответственным по матрице — ничего не меняй.

Если задачу повесили НЕ на ответственного по матрице (явное расхождение):
- (а) В исходную задачу (в её task_text — тому, на кого её повесили на созвоне) добавь отдельной строкой в конце:
  «🔸 Рекомендуется передать эту задачу <ФИО ответственного за процесс/контроль>, так как по «<точное name документа>» зона ответственности за это закреплена за ним. Исполнительный уровень — <ФИО исполнителя по матрице, если отличается от контролёра>.»
- (б) Добавь в массив operational_tasks ОТДЕЛЬНУЮ задачу-дубль, назначенную на ответственного за процесс/контроль (assignee_name = его полное ФИО, bitrix_user_id из org_context.users), с task_text:
  «<краткая суть задачи>. На созвоне эту задачу поставили на <ФИО исходного исполнителя>, хотя по «<name документа>» зона ответственности за неё закреплена за вами. Рекомендую взять эту задачу на себя.»
  Для этой задачи: source = «сверка с матрицей решений»; deadline и status — как у исходной задачи (или «срок не указан»).

КОНСЕРВАТИВНОСТЬ (критично — чтобы не переназначать ошибочно):
- Помечай расхождение и создавай дубль ТОЛЬКО когда матрица/карта ЯВНО закрепляют зону за другим человеком и ты можешь назвать конкретный процесс/строку-источник.
- Если процесс задачи неоднозначен ИЛИ источник отклонения не определён — НЕ переназначай и НЕ создавай дубль. Вместо этого добавь в task_text исходной задачи строку: «🔸 По матрице зона ответственности за это требует уточнения; пока источник не определён — владелец разбора <ФИО владельца разбора по матрице/оргструктуре>.»
- НИКОГДА не выдумывай ответственного, которого нет в матрице/карте.
- Это РЕКОМЕНДАЦИЯ, а не факт: дополнения и задачи-дубли проходят подтверждение владельца перед отправкой в Bitrix. Формулируй мягко.

В JSON у каждой задачи, по которой делалась сверка, добавь поле `responsibility_check`:
{"process": "<процесс/зона>", "matrix_executor": "<ФИО или null>", "matrix_controller": "<ФИО или null>", "source_document": "<name документа>", "mismatch": true|false, "note": "<кратко>"}
У задач-дублей, созданных этой сверкой, добавь в JSON поле `responsibility_duplicate`: true.
"""


def normalize_postgres_url(url: str) -> str:
    url = url.strip()
    for pref in ("postgresql+psycopg2://", "postgresql+psycopg://"):
        if url.startswith(pref):
            return "postgresql://" + url[len(pref):]
    return url


def main() -> int:
    load_dotenv("/var/www/albery/.env")
    dsn = normalize_postgres_url(os.getenv("DATABASE_ADMIN_URL", "").strip() or os.getenv("DATABASE_URL", "").strip())
    if not dsn:
        raise RuntimeError("DATABASE_URL is not set")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT c.id AS category_id, p.*
                    FROM ai_prompt_categories c
                    JOIN ai_prompts p ON p.category_id = c.id
                    WHERE c.category_key = %s AND c.is_active = TRUE AND p.is_active = TRUE
                    ORDER BY p.version DESC, p.created_at DESC LIMIT 1
                    """,
                    ("zoom_processing",),
                )
                active = cur.fetchone()
                if not active:
                    print("No active zoom_processing prompt found; nothing changed.")
                    return 1
                text = str(active["prompt_text"] or "")
                if MARKER in text:
                    print("Already has the matrix cross-check section; nothing changed.")
                    return 0
                new_text = text.rstrip() + "\n" + SECTION
                cur.execute("UPDATE ai_prompts SET is_active = FALSE WHERE id = %s", (active["id"],))
                cur.execute(
                    """
                    INSERT INTO ai_prompts (category_id, prompt_key, title, prompt_text, version, is_active, created_by_user_id)
                    VALUES (%s, %s, %s, %s, %s, TRUE, NULL)
                    RETURNING id, version
                    """,
                    (active["category_id"], active["prompt_key"] or "zoom_processing",
                     active["title"] or "Обработка Зумов", new_text, int(active["version"] or 1) + 1),
                )
                ins = cur.fetchone()
                print(f"Updated zoom_processing prompt -> id {ins['id']} version {ins['version']} "
                      f"(len {len(text)} -> {len(new_text)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
