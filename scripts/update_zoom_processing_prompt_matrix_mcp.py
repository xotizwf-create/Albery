from __future__ import annotations
"""Fix the matrix cross-check section of the active zoom_processing contract: the live path is the
Hermes agent (codex) which reads docs via MCP, NOT the dead openai function — so instruct it to READ
the matrix + process map through the albery MCP instead of expecting a `responsibility_reference`
input field. New version kept in history."""
import os
import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

MARKER = "search_company_knowledge(query=«матрица решений»)"

OLD = ("Во входных данных есть `responsibility_reference` — актуальные документы компании "
       "«Матрица решений» и «Карта процессов» (у каждого есть name и content). Это нормативный "
       "источник: кто за какой процесс/решение отвечает на уровне ИСПОЛНЕНИЯ (колонка «Исполняет») "
       "и на уровне КОНТРОЛЯ/процесса (колонки «Утверждает» / «Согласует», владелец процесса в карте).")

NEW = ("ПЕРЕД сверкой прочитай актуальные документы компании через MCP albery: "
       "search_company_knowledge(query=«матрица решений») и search_company_knowledge(query=«карта процессов»), "
       "затем get_company_file(folder_id=...) по нужному документу для полного текста (в «Матрице решений» "
       "строка про пересорт и складские операции находится глубоко в таблице — дочитай таблицу до конца). "
       "«Матрица решений» — таблица вида «Ключевое решение | Инициирует | Согласует | Утверждает | Исполняет»; "
       "«Карта процессов» — владельцы процессов и зоны ответственности. Это нормативный источник: кто за какой "
       "процесс/решение отвечает на уровне ИСПОЛНЕНИЯ (колонка «Исполняет») и на уровне КОНТРОЛЯ/процесса "
       "(колонки «Утверждает» / «Согласует», владелец процесса в карте).")


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
                if not active:
                    print("no active prompt"); return 1
                text = str(active["prompt_text"] or "")
                if MARKER in text:
                    print("already fixed"); return 0
                if text.count(OLD) != 1:
                    print("ABORT: OLD anchor count =", text.count(OLD)); return 2
                new_text = text.replace(OLD, NEW, 1)
                cur.execute("UPDATE ai_prompts SET is_active=FALSE WHERE id=%s", (active["id"],))
                cur.execute(
                    """INSERT INTO ai_prompts (category_id, prompt_key, title, prompt_text, version, is_active, created_by_user_id)
                       VALUES (%s,%s,%s,%s,%s,TRUE,NULL) RETURNING id, version""",
                    (active["category_id"], active["prompt_key"] or "zoom_processing",
                     active["title"] or "Обработка Зумов", new_text, int(active["version"] or 1) + 1),
                )
                ins = cur.fetchone()
                print(f"Fixed -> id {ins['id']} version {ins['version']} (len {len(text)} -> {len(new_text)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
