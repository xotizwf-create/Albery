"""Агент правит СВОЮ автоматизацию из диалога.

Живой случай (18.08.2026): владелец попросил починить автоматизацию «Цены Wildberries»,
которая записала не все строки таблицы, — агент ответил «в текущем доступе есть просмотр,
но нет редактирования её сценария». Инструмента правки действительно не было: единственная
запись существующей строки жила в schedule_my_automation и била по `created_by = 'self'`,
поэтому автоматизацию, заведённую владельцем в приложении, агент изменить не мог.
"""
from __future__ import annotations

import importlib

import pytest


class _FakeCursor:
    def __init__(self, sink: list[tuple[str, tuple]]):
        self._sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, sql: str, args: tuple = ()):  # noqa: D401
        self._sink.append((" ".join(sql.split()), args))

    def fetchone(self):
        return {"id": 1}


class _FakeConn:
    def __init__(self, sink: list[tuple[str, tuple]]):
        self._sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def transaction(self):
        return self

    def cursor(self):
        return _FakeCursor(self._sink)


def _row(**over):
    row = {
        "id": 1, "agent_slug": "agent-main", "name": "Цены Wildberries",
        "description": "", "schedule": "0 9 * * *",
        "prompt": "Собери цены и запиши в таблицу.",
        "deliver_to": "16", "delivery_channel": "bitrix", "delivery_profile": "agent-main",
        "delivery_conversation_id": "16", "kind": "agent", "created_by": "owner",
        "creator_label": "Александр Никитенко", "is_active": True,
        "last_run_at": None, "last_status": "ok", "last_result": "", "last_error": "",
        "created_at": None, "system_key": None, "last_edited_by": None, "last_edited_at": None,
    }
    row.update(over)
    return row


@pytest.fixture()
def automations(monkeypatch):
    importlib.import_module("app")  # боевой порядок импорта: иначе circular bootstrap agent_center
    import agent_automations as mod

    sink: list[tuple[str, tuple]] = []
    monkeypatch.setattr(mod, "pg_connect", lambda: _FakeConn(sink))
    monkeypatch.setattr(mod, "_requester_name", lambda requested_by, _target: requested_by)
    monkeypatch.setattr(mod, "_sink", sink, raising=False)
    monkeypatch.setattr(mod, "_rows", [], raising=False)
    monkeypatch.setattr(mod, "_load_rows", lambda where="", params=(): list(mod._rows))
    return mod


def _call(mod, args, rows):
    mod._rows = list(rows)
    return mod.automation_self_tool_call({"slug": "agent-main", "name": "Албери"},
                                         "update_my_automation", args)


def test_tool_is_registered_and_reviewed():
    importlib.import_module("app")
    from agent_automations import AUTOMATION_SELF_TOOL_SPECS
    from mcp.tool_policy import CONFIRMATION_REQUIRED, SELF_TOOL_NAMES, policy_for

    assert "update_my_automation" in AUTOMATION_SELF_TOOL_SPECS
    assert "update_my_automation" in SELF_TOOL_NAMES
    # Перезапись чужого сценария не выводится из расплывчатой просьбы.
    assert "update_my_automation" in CONFIRMATION_REQUIRED
    assert policy_for("update_my_automation").domain == "agent-self-service"


def test_agent_fixes_the_scenario_of_an_owner_created_automation(automations):
    result = _call(automations,
                   {"name": "Цены Wildberries", "requested_by": "Александр Никитенко",
                    "task": "Собери цены, запиши в таблицу и ПРОВЕРЬ, что заполнена каждая строка."},
                   [_row()])

    assert result["ok"] is True
    assert result["changed"] == ["task"]
    sql, args = automations._sink[-1]  # noqa: SLF001
    assert sql.startswith("UPDATE agent_automations SET prompt = %s")
    assert args[0].endswith("ПРОВЕРЬ, что заполнена каждая строка.")
    assert args[1] == "0 9 * * *"  # расписание не тронуто
    assert args[3] == ""
    # Авторство строки не переписано, но след правки остался.
    assert args[4] == "агент «Албери» · по просьбе: Александр Никитенко"


def test_untouched_owner_schedule_survives_the_agents_hourly_cap(automations):
    """Автоматизация владельца законно ходит раз в 15 минут; правка одного лишь
    сценария не должна упираться в часовой потолок агента."""
    result = _call(automations,
                   {"name": "Цены Wildberries", "requested_by": "Александр Никитенко",
                    "task": "Новый сценарий с проверкой строк."},
                   [_row(schedule="*/15 * * * *")])

    assert result["ok"] is True


def test_agent_cannot_raise_its_own_automation_above_hourly(automations):
    with pytest.raises(ValueError, match="Слишком часто"):
        _call(automations,
              {"name": "Моя сводка", "requested_by": "Александр Никитенко",
               "schedule": "*/5 * * * *"},
              [_row(name="Моя сводка", created_by="self", schedule="0 9 * * *")])


def test_system_automation_is_refused_with_the_real_reason(automations):
    with pytest.raises(ValueError, match="системная автоматизация"):
        _call(automations,
              {"name": "Синхронизация Битрикса", "requested_by": "Александр Никитенко",
               "task": "что-то другое"},
              [_row(name="Синхронизация Битрикса", kind="system", system_key="hermes:sync")])


def test_recurring_task_row_points_at_its_own_tool(automations):
    with pytest.raises(ValueError, match="update_recurring_task"):
        _call(automations,
              {"name": "Еженедельный отчёт", "requested_by": "Александр Никитенко",
               "task": "что-то другое"},
              [_row(name="Еженедельный отчёт", kind="task")])


def test_empty_edit_is_rejected_instead_of_touching_the_row(automations):
    with pytest.raises(ValueError, match="Нечего менять"):
        _call(automations, {"name": "Цены Wildberries", "requested_by": "Александр"}, [_row()])
    assert automations._sink == []  # noqa: SLF001


def test_missing_automation_names_the_way_to_find_it(automations):
    with pytest.raises(ValueError, match="list_my_automations"):
        _call(automations, {"name": "Нет такой", "requested_by": "Александр", "task": "x"}, [])


def test_pause_without_other_fields_keeps_scenario_and_schedule(automations):
    result = _call(automations,
                   {"name": "Цены Wildberries", "requested_by": "Александр", "active": False},
                   [_row()])

    assert result["active"] is False
    assert result["next_run"] == ""
    _sql, args = automations._sink[-1]  # noqa: SLF001
    assert args[0] == "Собери цены и запиши в таблицу."
    assert args[2] is False


def test_migration_is_registered_and_keeps_authorship_separate():
    from pathlib import Path

    from scripts import ensure_postgres

    assert "089_automation_edit_audit.sql" in ensure_postgres.ALWAYS_APPLY_MIGRATIONS
    sql = (Path(__file__).resolve().parents[2] / "database" / "migrations"
           / "089_automation_edit_audit.sql").read_text(encoding="utf-8")
    assert "last_edited_by" in sql and "last_edited_at" in sql
    assert "created_by" not in sql.split("ALTER TABLE")[1]
