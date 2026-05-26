"""Idempotent live-instruction fixes for chat-report and task-search routing.

Applies two corrections to the live `ai_instruction_folders` content read by the
MCP server:

1. Empty-day rule for chat reports: an active chat with no messages on a date is
   simply skipped. No daily report is created for it (not even a `no_data` one).
   This removes the contradiction with the dedicated daily/weekly chat report
   instructions and with get_report_readiness.
2. Task search by number: when a request contains a task number, route to
   search_tasks(bitrix_task_id=...) for an instant indexed lookup instead of a
   text ILIKE scan over title/description.

Run locally against the dev DB and on the server against the production DB
(DATABASE_URL decides which). Safe to run repeatedly: each edit is skipped if it
is already present, and fails loudly if an anchor drifted.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "mcp") not in sys.path:
    sys.path.insert(0, str(ROOT / "mcp"))

import context_server as cs  # noqa: E402


# Each edit: (anchor_old, replacement_new, already_applied_marker)
EDITS: dict[str, list[tuple[str, str, str]]] = {
    "Описание доступных инструментов": [
        (
            "- `search_tasks` - поиск задач по периоду, тексту и ответственному.",
            "- `search_tasks` - поиск задач по id, периоду, тексту и ответственному.\n\n"
            "Если в запросе есть номер задачи (например, `318241`), это самый быстрый и правильный путь: "
            "сразу `search_tasks(bitrix_task_id=318241)` — мгновенный поиск по индексу, возвращает одну задачу. "
            "Не передавать номер в `query` (он ищет только по тексту названия/описания) и не листать все задачи через `offset`. "
            "Полный текст одной задачи: `search_tasks(bitrix_task_id=318241, include_full_description=true)`. "
            "Описания задач по умолчанию приходят сокращёнными (превью 500 символов) для скорости.",
            "поиск задач по id, периоду",
        ),
    ],
    "Работа в системе / Классификатор запросов": [
        (
            "1. `search_tasks(query,date_from,date_to)` - основной источник статуса, срока, ответственного.",
            "1. Если в запросе есть номер задачи (например, `318241`) — сразу `search_tasks(bitrix_task_id=318241)`: "
            "мгновенный поиск одной задачи по индексу, без `query` и без перебора всех задач. "
            "Если номера нет — `search_tasks(query,date_from,date_to)` как основной источник статуса, срока, ответственного.",
            "мгновенный поиск одной задачи по индексу",
        ),
    ],
    "Описание доступных инструментов / Генерация отчетов по чатам": [
        (
            "Недельный отчет строится только по дневным отчетам. Если за какой-то день недели дневного отчета нет, "
            "MCP сначала формирует и сохраняет этот дневной отчет по дневной инструкции, затем возвращается к недельному отчету.",
            "Недельный отчет строится только по дневным отчетам за дни, где есть сообщения. "
            "Если за день с сообщениями дневного отчета нет, MCP сначала формирует и сохраняет этот дневной отчет "
            "по дневной инструкции, затем возвращается к недельному отчету. "
            "Дни без сообщений пропускаются: для них дневной отчет не формируется и не сохраняется.",
            "Дни без сообщений пропускаются: для них дневной отчет не формируется",
        ),
        (
            "5. Если сообщений за день нет, сохранить дневной отчет `no_data`.",
            "5. Если сообщений за день нет, пропустить этот день: дневной отчет не формировать "
            "и не сохранять (в том числе `no_data`).",
            "пропустить этот день: дневной отчет не формировать",
        ),
        (
            "Если за 11 мая нет сообщений, сохранить `no_data` отчет за 11 мая, чтобы 12 мая имел предыдущий контекст.",
            "Если за 11 мая нет сообщений, день пропускается: отчет за 11 мая не создается (в том числе `no_data`). "
            "Для контекста 12 мая берется ближайший предыдущий день, где отчет есть.",
            "день пропускается: отчет за 11 мая не создается",
        ),
    ],
}


def apply() -> int:
    rows = {row["path"]: row for row in cs.load_ai_instructions()}
    updated = 0
    for path, edits in EDITS.items():
        row = rows.get(path)
        if not row:
            raise SystemExit(f"Instruction folder not found: {path!r}")
        content = row["content"] or ""
        changed = False
        for old, new, marker in edits:
            if old in content:
                content = content.replace(old, new, 1)
                changed = True
            elif marker in content:
                print(f"  [already applied] {path}: {marker[:40]}...")
            else:
                raise SystemExit(
                    f"Anchor not found and edit not present in {path!r}.\n"
                    f"Anchor: {old[:80]}..."
                )
        if changed:
            with cs.connect() as conn:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE ai_instruction_folders SET content = %s, updated_at = now() WHERE id = %s",
                            (content, row["id"]),
                        )
            updated += 1
            print(f"  [updated] {path} ({len(content)} chars)")
        else:
            print(f"  [no change] {path}")
    return updated


def main() -> None:
    updated = apply()
    print(f"Done. Folders updated: {updated}.")


if __name__ == "__main__":
    main()
