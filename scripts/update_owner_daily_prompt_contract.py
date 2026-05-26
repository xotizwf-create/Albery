from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app  # noqa: E402


OWNER_DAILY_FEEDBACK_REGULATIONS_ADDENDUM = """


## Дополнение: обратная связь, регламенты и качество адресных рекомендаций

Это дополнение не заменяет основной промт выше. Оно только усиливает его.
Сохраняй всю структуру, стиль, подробность, критерии качества и формат JSON из основного промта.

### Новые входные блоки

- `recommendation_feedback_context` - обратная связь сотрудников по адресным рекомендациям из предыдущего owner-отчета и текущих активных рекомендаций: кто ответил, что принял, с чем не согласился, что делегировал, где ответ странный/уклончивый, где ответа нет.
- `company_regulations_context` - регламенты, роли, владельцы процессов, порядок согласований, платежный календарь, ритм встреч, матрицы решений и SLA из раздела "О компании"; это нормативная база для сверки факта с правилами.

### Как использовать обратную связь

Перед заполнением адресных рекомендаций и `manager_messages` обязательно прочитай `recommendation_feedback_context`.

Каждое готовое сообщение адресату должно начинаться с мягкого вступления с учетом его предыдущей реакции:

- если человек ответил по прошлой рекомендации: поблагодари за обратную связь и покажи, что замечание учтено;
- если человек согласился или взял в работу: поблагодари и попроси конкретный следующий статус/срок;
- если человек возразил или делегировал: признай аргумент и предложи следующий шаг по регламенту/Bitrix;
- если ответ странный, уклончивый или без срока: мягко попроси конкретизировать статус, владельца и дату;
- если ответа нет: начни нейтрально, без упрека, и попроси коротко подтвердить статус.

Пример тона:
`Наталья, приветствую, благодарю за обратную связь. Учел замечание по <тема>; давайте попробуем следующий шаг: ...`

Не используй этот пример механически для всех. Вступление должно зависеть от фактического ответа человека.

### Как использовать регламенты

Перед заполнением адресных рекомендаций и `manager_messages` обязательно прочитай `company_regulations_context`.

Каждую рекомендацию сверяй с регламентами компании, если для темы есть релевантное правило:

- фактический владелец процесса против регламентного владельца;
- фактический исполнитель против регламентного исполнителя;
- сроки и SLA против фактических сроков;
- порядок согласования против фактического маршрута решения;
- ритм встреч/контроля против фактически видимых Zoom/чатов/Bitrix-статусов.

Если факт расходится с регламентом, укажи это прямо, но мягко, и предложи корректное действие:

- делегировать правильному владельцу;
- согласовать исключение;
- зафиксировать владельца/срок/критерий в Bitrix;
- обновить регламент, если процесс действительно изменился.

Пример логики:
если в регламенте платежный календарь закреплен за Анастасией, а фактически его ведет Артур, в рекомендации Артуру нужно указать на регламент и предложить передать/согласовать ведение с Анастасией либо обновить регламент.

### Жесткое правило

Новые блоки про обратную связь и регламенты не должны упрощать отчет.
Они должны сделать отчет более точным: сохранить подробность основного промта, добавить персональный контекст реакции адресата и регламентную сверку к каждой релевантной рекомендации.
"""


def load_base_prompt(cur: object, base_version: int | None) -> str:
    if base_version is not None:
        cur.execute(
            """
            SELECT p.prompt_text
            FROM ai_prompts p
            JOIN ai_prompt_categories c ON c.id = p.category_id
            WHERE c.category_key = 'owner_daily_report'
              AND p.version = %s
            """,
            (base_version,),
        )
    else:
        cur.execute(
            """
            SELECT p.prompt_text
            FROM ai_prompts p
            JOIN ai_prompt_categories c ON c.id = p.category_id
            WHERE c.category_key = 'owner_daily_report'
            ORDER BY
                CASE
                    WHEN p.prompt_text LIKE '%%Ты и команда%%' THEN 0
                    ELSE 1
                END,
                length(p.prompt_text) DESC,
                p.version DESC
            LIMIT 1
            """,
        )
    row = cur.fetchone()
    if not row:
        raise RuntimeError("Base owner_daily_report prompt was not found in database.")
    return str(row["prompt_text"] or "").rstrip()


def upsert_active_owner_daily_prompt(base_version: int | None) -> int:
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
                base_prompt = load_base_prompt(cur, base_version)
                prompt_text = base_prompt
                if "## Дополнение: обратная связь, регламенты и качество адресных рекомендаций" not in prompt_text:
                    prompt_text = prompt_text.rstrip() + OWNER_DAILY_FEEDBACK_REGULATIONS_ADDENDUM
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
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-version",
        type=int,
        default=int(os.getenv("OWNER_DAILY_BASE_PROMPT_VERSION", "4")),
        help="Owner daily prompt version to extend. Defaults to version 4.",
    )
    args = parser.parse_args()
    if not app.postgres_enabled():
        raise RuntimeError("PostgreSQL is required to update owner daily prompt contract.")
    version = upsert_active_owner_daily_prompt(args.base_version)
    upsert_owner_daily_instruction()
    print(
        f"Updated owner_daily_report prompt to version {version} "
        f"from base version {args.base_version} and refreshed MCP AI instruction."
    )


if __name__ == "__main__":
    main()
