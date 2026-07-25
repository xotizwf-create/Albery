"""Настраиваемый сценарий воронки: шаги этапов из базы + выключатель агента.

Владелец 25.07.2026: «сделаем инструмент „Работа с воронками“, внутри можно выбрать воронку и
сценарий настраивать, чтобы этим можно было прям управлять».

Разделение ответственности, из-за нарушения которого мы уже трижды ломали поведение за сутки:
  • УСЛОВИЯ и ПРИОРИТЕТЫ правил остаются в коде (`funnel_rules`) — они завязаны на факты, и
    каждое закрыто тестом; править их мышкой значит снова получить расползание;
  • ТЕКСТ шага (чего агент ждёт и что делает) владелец правит сам: это то, что уходит в промпт,
    и здесь он разбирается лучше — он знает, как разговаривать со своими клиентами.

Читается на каждом ходу, поэтому кэш на минуту и полная устойчивость к сбою базы: нет настройки
или БД недоступна — работает сценарий из кода.
"""
from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger("funnel-scenario")

_TTL_S = 60.0
_cache: dict[str, Any] = {"at": 0.0, "stages": {}, "funnels": {}}


def _funnel_of_stage(stage: str) -> int | None:
    """«C16:UC_ANKETA» → 16. Этап сам говорит, к какой воронке относится."""
    head = str(stage or "").split(":", 1)[0].strip()
    if head.startswith("C") and head[1:].isdigit():
        return int(head[1:])
    return None


def _load(db) -> None:
    """Перечитать настройки. Сбой базы не имеет права влиять на разговор с клиентом."""
    stages: dict[tuple[int, str], dict] = {}
    funnels: dict[int, bool] = {}
    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT funnel_id, stage_id, trigger, need, action, enabled"
                            " FROM funnel_scenarios")
                for row in cur.fetchall():
                    fid = int(row["funnel_id"])
                    stage_id = str(row["stage_id"] or "")
                    if not stage_id:
                        funnels[fid] = bool(row["enabled"])
                        continue
                    stages[(fid, stage_id)] = {
                        "trigger": str(row["trigger"] or ""),
                        "need": str(row["need"] or ""),
                        "action": str(row["action"] or ""),
                    }
    except Exception:  # noqa: BLE001 — работаем по сценарию из кода
        log.warning("настройки воронок не прочитаны — сценарий из кода", exc_info=True)
        return
    _cache.update({"at": time.time(), "stages": stages, "funnels": funnels})


def _fresh(db) -> None:
    if time.time() - float(_cache["at"] or 0) > _TTL_S:
        _load(db)


def step_override(db, stage: str) -> dict:
    """Настроенный владельцем шаг этапа. Пустой словарь — используем сценарий из кода."""
    funnel_id = _funnel_of_stage(stage)
    if funnel_id is None:
        return {}
    _fresh(db)
    row = (_cache["stages"] or {}).get((funnel_id, str(stage)))
    if not row:
        return {}
    # Пустые поля не считаются настройкой: владелец мог заполнить только «что делает».
    return {k: v for k, v in row.items() if str(v).strip()}


def agent_enabled(db, funnel_id: int) -> bool:
    """Работает ли агент на этой воронке. По умолчанию — да (как было до инструмента)."""
    _fresh(db)
    return bool((_cache["funnels"] or {}).get(int(funnel_id), True))


def invalidate() -> None:
    """Сбросить кэш — вызывается сразу после сохранения из кабинета."""
    _cache["at"] = 0.0
