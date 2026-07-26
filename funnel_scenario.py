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
import os
import time
from typing import Any

log = logging.getLogger("funnel-scenario")

_TTL_S = 60.0
_cache: dict[str, Any] = {"at": 0.0, "stages": {}, "funnels": {}}

# Воронка ИУ («Партнёрская программа WB — индивидуальные условия»). То же значение, что в
# context_server: держим их согласованными через одну переменную окружения.
IU_FUNNEL_ID = int(os.getenv("IU_FUNNEL_ID", "16") or 16)


def _funnel_of_stage(stage: str) -> int | None:
    """«C16:UC_ANKETA» → 16. Этап сам говорит, к какой воронке относится."""
    head = str(stage or "").split(":", 1)[0].strip()
    if head.startswith("C") and head[1:].isdigit():
        return int(head[1:])
    return None


def _load(db) -> None:
    """Перечитать настройки. Сбой базы не имеет права влиять на разговор с клиентом."""
    stages: dict[tuple[int, str], dict] = {}
    funnels: dict[int, dict] = {}
    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT funnel_id, stage_id, trigger, need, action, enabled,"
                            " testing, blocked_phrases FROM funnel_scenarios")
                for row in cur.fetchall():
                    fid = int(row["funnel_id"])
                    stage_id = str(row["stage_id"] or "")
                    if not stage_id:
                        # `testing` и `blocked_phrases` читаем мягко: колонки появились
                        # миграциями 067 и 068, и строка из более старого кода (или теста) не
                        # обязана их содержать.
                        funnels[fid] = {
                            "enabled": bool(row["enabled"]),
                            "testing": bool(row.get("testing", False)),
                            "blocked_phrases": str(row.get("blocked_phrases") or ""),
                        }
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
    row = (_cache["funnels"] or {}).get(int(funnel_id))
    return bool(row.get("enabled", True)) if row else True


def testing_mode(db, funnel_id: int) -> bool:
    """Идёт ли тестирование воронки: эскалации уходят в тестовую группу, а не в рабочую.

    По умолчанию — НЕТ. Сбой базы тоже читается как «нет»: молча увести вопросы живых клиентов
    в тестовую группу опаснее, чем лишний раз потревожить рабочую."""
    _fresh(db)
    row = (_cache["funnels"] or {}).get(int(funnel_id))
    return bool(row.get("testing", False)) if row else False


def blocked_phrases(db=None, funnel_id: int | None = None) -> str:
    """Запрещённые фразы воронки, по одной на строку.

    Пусто и при сбое базы: встроенные категории фильтра (брань, политика, jailbreak) работают
    всегда, а список владельца — дополнение к ним, а не замена."""
    if db is None:
        from shared.db import connect

        db = connect
    fid = int(funnel_id if funnel_id is not None else IU_FUNNEL_ID)
    _fresh(db)
    row = (_cache["funnels"] or {}).get(fid)
    return str(row.get("blocked_phrases") or "") if row else ""


def invalidate() -> None:
    """Сбросить кэш — вызывается сразу после сохранения из кабинета."""
    _cache["at"] = 0.0
