"""«Я перезапустился, отправьте запрос заново» must only reach people whose turn really died.

For a month it reached people whose turn was running perfectly: the hourly daily-sync cron
imports b24bot, and import-time process-start routines «recovered» live turns of other users
(11 messages in 30 days — dialogs 14/16/28). The user saw an apology while the real agent kept
working and answered normally.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def bot(app_module, monkeypatch):
    import b24bot

    monkeypatch.delenv("ALBERY_WEB_PROCESS", raising=False)
    return b24bot


def test_startup_routines_do_not_run_outside_the_web_process(bot, monkeypatch):
    """A cron script importing the module must not fire boot recovery."""
    called = []
    monkeypatch.setattr(bot, "_b24_recover_inflight_turns", lambda *a, **k: called.append(1))
    monkeypatch.setattr(bot.threading, "Thread",
                        lambda *a, **k: pytest.fail("startup thread must not start"))

    bot._b24_startup_register_commands()

    assert called == []


def test_startup_routines_run_in_the_web_process(bot, monkeypatch):
    monkeypatch.setenv("ALBERY_WEB_PROCESS", "1")
    started = []

    class FakeThread:
        def __init__(self, target=None, **kw):
            started.append(target)

        def start(self):
            pass

    monkeypatch.setattr(bot.threading, "Thread", FakeThread)
    bot._b24_startup_register_commands()

    assert started, "в веб-процессе стартовые процедуры обязаны запускаться"


def test_web_process_flag_reading(bot, monkeypatch):
    for value in ("1", "true", "YES"):
        monkeypatch.setenv("ALBERY_WEB_PROCESS", value)
        assert bot.web_process_enabled() is True
    for value in ("", "0", "no"):
        monkeypatch.setenv("ALBERY_WEB_PROCESS", value)
        assert bot.web_process_enabled() is False


def test_recovery_only_looks_at_turns_older_than_this_process(bot, monkeypatch):
    """The guard that makes a live turn untouchable even if recovery does run."""
    captured = {}

    class FakeCursor:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, params=None):
            captured["sql"], captured["params"] = " ".join(sql.split()), params
        def fetchall(self): return []

    class FakeConn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def cursor(self): return FakeCursor()
        def transaction(self): return self

    monkeypatch.setattr(bot, "pg_connect", lambda: FakeConn())
    bot._b24_recover_inflight_turns("endpoint", "token")

    assert "started_at < to_timestamp(%s)" in captured["sql"]
    assert captured["params"] == (bot._PROCESS_STARTED_AT,)


def test_a_live_turn_gets_no_apology(bot, monkeypatch):
    """End-to-end shape of the bug: a turn started AFTER this process must be left alone."""
    notified = []
    monkeypatch.setattr(bot, "_albery_bitrix_notify",
                        lambda *a, **k: notified.append(a), raising=False)

    class FakeCursor:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, params=None):
            # The query filters by started_at, so a live (newer) turn is simply not returned.
            self.rows = []
        def fetchall(self): return self.rows

    class FakeConn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def cursor(self): return FakeCursor()
        def transaction(self): return self

    monkeypatch.setattr(bot, "pg_connect", lambda: FakeConn())

    assert bot._b24_recover_inflight_turns("e", "t") == 0
    assert notified == [], "живой ход не должен получать извинения"


def test_run_5002_marks_itself_as_the_web_process():
    """The flag must be set BEFORE app is imported — routines fire at import time."""
    from pathlib import Path

    source = Path(__file__).resolve().parents[2] / "run_5002.py"
    text = source.read_text(encoding="utf-8")
    flag_at = text.index("ALBERY_WEB_PROCESS")
    import_at = text.index("from app import app")
    assert flag_at < import_at, "флаг должен выставляться до импорта app"
