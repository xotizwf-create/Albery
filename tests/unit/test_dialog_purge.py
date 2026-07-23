"""Удаление переписки из журнала (владелец, 23.07.2026).

Операция необратима: журнал — единственное место, где эти сообщения хранятся. Поэтому здесь
проверяется не только «удалилось», но и защита от случайного удаления.
"""
from __future__ import annotations

import contextlib

import pytest


class _Cur:
    """Курсор-заглушка: запоминает SQL и параметры, отдаёт счётчик как настоящая выборка."""

    def __init__(self, log, count=3):
        self.log = log
        self._count = count

    def execute(self, sql, params=None):
        self.log.append((" ".join(str(sql).split()), list(params or [])))

    def fetchone(self):
        return {"n": self._count, "first_at": "2026-07-23 10:00:00",
                "last_at": "2026-07-23 12:00:00"}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


@pytest.fixture
def sql_log(monkeypatch):
    log = []

    class _Conn:
        def cursor(self):
            return _Cur(log)

    @contextlib.contextmanager
    def fake_connect():
        yield _Conn()

    import agent_center

    monkeypatch.setattr(agent_center, "pg_connect", fake_connect)
    return log


def test_username_is_matched_case_insensitively(sql_log):
    """@AlexxandRN и @alexxandrn — один и тот же человек."""
    from agent_center import purge_dialog

    res = purge_dialog("telegram", username="@AlexxandRN")

    delete = [s for s, _ in sql_log if s.startswith("DELETE")]
    assert delete and "lower(username) = %s" in delete[0]
    assert sql_log[-1][1] == ["alexxandrn"], "@ снят, регистр приведён"
    assert res["deleted"] == 3


def test_deleting_by_id_and_username_covers_old_rows(sql_log):
    """В старых записях username мог быть пустым — по одному id их не достать."""
    from agent_center import purge_dialog

    purge_dialog("telegram", dialog_id="1451982360", username="alexxandrn")

    delete = [s for s, _ in sql_log if s.startswith("DELETE")][0]
    assert "dialog_id = %s OR lower(username) = %s" in delete


def test_nothing_is_deleted_without_a_target():
    """Пустой запрос не должен вычистить весь журнал."""
    from agent_center import purge_dialog

    with pytest.raises(ValueError, match="dialog_id или username"):
        purge_dialog("telegram")


def test_unknown_channel_is_refused():
    from agent_center import purge_dialog

    with pytest.raises(ValueError, match="канал"):
        purge_dialog("whatsapp", dialog_id="1")


def test_count_is_taken_before_delete(sql_log):
    """Считать после удаления — значит всегда возвращать ноль."""
    from agent_center import purge_dialog

    purge_dialog("telegram", dialog_id="555")

    kinds = [s.split()[0] for s, _ in sql_log]
    assert kinds == ["SELECT", "DELETE"]


def test_mcp_tool_requires_confirmation():
    """Инструмент не должен удалять переписку с первого же вызова агента."""
    from mcp.context_server import McpError, tool_delete_dialog

    with pytest.raises(McpError, match="confirm"):
        tool_delete_dialog({"username": "alexxandrn"})
