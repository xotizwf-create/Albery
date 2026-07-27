"""Автоотправка задач по созвонам и запрет молчания при сбое.

Инцидент 27.07.2026: задачи по созвонам не уходили, и система об этом не сказала ни слова —
единственный канал согласования (Telegram) был же и единственным каналом тревоги. Тесты
закрепляют новое поведение: отправка идёт сама, а любой сбой становится задачей в Битриксе.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

import zoom_dispatch_watch as watch


NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        self.executed.append((" ".join(str(sql).split()), params))

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, rows):
        self.cursor_instance = FakeCursor(rows)

    def cursor(self):
        return self.cursor_instance


def connect_factory(rows):
    connection = FakeConnection(rows)

    @contextmanager
    def connect():
        yield connection

    return connect, connection


def call_row(**overrides):
    row = {
        "call_id": "3cf9bc47-c12b-4b6e-bce9-1169c6d57a41",
        "start_time_msk": datetime(2026, 7, 27, 10, 1, tzinfo=timezone.utc),
        "topic": "Зал персональной конференции Координатор",
        "tasks_count": 2,
        "alerted": False,
    }
    row.update(overrides)
    return row


def test_nothing_happens_while_the_switch_is_off(monkeypatch):
    """Выкатка не должна сама по себе начать рассылать задачи людям."""
    monkeypatch.delenv("ZOOM_AUTO_DISPATCH_ENABLED", raising=False)

    def refuse(_call_id):
        raise AssertionError("отправка при выключенном тумблере недопустима")

    result = watch.run_once(dispatch=refuse)

    assert result["enabled"] is False
    assert result["sent"] == []


def test_ready_call_is_dispatched_without_any_messenger(monkeypatch):
    """Согласование в Telegram больше не требуется — Албери отправляет сам."""
    monkeypatch.setenv("ZOOM_AUTO_DISPATCH_ENABLED", "1")
    connect, _connection = connect_factory([call_row()])
    dispatched = []

    result = watch.run_once(
        now=NOW,
        connect=connect,
        dispatch=dispatched.append,
        alert=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("тревоги быть не должно")),
    )

    assert dispatched == ["3cf9bc47-c12b-4b6e-bce9-1169c6d57a41"]
    assert result["sent"] == dispatched
    assert result["failed"] == []


def test_a_failed_dispatch_becomes_a_bitrix_task_not_silence(monkeypatch):
    """Главное правило: о потерянных задачах обязаны узнать, и не через сломанный канал."""
    monkeypatch.setenv("ZOOM_AUTO_DISPATCH_ENABLED", "1")
    connect, _connection = connect_factory([call_row()])
    alerts = []
    marked = []

    def boom(_call_id):
        raise ValueError("Не удалось собрать ни одного получателя по созвону")

    result = watch.run_once(
        now=NOW,
        connect=connect,
        dispatch=boom,
        alert=lambda **kwargs: alerts.append(kwargs),
        mark_alerted=lambda call_id, reason: marked.append(call_id),
    )

    assert result["sent"] == []
    assert [item["call_id"] for item in result["failed"]] == [
        "3cf9bc47-c12b-4b6e-bce9-1169c6d57a41"
    ]
    assert len(alerts) == 1
    assert "получателя" in alerts[0]["reason"]
    assert marked == ["3cf9bc47-c12b-4b6e-bce9-1169c6d57a41"]


def test_the_same_call_never_alerts_twice(monkeypatch):
    """Повтор каждые пять минут превратил бы задачи владельца в свалку."""
    monkeypatch.setenv("ZOOM_AUTO_DISPATCH_ENABLED", "1")
    connect, _connection = connect_factory([call_row(alerted=True)])
    alerts = []

    def boom(_call_id):
        raise ValueError("та же самая причина")

    result = watch.run_once(
        now=NOW,
        connect=connect,
        dispatch=boom,
        alert=lambda **kwargs: alerts.append(kwargs),
    )

    assert result["failed"], "повторная попытка отправки обязана состояться"
    assert alerts == [], "а вот вторая тревога по тому же созвону — нет"


def test_one_broken_call_does_not_stop_the_rest(monkeypatch):
    monkeypatch.setenv("ZOOM_AUTO_DISPATCH_ENABLED", "1")
    rows = [call_row(call_id="broken"), call_row(call_id="healthy")]
    connect, _connection = connect_factory(rows)
    sent = []

    def dispatch(call_id):
        if call_id == "broken":
            raise ValueError("портал недоступен")
        sent.append(call_id)

    result = watch.run_once(
        now=NOW, connect=connect, dispatch=dispatch,
        alert=lambda **_kwargs: None, mark_alerted=lambda *_args: None,
    )

    assert sent == ["healthy"]
    assert [item["call_id"] for item in result["failed"]] == ["broken"]


def test_old_calls_are_not_fired_at_people_when_automation_is_switched_on(monkeypatch):
    """Включение автоматики не должно выстрелить хвостом задач по давним встречам."""
    monkeypatch.setenv("ZOOM_AUTO_DISPATCH_ENABLED", "1")
    monkeypatch.setenv("ZOOM_AUTO_DISPATCH_MAX_AGE_HOURS", "24")
    connect, connection = connect_factory([])

    watch.pending_calls(now=NOW, connect=connect)

    sql, params = connection.cursor_instance.executed[0]
    assert "start_time_msk >= %s" in sql
    assert params[0] == NOW - timedelta(hours=24)
    # Уже отправленное и созвоны без задач не берутся вовсе.
    assert "'bitrix_dispatch') IS NULL" in sql
    assert "operational_tasks" in sql


def test_alert_text_says_what_happened_and_what_to_do():
    payload = watch.build_alert(call_row(), "Не удалось собрать ни одного получателя")

    assert "не ушли" in payload["title"]
    assert "27.07.2026 10:01" in payload["title"]
    assert "Не удалось собрать ни одного получателя" in payload["description"]
    assert "Отправка задач" in payload["description"]
    assert payload["result_criteria"]
