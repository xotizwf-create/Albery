#!/usr/bin/env python3
"""Записать фактический набор инструментов тем агентам, кто ехал на пресете по тиру.

Зачем и почему ОТДЕЛЬНЫМ шагом до миграции 082. Набор агента, которому инструменты не
настраивали, до сих пор вычислялся из колонки tier: faq → 18 инструментов, ops → 134,
developer → весь реестр. Это скрытый источник возможностей: «Агент Менеджер МП» получил
134 инструмента молча, потому что при создании ему поставили tier='ops'. Миграция 082
убирает этот источник, и если просто её накатить, такой агент разом обеднеет до базового
набора — то есть деплой поменяет поведение живого агента, чего быть не должно.

Поэтому порядок такой: сначала этот скрипт переносит ФАКТИЧЕСКИЙ набор в явный список
(поведение не меняется ни на йоту, потому что список записывается тот же самый, что и
считался), и только потом накатывается миграция с новым режимом.

Что трогаем и чего не трогаем:
  * только агенты с tools_customized = false — единственные, кто едет на пресете;
  * агент-разработчик пропускается СОЗНАТЕЛЬНО: его набор обязан обновляться сам при
    появлении новых инструментов, для него миграция ставит режим 'max';
  * агентов с явным списком не трогаем вовсе, даже если манифест режет их набор — стёртый
    список нельзя вернуть, а кап манифеста и так действует поверх.

Идемпотентен: после успешного прогона агентов с tools_customized = false не остаётся
(кроме разработчика), и повторный запуск ничего не делает. Без --apply только показывает.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Планировщики и фоновые обходы стартуют при импорте приложения — офлайн-скрипту они не
# нужны и могут написать живым людям. Гасим до импорта, как это делают соседние скрипты.
os.environ.setdefault("B24_TASK_OFFER", "0")
os.environ.setdefault("B24_TASK_CHECKIN", "0")
os.environ.setdefault("AGENT_AUTOMATIONS", "0")
os.environ.setdefault("B24_SESSION_IDLE_WATCH", "0")
os.environ.setdefault("RECURRING_TASKS_SCHEDULER", "0")
os.environ.setdefault("AGENT_HEALTH_WATCHDOG", "0")

# Агент, чей набор обязан оставаться живым зеркалом реестра.
SELF_MAINTAINING_SLUG = "agent-razrabotchik"


def database_url() -> str:
    from shared.db import database_url as shared_url
    return shared_url()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="записать изменения (без флага — только показать)")
    args = parser.parse_args()

    import psycopg
    from psycopg.rows import dict_row

    # Порядок импорта здесь ЗНАЧИМ, и это не стиль. mcp.context_server замораживает
    # OPS_TOOL_NAMES в момент своего импорта, а write_company_sheet регистрируется в реестре
    # позже — при импорте agent_center. Импортируешь agent_center первым — снимок ops
    # получается на один инструмент шире (135 вместо 134), и материализация записала бы
    # агенту возможность, которой у него сейчас нет. Живая служба инструментов импортирует
    # именно в этом порядке, поэтому здесь он повторяется буквально.
    import mcp.context_server as _cs  # noqa: F401,PLC0415 — импорт ради порядка, см. выше

    from agent_center import _agent_tool_names  # noqa: PLC2701 — внутренний резолвер и есть истина

    print(f"Реестр инструментов: {len(_cs.TOOLS)}, операционный пресет: {len(_cs.OPS_TOOL_NAMES)}.")
    print("Ожидается 159 и 134 — как отдаёт живая служба. Другие числа означают, что порядок")
    print("импорта разъехался: остановитесь и разберитесь, не записывая ничего.\n")

    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            # Порядок обязателен: сначала этот скрипт, потом миграция 082. Если колонка режима
            # уже есть, значит миграция прошла — и у не настроенного агента режим уже 'base',
            # то есть резолвер вернёт базовый набор, а не прежний пресет. Записать такой
            # результат значит зафиксировать обнищание вместо того, чтобы его предотвратить.
            cur.execute(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'agents' AND column_name = 'tools_mode'"
            )
            if cur.fetchone():
                print("Колонка tools_mode уже есть — миграция 082 применена, материализация "
                      "больше не нужна и НЕБЕЗОПАСНА. Скрипт ничего не делает.")
                return 0

            cur.execute(
                "SELECT id::text AS id, slug, tier, tools_customized, coalesce(tools, '{}') AS tools "
                "FROM agents ORDER BY slug"
            )
            agents = [dict(r) for r in cur.fetchall()]

        planned: list[tuple[str, list[str]]] = []
        for agent in agents:
            if agent["tools_customized"]:
                continue
            if agent["slug"] == SELF_MAINTAINING_SLUG:
                print(f"  {agent['slug']:26} пропуск — набор обязан обновляться сам (режим max)")
                continue
            effective = sorted(_agent_tool_names(agent))
            planned.append((agent["id"], effective))
            print(f"  {agent['slug']:26} пресет по тиру '{agent['tier']}' → явный список "
                  f"из {len(effective)} инструментов")

        if not planned:
            print("Нечего материализовать: все агенты уже с явным списком.")
            return 0

        if not args.apply:
            print("\nПоказан план. Запустите с --apply, чтобы записать.")
            return 0

        with conn.cursor() as cur:
            for agent_id, tools in planned:
                cur.execute(
                    "UPDATE agents SET tools = %s, tools_customized = TRUE, updated_at = now() "
                    "WHERE id = %s AND NOT tools_customized",
                    (tools, agent_id),
                )
        conn.commit()
        print(f"\nЗаписано агентов: {len(planned)}.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
