from __future__ import annotations

# Перезапуск сервиса во время автоматизации: запуск обрывается, и его состояние
# обязано стать честным — «прервана», а не вечное «выполняется».

from contextlib import contextmanager
from datetime import timedelta


def _aa():
    """Модуль автоматизаций в боевом порядке импорта.

    Первым всегда поднимается app (так стартует сервис); если начать с
    agent_automations, встречный импорт agent_center поймает его недособранным.
    """

    import app  # noqa: F401
    import agent_automations

    return agent_automations


class _Cursor:
    def __init__(self, log, rows):
        self.log = log
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        self.log.append((" ".join(str(sql).split()), params))

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, log, rows):
        self.cursor_instance = _Cursor(log, rows)

    def cursor(self):
        return self.cursor_instance

    @contextmanager
    def transaction(self):
        yield


def _fake_pg(monkeypatch, aa, rows):
    log: list[tuple[str, object]] = []

    @contextmanager
    def connect():
        yield _Connection(log, rows)

    monkeypatch.setattr(aa, "pg_connect", connect)
    return log


def test_run_killed_by_a_restart_is_marked_interrupted(monkeypatch):
    aa = _aa()
    log = _fake_pg(monkeypatch, aa, [{"id": 2, "name": "Ежедневный отчёт собственнику"}])

    recovered = aa._recover_interrupted_runs()

    assert recovered == 1
    statement, params = log[0]
    assert statement.startswith("UPDATE agent_automations SET last_status = 'interrupted'")
    # Чинятся только зависшие запуски: живой, начавшийся минуту назад, не трогаем.
    assert "last_status = 'running'" in statement and "last_run_at <" in statement
    cutoff = params[-1]
    assert (aa.msk_now() - cutoff).total_seconds() >= aa._RUNNING_STALE_S
    # Причина остаётся в записи: владелец должен видеть, почему отчёт не пришёл.
    assert any("перезапуск" in str(value).lower() for value in params)


def test_recovery_keeps_an_existing_error_message(monkeypatch):
    aa = _aa()
    log = _fake_pg(monkeypatch, aa, [])

    aa._recover_interrupted_runs()

    statement, _ = log[0]
    assert "COALESCE" in statement, "прежняя причина сбоя не должна затираться"


def test_recovery_failure_never_blocks_the_scheduler(monkeypatch):
    aa = _aa()

    @contextmanager
    def broken():
        raise RuntimeError("база недоступна")
        yield  # pragma: no cover

    monkeypatch.setattr(aa, "pg_connect", broken)

    assert aa._recover_interrupted_runs() == 0


def test_stale_running_is_shown_as_interrupted_in_the_panel():
    aa = _aa()

    fresh = {"last_status": "running", "last_run_at": aa.msk_now() - timedelta(seconds=60)}
    stale = {
        "last_status": "running",
        "last_run_at": aa.msk_now() - timedelta(seconds=aa._RUNNING_STALE_S + 60),
    }

    assert aa._running_is_stale(fresh) is False
    assert aa._running_is_stale(stale) is True
