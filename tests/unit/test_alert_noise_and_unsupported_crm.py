"""Тревога обязана гаснуть сама, а невыполнимую работу нельзя ставить в очередь.

Разобрано 20.08.2026 по жалобе владельца («постоянно в группу уведомления приходят ошибки»).

19.08 канал Авито за вечер положил 75 задач `ensure_deal` в dead_letter: CRM-адаптер принимает
только Telegram и отбивает чужой канал по построению. Монитор считал мёртвые строки ЗА ВСЁ
ВРЕМЯ — а из терминального состояния строка не уходит никогда, — поэтому один разобранный
вечер приходил в «Уведомления» как КРИТИЧНО каждые 6 часов и не мог перестать.

Две стороны одной поломки закреплены здесь:
* монитор сообщает о мёртвых строках ОДИН раз — по границе показанного, а не по счётчику;
* задача, которую адаптер заведомо отобьёт, вообще не ставится.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime

import pytest

import funnel_workspace_store as workspace_store
from scripts.workspace_queue_health import (
    TERMINAL_QUEUES,
    inspect_terminal_queues,
    inspect_workspace_queue_health,
    unreported_terminal_queues,
)


def _connect_with_row(row, seen_sql=None):
    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, params=None):
            if seen_sql is not None:
                seen_sql.append(sql)

        def fetchone(self):
            return row

    class Connection:
        def cursor(self):
            return Cursor()

    @contextmanager
    def connect():
        yield Connection()

    return connect


def test_live_probe_no_longer_counts_terminal_states():
    """Счётчик, который не может обнулиться, не имеет права быть «текущей проблемой»."""
    sql: list[str] = []
    problems = inspect_workspace_queue_health(
        connect_factory=_connect_with_row({"crm_dead": 75, "outbox_failed": 4}, sql),
        now=datetime(2026, 8, 20, 8, 0),
    )
    assert problems == []
    assert "dead_letter" not in sql[0]
    assert "delivery_status = 'failed'" not in sql[0]


def test_terminal_queues_report_the_boundary_not_the_pile():
    marks = inspect_terminal_queues(
        connect_factory=_connect_with_row(
            {"update_dead": 0, "ai_failed": 0, "outbox_failed": 511, "crm_dead": 396}
        ),
    )
    assert marks == {"update_dead": 0, "ai_failed": 0, "outbox_failed": 511, "crm_dead": 396}
    assert set(marks) == set(TERMINAL_QUEUES)


def test_the_avito_evening_is_reported_once_and_then_stays_quiet():
    """Ровно жалоба владельца: 75 мёртвых CRM-задач и 4 недоставленных за вечер 19.08."""
    marks = {"update_dead": 0, "ai_failed": 0, "outbox_failed": 511, "crm_dead": 396}

    first = unreported_terminal_queues(marks, {})
    assert first == [
        "неотправленные сообщения — появились новые, разобрать",
        "CRM-задачи, упавшие насмерть — появились новые, разобрать",
    ]

    # Монитор показал их человеку и сдвинул границу — больше об этом же не напоминают.
    assert unreported_terminal_queues(marks, marks) == []


def test_a_new_failure_after_the_boundary_still_wakes_the_owner():
    seen = {"update_dead": 0, "ai_failed": 0, "outbox_failed": 511, "crm_dead": 396}
    later = dict(seen, crm_dead=397)
    assert unreported_terminal_queues(later, seen) == [
        "CRM-задачи, упавшие насмерть — появились новые, разобрать",
    ]


def test_selfcheck_moves_the_boundary_only_after_the_alert_is_delivered():
    source = (workspace_store.__file__.rsplit("funnel_workspace_store.py", 1)[0]
              + "scripts/albery_selfcheck.py")
    with open(source, encoding="utf-8") as fh:
        text = fh.read()
    sent = text.split('state["last_alert_ts"] = time.time()', 1)[1]
    assert 'state["terminal_queues_seen"] = terminal_marks' in sent.split("if should_notify:", 1)[0]


def test_avito_egress_is_watched_only_while_the_workday_runs():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "albery_selfcheck_probe",
        workspace_store.__file__.rsplit("funnel_workspace_store.py", 1)[0]
        + "scripts/albery_selfcheck.py",
    )
    # Модуль-скрипт выполняет проверки на импорте, поэтому берём только исходник функции.
    with open(spec.origin, encoding="utf-8") as fh:
        text = fh.read()
    namespace: dict = {"datetime": datetime, "AVITO_WATCH_HOURS": (9, 19)}
    body = text.split("def avito_egress_expected(", 1)[1].split("\n\n\n", 1)[0]
    exec("def avito_egress_expected(" + body, namespace)  # noqa: S102
    expected = namespace["avito_egress_expected"]

    assert expected(datetime(2026, 8, 20, 11, 0)) is True    # среда, рабочий день
    assert expected(datetime(2026, 8, 20, 3, 0)) is False    # ночь — компьютер выключен
    assert expected(datetime(2026, 8, 20, 22, 0)) is False   # вечер
    assert expected(datetime(2026, 8, 22, 12, 0)) is False   # суббота


class _RecordingCursor:
    """Курсор-протокол: запоминает запросы, на INSERT … RETURNING отдаёт готовую строку."""

    def __init__(self):
        self.inserts: list[tuple[str, tuple]] = []
        self._returning = False

    def execute(self, sql, params=None):
        self.inserts.append((sql, params))
        self._returning = "RETURNING" in sql

    def fetchone(self):
        return {"id": 1, "action_type": "ensure_deal"} if self._returning else None

    def fetchall(self):
        return []


def test_unsupported_channel_never_gets_a_crm_action():
    cur = _RecordingCursor()
    action = workspace_store._enqueue_ensure_deal_action_cursor(
        cur, conversation_id=101, message_id=202, source_key="avito",
    )
    assert action is None
    assert cur.inserts == [], "задача, которую адаптер отобьёт, не должна попадать в очередь"


@pytest.mark.parametrize("source", sorted(workspace_store.CRM_LINKED_SOURCES))
def test_telegram_channels_still_get_their_crm_action(source):
    cur = _RecordingCursor()
    workspace_store._enqueue_ensure_deal_action_cursor(
        cur, conversation_id=101, message_id=202, source_key=source,
    )
    assert cur.inserts, f"канал {source} обязан по-прежнему связываться со сделкой"
    assert "funnel_workspace_crm_actions" in cur.inserts[0][0]


def test_backfill_cannot_resurrect_actions_for_unsupported_channels():
    cur = _RecordingCursor()
    workspace_store._backfill_missing_deal_actions_cursor(cur, 50)
    sql, params = cur.inserts[0]
    assert "c.source_key = ANY(%s)" in sql
    assert params[0] == sorted(workspace_store.CRM_LINKED_SOURCES)


def test_adapter_and_queue_agree_on_the_supported_channels():
    """Расхождение этих двух списков и есть механизм появления мёртвых задач."""
    import funnel_workspace_crm

    source = funnel_workspace_crm.__file__.replace(".pyc", ".py")
    with open(source, encoding="utf-8") as fh:
        text = fh.read()
    assert "workspace_store.CRM_LINKED_SOURCES" in text
