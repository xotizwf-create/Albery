"""Метку «ОШИБКА» должно быть можно снять после разбора.

Владелец 20.07.2026: «в UI у любого диалога, где хоть раз была ошибка (таймаут либо обрывание
хода) пишется ОШИБКА и никак не убирается». Счётчик считал ВСЕ неуспешные ходы за всю историю,
поэтому один давний таймаут навсегда помечал переписку проблемной.

Снятие метки требует ссылки на задачу Битрикса, где сбой устранён, — чтобы это было закрытие
по факту работы, а не «замазывание».
"""
from __future__ import annotations

import pytest


@pytest.fixture
def ac(app_module):
    import agent_center

    return agent_center


class _Cur:
    def __init__(self, rows=None, rowcount=0):
        self._rows = rows or []
        self.rowcount = rowcount
        self.sql = ""
        self.params = None

    def __enter__(self): return self
    def __exit__(self, *a): return False

    def execute(self, sql, params=None):
        self.sql = " ".join(sql.split())
        self.params = params

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, cur):
        self._cur = cur

    def __enter__(self): return self
    def __exit__(self, *a): return False
    def cursor(self): return self._cur
    def transaction(self): return self


def test_badge_ignores_resolved_errors(ac, monkeypatch):
    """Ядро правки: разобранные сбои не должны держать метку."""
    cur = _Cur(rows=[])
    monkeypatch.setattr(ac, "pg_connect", lambda: _Conn(cur))

    ac._dialog_error_counts()

    assert "error_resolved_at IS NULL" in cur.sql
    assert "status <> 'ok'" in cur.sql


def test_resolution_requires_a_dialog(ac):
    with pytest.raises(ValueError):
        ac.resolve_dialog_errors(dialog_id="", task_id=1820)


def test_resolution_records_the_task_number(ac, monkeypatch):
    cur = _Cur(rowcount=3)
    monkeypatch.setattr(ac, "pg_connect", lambda: _Conn(cur))

    n = ac.resolve_dialog_errors(dialog_id="16", task_id=1820, by="владелец")

    assert n == 3
    assert "error_resolved_at = now()" in cur.sql
    assert 1820 in cur.params, "номер задачи обязан сохраниться"
    assert "владелец" in cur.params


def test_resolution_touches_only_unresolved_errors(ac, monkeypatch):
    """Повторное снятие не должно переписывать историю прошлого разбора."""
    cur = _Cur(rowcount=0)
    monkeypatch.setattr(ac, "pg_connect", lambda: _Conn(cur))

    ac.resolve_dialog_errors(dialog_id="16", task_id=1820)

    assert "error_resolved_at IS NULL" in cur.sql
    assert "status <> 'ok'" in cur.sql


def test_resolution_can_target_one_interaction(ac, monkeypatch):
    cur = _Cur(rowcount=1)
    monkeypatch.setattr(ac, "pg_connect", lambda: _Conn(cur))

    ac.resolve_dialog_errors(dialog_id="", interaction_id=1088, task_id=1820)

    assert "id = %s" in cur.sql
    assert 1088 in cur.params


def test_main_agent_slug_maps_to_null(ac, monkeypatch):
    """У главного агента agent_slug в базе NULL — фильтр обязан это учитывать."""
    cur = _Cur(rowcount=1)
    monkeypatch.setattr(ac, "pg_connect", lambda: _Conn(cur))

    ac.resolve_dialog_errors(dialog_id="16", agent_slug="main", task_id=1)

    assert "agent_slug IS NULL" in cur.sql


def test_listing_hides_resolved_by_default(ac, monkeypatch):
    cur = _Cur(rows=[])
    monkeypatch.setattr(ac, "pg_connect", lambda: _Conn(cur))

    ac.list_dialog_errors(dialog_id="16")

    assert "error_resolved_at IS NULL" in cur.sql


def test_listing_can_include_resolved_history(ac, monkeypatch):
    cur = _Cur(rows=[])
    monkeypatch.setattr(ac, "pg_connect", lambda: _Conn(cur))

    ac.list_dialog_errors(dialog_id="16", include_resolved=True)

    assert "error_resolved_at IS NULL" not in cur.sql, "история разбора должна быть видна"


def test_mcp_tools_are_registered_and_documented(ctx):
    for name in ("list_dialog_errors", "resolve_dialog_errors"):
        spec = ctx.TOOLS[name]
        assert callable(spec["handler"])
        assert spec["description"].strip()
    resolve = ctx.TOOLS["resolve_dialog_errors"]
    assert "task_id" in resolve["inputSchema"]["properties"]
    assert "ОБЯЗАТЕЛЬНО" in resolve["description"], "агент должен знать про обязательную ссылку на задачу"


def test_error_tools_are_not_public(ctx):
    """Разбор сбоев — служебная работа, не для публичных коннекторов."""
    assert "list_dialog_errors" in ctx.OWNER_ONLY_TOOL_NAMES
    assert "resolve_dialog_errors" in ctx.OWNER_ONLY_TOOL_NAMES


def test_resolve_tool_demands_a_task_or_note(ctx, monkeypatch):
    with pytest.raises(ctx.McpError):
        ctx.tool_resolve_dialog_errors({"dialog_id": "16"})


def test_resolve_tool_rejects_empty_target(ctx):
    with pytest.raises(ctx.McpError):
        ctx.tool_resolve_dialog_errors({"task_id": 1820})
