"""Bitrix task + org-structure pull, with a fake Bitrix client and fake DB.

Exercises the real assembly/normalization pipeline (build_task_record,
resolve_person -> departments/manager) and the sync orchestration
(sync_bitrix_task_by_id -> upsert / delete) without any HTTP or PostgreSQL.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


class FakeBitrixClient:
    """Duck-typed stand-in for BitrixClient used by build_task_record."""

    def __init__(self, *, details, users=None, departments=None,
                 results=None, history=None, checklist=None, comments=None):
        self._details = details
        self._users = users or {}
        self._departments = departments or {}
        self._results = results or {}
        self._history = history or {}
        self._checklist = checklist or {}
        self._comments = comments or {}
        self.request_delay = 0

    def get_task_details(self, task_id):
        return self._details

    def get_task_results(self, task_id):
        return self._results

    def get_task_history(self, task_id):
        return self._history

    def get_task_checklist(self, task_id):
        return self._checklist

    def get_task_comments(self, task_id, chat_id=None):
        return self._comments

    def get_user(self, user_id):
        return self._users.get(user_id, {})

    def get_department(self, dep_id):
        return self._departments.get(dep_id, {})


def _sample_client():
    return FakeBitrixClient(
        details={
            "id": 318241,
            "title": "Подготовить КП",
            "description": "Сделать коммерческое предложение",
            "creator": {"id": 1, "name": "Иван Петров"},
            "responsible": {"id": 2, "name": "Пётр Сидоров"},
            "status": "2",
            "deadline": "2026-05-30T18:00:00",
        },
        users={
            1: {"ID": 1, "NAME": "Иван", "LAST_NAME": "Петров", "EMAIL": "ivan@x.ru"},
            2: {"ID": 2, "NAME": "Пётр", "LAST_NAME": "Сидоров", "UF_DEPARTMENT": [10]},
            5: {"ID": 5, "NAME": "Босс", "LAST_NAME": "Главный"},
        },
        departments={10: {"ID": 10, "NAME": "Продажи", "UF_HEAD": 5}},
        results={"result": {"items": [{"id": "r1"}]}},
        comments={"result": {"messages": [{"id": "c1"}, {"id": "c2"}]}},
    )


def test_build_task_record_assembles_pulled_data(app_module):
    record = app_module.build_task_record(_sample_client(), {"id": 318241})
    assert record["task_id"] == 318241
    assert record["title"] == "Подготовить КП"
    assert record["status"]["code"] == "2"
    assert record["deadline"] == "2026-05-30T18:00:00"
    assert record["creator"]["name"]
    assert record["responsible"]["name"]
    # org structure: department + manager resolved from Bitrix
    assert "Продажи" in record["department"]
    assert record["result"]["items"] == [{"id": "r1"}]
    assert len(record["comments"]["items"]) == 2


def test_build_task_record_requires_id(app_module):
    with pytest.raises(ValueError):
        app_module.build_task_record(_sample_client(), {})


def test_sync_bitrix_task_upserts(bitrix_module, fake_pg, monkeypatch):
    monkeypatch.setenv("BITRIX_WEBHOOK_BASE", "https://example.bitrix24.ru/rest/1/token/")
    monkeypatch.setattr(bitrix_module, "BitrixClient", lambda base: _sample_client())
    cur = fake_pg(bitrix_module)

    result = bitrix_module.sync_bitrix_task_by_id(318241, event_name="ONTASKUPDATE")

    assert result["action"] == "upserted"
    assert result["task_id"] == 318241
    assert any("INSERT INTO bitrix_tasks" in sql for sql, _ in cur.executed)


def test_sync_bitrix_task_delete_event(bitrix_module, fake_pg, monkeypatch):
    monkeypatch.setenv("BITRIX_WEBHOOK_BASE", "https://example.bitrix24.ru/rest/1/token/")
    cur = fake_pg(bitrix_module)

    result = bitrix_module.sync_bitrix_task_by_id(999, event_name="ONTASKDELETE")

    assert result["action"] == "deleted"
    assert any("DELETE FROM bitrix_tasks" in sql for sql, _ in cur.executed)


def test_list_users_parses_and_dedups(app_module, monkeypatch):
    client = app_module.BitrixClient("https://example.bitrix24.ru/rest/1/token/")
    pages = [
        {"result": [
            {"ID": "1", "NAME": "Иван", "ACTIVE": True},
            {"ID": "2", "NAME": "Пётр", "ACTIVE": True},
            {"ID": "1", "NAME": "Иван dup", "ACTIVE": True},  # duplicate id
        ]},
    ]
    calls = {"n": 0}

    def fake_call_with_fallback(method, payload=None, prefer_api=False):
        assert method == "user.get"
        i = calls["n"]
        calls["n"] += 1
        return pages[i] if i < len(pages) else {"result": []}

    monkeypatch.setattr(client, "call_with_fallback", fake_call_with_fallback)
    users = client.list_users()
    ids = sorted(app_module.to_int(u.get("ID")) for u in users)
    assert ids == [1, 2]  # deduped by ID
