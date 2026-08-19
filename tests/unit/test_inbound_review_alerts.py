"""Тревога о зависшем ходе обязана различать «человек без ответа» и «запись без движения».

19.08.2026 владелец: «А что значит эта ошибка? Она приходит и приходит». Приходило
«КРИТИЧНО: Bitrix inbound review: 1» — 8 раз за сутки про ОДИН ход. Разбор: Софья спросила
про повторяющиеся задачи в 09:22 и получила ответ в 09:23. Ход при этом не уложился в
аренду и был помечен «исход неизвестен» — намеренно, чтобы не переиграть его вслепую и не
отправить второй ответ.

Но дальше запись не двигалась никогда, а сторож видел её вечно. Тревога была верной и
бесполезной одновременно: сделать по ней нечего, а привыкание к ней обесценивает следующую
настоящую.

Двусмысленность снимается фактом: ушёл ли в тот же диалог ответ бота.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bitrix_inbound


def _health(rows):
    """inspect_health с подставленным запросом — без похода в базу."""
    return bitrix_inbound.inspect_health(
        now=datetime.now(timezone.utc),
        query=lambda sql, params: rows,
    )


def test_unanswered_turn_is_reported_with_what_to_do():
    """«Bitrix inbound review: 1» не говорит ни что случилось, ни что делать."""
    problems = _health([{"status": "review", "n": 1, "oldest": None}])

    assert len(problems) == 1
    text = problems[0]
    assert "без ответа" in text, "тревога обязана называть суть"
    assert "вручную" in text, "тревога обязана называть действие"


def test_failed_jobs_are_described_too():
    problems = _health([{"status": "failed", "n": 2, "oldest": None}])

    assert "сорвалась" in problems[0]
    assert "2" in problems[0]


def test_clean_queue_is_silent():
    assert _health([]) == []


def test_overdue_stage_still_reported():
    """Застрявший этап — по-прежнему проблема, эту защиту не теряем."""
    old = datetime.now(timezone.utc) - timedelta(minutes=30)
    problems = _health([{"status": "brain_running", "n": 1, "oldest": old}])

    assert problems and "brain_running" in problems[0]


def test_fresh_stage_is_not_reported():
    fresh = datetime.now(timezone.utc) - timedelta(seconds=10)
    assert _health([{"status": "brain_running", "n": 1, "oldest": fresh}]) == []


def test_resolver_closes_only_answered_turns(monkeypatch):
    """Ключевое: закрываем по ФАКТУ доставленного ответа, а не «чтобы не мешало»."""
    executed = {}

    class _Cur:
        rowcount = 1

        def execute(self, sql, *a):
            executed["sql"] = " ".join(sql.split())

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Conn:
        def cursor(self):
            return _Cur()

        def transaction(self):
            return _Cur()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(bitrix_inbound, "_db", lambda: _Conn())
    closed = bitrix_inbound.resolve_answered_reviews()

    assert closed == 1
    sql = executed["sql"]
    assert "status='review'" in sql, "трогаем только неоднозначные"
    assert "direction='out'" in sql, "доказательство — исходящий ответ"
    assert "dialog_id" in sql, "ответ должен быть в ТОМ ЖЕ диалоге"


def test_resolver_failure_does_not_break_monitoring(monkeypatch):
    """Сторож не должен умолкать из-за того, что не смог прибраться."""
    def boom():
        raise RuntimeError("база недоступна")

    monkeypatch.setattr(bitrix_inbound, "resolve_answered_reviews", boom)
    monkeypatch.setattr(bitrix_inbound, "_db", lambda: (_ for _ in ()).throw(RuntimeError("нет базы")))

    # inspect_health сам ходит в базу, поэтому проверяем только, что уборка не роняет вызов
    try:
        bitrix_inbound.inspect_health()
    except RuntimeError as exc:
        assert "нет базы" in str(exc), "упало на запросе, а не на уборке"
