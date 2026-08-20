"""Мониторинг обязан замечать пропажу выхода в Авито.

Выход в Авито — браузер с живой сессией на машине владельца: датацентровый адрес прода Авито
не пускает. Значит «жив компьютер — жив канал», и до 19.08.2026 обратное было НЕВИДИМО:
зеркало переставало приносить переписку, ответы копились в очереди, а мониторинг молчал.
Молчащий канал не должен выглядеть как «новых сообщений нет».
"""
from __future__ import annotations

import re
from pathlib import Path

SELFCHECK = Path("scripts/albery_selfcheck.py")


def _source() -> str:
    return SELFCHECK.read_text(encoding="utf-8")


def _avito_block() -> str:
    source = _source()
    return source.split("Канал Авито: жив ли выход", 1)[1].split("# --- PostgreSQL backup", 1)[0]


def test_a_missing_egress_is_noticed():
    """Воркер отмечается на каждом обходе; полчаса тишины = выхода нет."""
    block = _avito_block()
    assert "avito_accounts" in block, "состояние выхода живёт в avito_accounts"
    assert "session_checked_at" in block, "молчание меряется временем последней отметки"
    assert "AVITO_SILENT_AFTER_MIN" in block


def test_a_dead_session_is_noticed():
    assert "session_status" in _avito_block(), "просроченная сессия — тоже пропавший выход"


def test_a_stuck_queue_is_noticed():
    """Ответы, которые никто не забирает, — вторая половина той же поломки."""
    block = _avito_block()
    assert "funnel_workspace_outbox" in block
    assert "'avito'" in block and "pending" in block


def test_the_watchdog_is_silent_while_the_channel_is_switched_off():
    """Пока канал выключен, его нечему ломаться — алерт был бы шумом."""
    block = _avito_block()
    assert 'AVITO_CHANNEL_ENABLED' in block, "проверка обязана быть под рубильником канала"


def test_the_alert_says_what_to_do():
    """Алерт без действия заставляет владельца вспоминать команду — это не помощь."""
    block = _avito_block()
    assert "avito_worker.py" in block


def test_the_threshold_is_forgiving_enough_for_a_reboot():
    source = _source()
    minutes = int(re.search(r"AVITO_SILENT_AFTER_MIN\s*=\s*(\d+)", source).group(1))
    assert minutes >= 15, "перезагрузка компьютера не должна будить владельца"


def test_missing_egress_is_an_incident_only_while_the_workday_runs():
    """Ночью и в выходные компьютер владельца выключен — это норма, а не авария.

    Решение владельца 20.08.2026: будни 9–19 МСК, молчание дольше двух часов. До этого
    каждое утро начиналось с КРИТИЧНО о том, что все и так знают."""
    assert "avito_egress_expected()" in _avito_block()
    minutes = int(re.search(r"AVITO_SILENT_AFTER_MIN\s*=\s*(\d+)", _source()).group(1))
    assert minutes == 120
