from __future__ import annotations
"""Strengthen the matrix cross-check section so the model reliably fills responsibility_check
(incl. source_document) AND adds BOTH the recommendation line + the duplicate task. New version."""
import os
import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

MARKER = "ЭТО КЛЮЧЕВОЕ ТРЕБОВАНИЕ ВЛАДЕЛЬЦА"
HEADER = "## Сверка задач с матрицей решений и картой процессов (ОБЯЗАТЕЛЬНО)\n"
INJECT = (HEADER +
          "ЭТО КЛЮЧЕВОЕ ТРЕБОВАНИЕ ВЛАДЕЛЬЦА — выполняй его в КАЖДОМ отчёте, не пропускай. Для КАЖДОЙ "
          "operational_task ОБЯЗАТЕЛЬНО заполни объект responsibility_check со ВСЕМИ полями: process, "
          "matrix_executor, matrix_controller, source_document (ТОЧНОЕ название документа-источника, "
          "например «Копия Рабочая матрица решений по ключевым процессам 04.05.26»), mismatch, note. "
          "Поле source_document НЕ может быть null, если ты сверял задачу. При расхождении добавляй ОБА "
          "элемента сразу: и строку-рекомендацию «🔸 Рекомендуется передать…» в task_text исходной задачи, "
          "и отдельную задачу-дубль с responsibility_duplicate=true — не одно из двух, а оба.\n")


def norm(url: str) -> str:
    url = url.strip()
    for p in ("postgresql+psycopg2://", "postgresql+psycopg://"):
        if url.startswith(p):
            return "postgresql://" + url[len(p):]
    return url


def main() -> int:
    load_dotenv("/var/www/albery/.env")
    dsn = norm(os.getenv("DATABASE_ADMIN_URL", "").strip() or os.getenv("DATABASE_URL", "").strip())
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT c.id AS category_id, p.* FROM ai_prompt_categories c
                       JOIN ai_prompts p ON p.category_id=c.id
                       WHERE c.category_key=%s AND c.is_active=TRUE AND p.is_active=TRUE
                       ORDER BY p.version DESC, p.created_at DESC LIMIT 1""",
                    ("zoom_processing",),
                )
                active = cur.fetchone()
                text = str(active["prompt_text"] or "")
                if MARKER in text:
                    print("already strengthened"); return 0
                if text.count(HEADER) != 1:
                    print("ABORT header count =", text.count(HEADER)); return 2
                new_text = text.replace(HEADER, INJECT, 1)
                cur.execute("UPDATE ai_prompts SET is_active=FALSE WHERE id=%s", (active["id"],))
                cur.execute(
                    """INSERT INTO ai_prompts (category_id, prompt_key, title, prompt_text, version, is_active, created_by_user_id)
                       VALUES (%s,%s,%s,%s,%s,TRUE,NULL) RETURNING id, version""",
                    (active["category_id"], active["prompt_key"] or "zoom_processing",
                     active["title"] or "Обработка Зумов", new_text, int(active["version"] or 1) + 1),
                )
                ins = cur.fetchone()
                print(f"Strengthened -> id {ins['id']} version {ins['version']} (len {len(text)} -> {len(new_text)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
