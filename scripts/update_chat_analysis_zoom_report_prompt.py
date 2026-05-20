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


REPLACEMENTS = [
    (
        '- `Zoom-созвоны текущего дня` - расшифровки созвонов за дату отчета из раздела "Zoom созвоны". Это источник фактов выполнения, решений, новых поручений, переносов и рисков, если они относятся к людям/темам этого чата.',
        '- `Zoom-отчеты текущего дня` - готовые управленческие отчеты по Zoom-созвонам за дату отчета из раздела "Zoom созвоны". Перед анализом чата отсутствующие Zoom-отчеты должны быть сформированы отдельно. В `chat_analysis` не передается сырая транскрибация Zoom; используй только готовый Zoom-отчет и бери из него только то, что относится к этому чату: задачи участников чата, выполнение/перенос их задач, решения по темам чата, риски и контрольные точки.',
    ),
    (
        "- Если в контексте есть `Хвосты с прошлого дня`, обязательно проверь каждый хвост по переписке текущего дня и Zoom-созвонам текущего дня, затем верни его в `previous_day_tasks`.",
        "- Если в контексте есть `Хвосты с прошлого дня`, обязательно проверь каждый хвост по переписке текущего дня и Zoom-отчетам текущего дня, затем верни его в `previous_day_tasks`.",
    ),
    (
        "- Задачи из текущего дня должны находиться по двум источникам: переписка чата + Zoom-созвоны текущего дня. Если задача поставлена в Zoom, но относится к этому чату/участникам, добавь ее в `commitments` с `source_type = \"zoom\"` и `evidence_zoom_call_ids`.",
        "- Задачи из текущего дня должны находиться по двум источникам: переписка чата + Zoom-отчеты текущего дня. Если задача поставлена в Zoom-отчете, но относится к этому чату/участникам, добавь ее в `commitments` с `source_type = \"zoom\"` и `evidence_zoom_call_ids`.",
    ),
    (
        "- Факт выполнения задач определяй по совокупности переписки и Zoom: если выполнение подтверждено в Zoom, а в чате не написано, все равно фиксируй `results` с источником Zoom.",
        "- Факт выполнения задач определяй по совокупности переписки и Zoom-отчетов: если выполнение подтверждено в Zoom-отчете, а в чате не написано, все равно фиксируй `results` с источником Zoom.",
    ),
    (
        "1. Анализируй только предоставленные входные данные: расшифровку чата, OCR-текст вложений, Zoom-созвоны текущего дня, хвосты и отчет предыдущего дня, активные цели и недельный контекст. Не используй догадки и внешние знания.",
        "1. Анализируй только предоставленные входные данные: расшифровку чата, OCR-текст вложений, готовые Zoom-отчеты текущего дня, хвосты и отчет предыдущего дня, активные цели и недельный контекст. Не используй догадки и внешние знания. Не используй сырую Zoom-транскрибацию в `chat_analysis`.",
    ),
    (
        "9. Для доказательств из Zoom используй `zoom_call_id` в `evidence_zoom_call_ids`; если есть сегмент/таймкод, укажи его в `source_detail`.",
        "9. Для доказательств из Zoom используй `zoom_call_id` в `evidence_zoom_call_ids`; в `source_detail` укажи раздел/пункт Zoom-отчета и таймкод, если он есть в Zoom-отчете.",
    ),
]


INSERT_AFTER = (
    "- Не добавляй факты из источников, которые не переданы во входных данных.",
    "- В разделе `Источник` указывай Zoom как `Zoom-отчеты`, если использовались `analytical_note`; не пиши, что анализировалась Zoom-транскрибация.\n"
    "- Из Zoom-отчета переносить только релевантное чату: задачи участников чата, результаты/переносы их задач, решения по темам чата, риски и контрольные точки. Не переносить весь Zoom-отчет целиком.",
)


def updated_prompt(prompt_text: str) -> str:
    updated = prompt_text
    missing: list[str] = []
    for old, new in REPLACEMENTS:
        if old not in updated:
            missing.append(old[:120])
            continue
        updated = updated.replace(old, new)

    marker, addition = INSERT_AFTER
    if addition not in updated:
        if marker not in updated:
            missing.append(marker)
        else:
            updated = updated.replace(marker, marker + "\n" + addition)

    if missing:
        raise RuntimeError("Could not update prompt; missing fragments:\n- " + "\n- ".join(missing))
    return updated


def main() -> None:
    with psycopg.connect(load_database_url()) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT c.id, p.prompt_text
                    FROM ai_prompt_categories c
                    JOIN ai_prompts p ON p.category_id = c.id
                    WHERE c.category_key = %s AND p.is_active = TRUE
                    LIMIT 1
                    """,
                    ("chat_analysis",),
                )
                row = cur.fetchone()
                if not row:
                    raise RuntimeError("Active chat_analysis prompt not found")
                category_id, prompt_text = row
                new_prompt = updated_prompt(prompt_text)
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
                    (category_id, "chat_daily", "chat_analysis", new_prompt, version),
                )
                prompt_id = cur.fetchone()[0]
    print(f"saved chat_analysis prompt {prompt_id} version {version}")


if __name__ == "__main__":
    main()
