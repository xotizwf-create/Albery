from __future__ import annotations

"""Update the active zoom_processing prompt:
1. Краткая сводка (item 3) must include a "Соответствие регламенту" verdict.
2. Embed a compact meeting regulation block to compare each call against.
3. Поведенческие факторы (item 9 / report section 11) rewritten to demand
   concrete, episode-anchored signals instead of generic labels.

Pattern mirrors scripts/update_recommendation_prompt_contracts.py: deactivate
the current active prompt and insert a new version, keeping full history.
"""

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row


MARKER = "Регламент встреч (норма для сверки)"

OLD_SUMMARY = "1. Краткая сводка: 4-8 предложений о сути встречи, ключевых решениях, задачах и рисках."

NEW_SUMMARY = (
    "1. Краткая сводка: 4-8 предложений о сути встречи, ключевых решениях, задачах и рисках. "
    "В конце краткой сводки обязательно добавь блок \"Соответствие регламенту\" (1-3 предложения): "
    "какой регламентной встрече из раздела \"Регламент встреч (норма для сверки)\" соответствует этот созвон; "
    "проведён ли он в норму по времени, длительности и составу участников (кто из обязательных был, кого не было, "
    "кто присутствовал не под своим Именем и Фамилией); покрыта ли обязательная повестка или встреча ушла в "
    "посторонние темы. Если созвон не соответствует ни одной регламентной встрече, прямо так и напиши."
)

OLD_BEHAVIOR = (
    "9. Поведенческие факторы: обязательно оставь отдельный раздел. Фиксируй только управленчески значимые сигналы: "
    "неопределенность, уход от ответственности, повторяющееся отсутствие сроков, конфликт, сопротивление, "
    "инициативность, готовность брать ответственность. Если значимых сигналов нет, так и напиши."
)

NEW_BEHAVIOR = (
    "9. Поведенческие факторы: обязательный отдельный раздел. Это не ярлыки и не общие характеристики людей. "
    "По каждому значимому сигналу пиши предметно и с доказательством:\n"
    "   - кто (ФИО);\n"
    "   - что именно делал или говорил — с привязкой к таймкоду и, по возможности, короткой цитатой;\n"
    "   - по делу или не по делу: работал ли человек на цель встречи и обязательную повестку или уходил в "
    "посторонние темы, возмущения, общие рассуждения, повторы;\n"
    "   - управленческое последствие: как это повлияло на ход встречи и принятие решений.\n"
    "   Если это видно по транскрипту, оцени, какую долю встречи человек или встреча работали по делу, а какую — мимо цели.\n"
    "   Примеры правильной формы (это образец формулировки, а не готовый вывод): "
    "\"Артур примерно половину встречи (00:12:00-00:48:00) посвятил возмущениям про внедрение ИИ вместо разбора "
    "плана недели — обязательная повестка по продажам и платёжному календарю осталась не пройдена\"; "
    "\"Наталья дважды (00:05:10, 00:22:40) уходила от конкретного срока по плану продаж, отвечая общими словами, — "
    "решение осталось без даты\".\n"
    "   Запрещены пустые формулировки без эпизода: \"проявлял инициативность\", \"демонстрировал сопротивление\", "
    "\"усиливал рамку\" без указания, что именно человек сказал или сделал и когда.\n"
    "   Каждый сигнал в JSON behavioral_signals обязан иметь непустой evidence с таймкодом и текстом-цитатой.\n"
    "   Если значимых сигналов нет, так и напиши."
)

INSERT_ANCHOR = "Что обязательно извлекать\n"

REGULATION_BLOCK = (
    "## Регламент встреч (норма для сверки)\n"
    "Сверяй созвон с этим расписанием. Определи, какой встрече он соответствует, проведена ли встреча в норму и "
    "покрыта ли её обязательная повестка. Это нужно для блока \"Соответствие регламенту\" в краткой сводке.\n\n"
    "Ежедневные планёрки:\n"
    "- Планёрка отдела продаж (Наталья, Софья): понедельник/среда/пятница, 10:00, 15-20 минут. "
    "Повестка: план на день, факт вчера, отклонения, блокеры.\n"
    "- Управленческая планёрка (Артур + закупки, склад, бухгалтерия, логистика): понедельник/среда/пятница, 11:00, "
    "60 минут. Повестка: остатки денег, поступления/списания, поставки план/факт, остатки SKU, статус логистики, "
    "отклонения, решения по платежам и закупкам, план недели.\n\n"
    "Еженедельные встречи:\n"
    "- Склад/закупки/ОП (Артур, Оксана, Наталья, Дмитрий, Бахтиер): вторник, 11:00, 60 минут. "
    "Контроль метрик и синхронизация команды.\n"
    "- Проверка платёжного календаря (Артур, Наталья, бухгалтерия): вторник, 14:00, 60 минут. "
    "Контроль плана затрат и поступлений, риск кассового разрыва.\n"
    "- Исполнительная (операционная) встреча недели (Евгений, Артур, Наталья, Анастасия по необходимости): "
    "в регламенте две несведённые версии времени — методология \"среда 09:00-12:00\" и таблица \"четверг 09:30-12:00\". "
    "Повестка: план/факт продаж, платёжный календарь, закупки, склад, проблемы, решения и постановка задач.\n"
    "- Контрольная встреча итогов недели (руководители): тоже две версии — методология \"пятница 15:00-16:30\" и "
    "таблица \"пятница 12:00-13:00\". Повестка: что планировали, что сделали, что не сделали и почему, ключевые "
    "отклонения, выводы, план следующей недели.\n"
    "- Стратегическая/ежемесячная встреча (Евгений, Артур, финблок): раз в месяц. Финрезультат, маржа, отклонения, "
    "решения по бизнесу.\n\n"
    "Правило регламента: каждый участник присутствует под своим Именем и Фамилией.\n"
    "Если созвон по времени попадает в одну из двух несведённых версий (исполнительная или контрольная встреча) — "
    "считай соответствие по ближайшей версии и отметь в выводе, что в регламенте календарь по этой встрече не сведён.\n\n"
)


def normalize_postgres_url(database_url: str) -> str:
    normalized = database_url.strip()
    for prefix in ("postgresql+psycopg2://", "postgresql+psycopg://"):
        if normalized.startswith(prefix):
            return "postgresql://" + normalized[len(prefix):]
    return normalized


def apply_edits(text: str) -> str:
    if OLD_SUMMARY not in text:
        raise RuntimeError("OLD_SUMMARY anchor not found in active prompt")
    if OLD_BEHAVIOR not in text:
        raise RuntimeError("OLD_BEHAVIOR anchor not found in active prompt")
    if INSERT_ANCHOR not in text:
        raise RuntimeError("INSERT_ANCHOR not found in active prompt")
    text = text.replace(OLD_SUMMARY, NEW_SUMMARY, 1)
    text = text.replace(OLD_BEHAVIOR, NEW_BEHAVIOR, 1)
    text = text.replace(INSERT_ANCHOR, REGULATION_BLOCK + INSERT_ANCHOR, 1)
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
                    print("Active zoom_processing prompt already updated; nothing changed.")
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
