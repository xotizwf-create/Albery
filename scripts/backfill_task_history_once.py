#!/usr/bin/env python3
"""Разовый бэкфилл истории изменений задач из уже накопленного raw_json.

Зачем разово. Синхронизация с 25.06.2026 звала tasks.task.history.list на каждую задачу и
складывала ответ в bitrix_tasks.raw_json, откуда его никто не доставал. Это родной аудит
портала — кто, какое поле, из чего в что, когда. На 06.08.2026 там 3467 записей по 937
задачам, включая 147 переносов дедлайна. То есть история за всё время жизни задач
восстанавливается ЗАДНИМ ЧИСЛОМ, без единого обращения к Битриксу.

Дальше её поддерживает сама синхронизация (bitrix.py::upsert_task_records), поэтому скрипт
нужен один раз — после миграции 081. Повторный запуск безопасен: вставка идёт
ON CONFLICT (bitrix_history_id) DO NOTHING, дедуп по родному id записи на портале.

Запуск на проде:
    cd /var/www/albery && ./.venv/bin/python scripts/backfill_task_history_once.py
Пробный прогон без записи:
    ... scripts/backfill_task_history_once.py --dry-run
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Ни одна стартовая процедура приложения не должна сработать из офлайн-скрипта: b24bot и app
# импортируются и здесь, а их process-start routines рассылали живым пользователям
# «я перезапустился» (грабля из docs/playbooks/safe-deploy.md).
os.environ.setdefault("B24_TASK_OFFER", "0")
os.environ.setdefault("B24_TASK_CHECKIN", "0")
os.environ.setdefault("B24_SESSION_IDLE_WATCH", "0")

from app import pg_connect  # noqa: E402
from bitrix import task_history_rows  # noqa: E402


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    scanned = tasks_with_history = prepared = inserted = 0

    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, bitrix_task_id, raw_json
                FROM bitrix_tasks
                WHERE jsonb_array_length(COALESCE(raw_json->'history'->'items', '[]'::jsonb)) > 0
                ORDER BY bitrix_task_id
                """
            )
            tasks = cur.fetchall()

        for task in tasks:
            scanned += 1
            rows = task_history_rows(task["raw_json"] or {})
            if not rows:
                continue
            tasks_with_history += 1
            prepared += len(rows)
            if dry_run:
                continue
            # Отдельная транзакция на задачу: одна кривая запись не должна отменить весь бэкфилл.
            with conn.transaction():
                with conn.cursor() as cur:
                    for row in rows:
                        cur.execute(
                            """
                            INSERT INTO bitrix_task_history (
                                bitrix_history_id, bitrix_task_id, task_id, field,
                                value_from, value_to, changed_by_bitrix_user_id,
                                changed_by_name, changed_at
                            ) VALUES (
                                %(bitrix_history_id)s, %(bitrix_task_id)s, %(task_id)s, %(field)s,
                                %(value_from)s, %(value_to)s, %(changed_by_bitrix_user_id)s,
                                %(changed_by_name)s, %(changed_at)s
                            )
                            ON CONFLICT (bitrix_history_id) DO NOTHING
                            """,
                            {**row, "task_id": task["id"]},
                        )
                        inserted += cur.rowcount

    print(f"задач просмотрено:      {scanned}")
    print(f"из них с историей:      {tasks_with_history}")
    print(f"записей истории найдено:{prepared}")
    if dry_run:
        print("--dry-run: не записано ничего")
    else:
        print(f"записей добавлено:      {inserted} (дубли пропущены по bitrix_history_id)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
