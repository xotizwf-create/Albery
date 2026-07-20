"""The daily overdue report must be answerable from the tool registry.

Automation #36 («Отчёт по новым просроченным задачам», созданная Натальей Горюновой) failed
on 20.07.2026 with «доступные данные не содержат истории первого перехода задач в просрочку»:
search_tasks filters by activity date and has no notion of closure, so the question was not
expressible. A deadline IS the moment a task becomes overdue, so the query is deterministic.
"""
from __future__ import annotations

import pytest


def test_tool_is_registered_and_documented(ctx):
    spec = ctx.TOOLS["list_overdue_tasks"]
    assert spec["handler"] is ctx.tool_list_overdue_tasks
    desc = spec["description"]
    # The agent has to know an empty result is an answer, not missing data.
    assert "просроченных нет" in desc
    props = spec["inputSchema"]["properties"]
    assert {"became_overdue_from", "became_overdue_to", "only_open"} <= set(props)


def test_exposed_in_the_core_toolset(ctx):
    """A daily automation must reach it without a second-stage tool load."""
    assert "list_overdue_tasks" in ctx.CORE_TOOL_NAMES
    assert "list_overdue_tasks" in ctx.OPS_TOOL_NAMES


def _run(ctx, monkeypatch, rows, args):
    captured = {}

    class FakeCursor:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, params=None):
            captured["sql"], captured["params"] = " ".join(sql.split()), params
        def fetchall(self): return [dict(r) for r in rows]

    class FakeConn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def cursor(self): return FakeCursor()

    monkeypatch.setattr(ctx, "connect", lambda: FakeConn())
    monkeypatch.setattr(ctx, "_task_deep_link", lambda tid: f"https://b24/task/{tid}")
    return ctx.tool_list_overdue_tasks(args), captured


def test_empty_result_is_a_confident_answer(ctx, monkeypatch):
    """17.07.2026 really had zero still-overdue tasks — the report must say so plainly."""
    out, _ = _run(ctx, monkeypatch, [], {"became_overdue_from": "2026-07-17",
                                         "became_overdue_to": "2026-07-17"})
    assert out["total"] == 0
    assert out["items"] == []
    assert "просроченных задач нет" in out["display_rule"]


def test_groups_by_responsible_and_links_tasks(ctx, monkeypatch):
    rows = [
        {"bitrix_task_id": 1222, "title": "Итоги созвона", "status": "pending",
         "responsible_name": "Евгений Палей", "days_overdue": 11},
        {"bitrix_task_id": 1274, "title": "Согласование", "status": "pending",
         "responsible_name": "Евгений Палей", "days_overdue": 6},
        {"bitrix_task_id": 1432, "title": "Итоги созвона", "status": "pending",
         "responsible_name": "Анастасия Клеблеева", "days_overdue": 6},
    ]
    out, _ = _run(ctx, monkeypatch, rows, {})

    assert out["total"] == 3
    assert out["items"][0]["task_url"] == "https://b24/task/1222"
    groups = {g["responsible_name"]: g for g in out["grouped_by_responsible"]}
    assert groups["Евгений Палей"]["count"] == 2
    assert groups["Евгений Палей"]["bitrix_task_ids"] == [1222, 1274]
    # Busiest owner first, so the report leads with who is most behind.
    assert out["grouped_by_responsible"][0]["responsible_name"] == "Евгений Палей"


def test_only_open_excludes_closed_tasks_by_default(ctx, monkeypatch):
    _, cap = _run(ctx, monkeypatch, [], {})
    assert "t.closed_at_bitrix IS NULL" in cap["sql"]
    assert list(ctx._CLOSED_TASK_STATUSES) in cap["params"]


def test_only_open_false_includes_closed(ctx, monkeypatch):
    _, cap = _run(ctx, monkeypatch, [], {"only_open": False})
    assert "closed_at_bitrix IS NULL" not in cap["sql"]


def test_filters_on_deadline_not_activity_date(ctx, monkeypatch):
    """The bug was search_tasks filtering by updated_at — the deadline is what matters."""
    _, cap = _run(ctx, monkeypatch, [], {"became_overdue_from": "2026-07-17"})
    assert "deadline_at" in cap["sql"]
    assert "updated_at_bitrix" not in cap["sql"]


def test_responsible_filter_is_applied(ctx, monkeypatch):
    _, cap = _run(ctx, monkeypatch, [], {"responsible_bitrix_user_id": 30})
    assert "t.responsible_bitrix_user_id = %s" in cap["sql"]
    assert 30 in cap["params"]
