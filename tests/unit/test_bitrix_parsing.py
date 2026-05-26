"""Pure Bitrix task-ingestion helpers (no DB or network).

These are the building blocks of "вытягивание задач из Битрикса": id extraction,
field picking from heterogeneous Bitrix payloads, dedup, and owner-report
compaction.
"""
from __future__ import annotations


def test_to_int(app_module):
    assert app_module.to_int("318241") == 318241
    assert app_module.to_int(5) == 5
    assert app_module.to_int("") is None
    assert app_module.to_int(None) is None
    assert app_module.to_int("not-a-number") is None


def test_first_non_empty(app_module):
    assert app_module.first_non_empty(None, "", "x", "y") == "x"
    assert app_module.first_non_empty(None, "") is None
    assert app_module.first_non_empty(0, "fallback") == 0  # 0 is not empty


def test_nested_get(app_module):
    data = {"chat": {"id": 42}}
    assert app_module.nested_get(data, "chat.id") == 42
    assert app_module.nested_get(data, "chat.missing") is None
    assert app_module.nested_get(data, "missing.path") is None


def test_pick_prefers_first_present_key(app_module):
    data = {"ID": "", "id": "318241"}
    assert app_module.pick(data, "ID", "id") == "318241"
    assert app_module.pick({"a": "v"}, "x", "a") == "v"
    assert app_module.pick({}, "x", "y") is None


def test_pick_supports_dotted_keys(app_module):
    assert app_module.pick({"chat": {"id": 7}}, "chat.id") == 7


def test_extract_task_id(app_module):
    assert app_module.extract_task_id({"id": "318241"}) == 318241
    assert app_module.extract_task_id({"ID": 5}) == 5
    assert app_module.extract_task_id({}) is None


def test_merge_tasks_by_id_dedups_keeping_last(app_module):
    tasks = [
        {"id": 1, "title": "first"},
        {"id": 2, "title": "two"},
        {"id": 1, "title": "first-updated"},
    ]
    merged = app_module.merge_tasks_by_id(tasks)
    ids = [app_module.extract_task_id(t) for t in merged]
    assert ids == [1, 2]  # order preserved, deduped
    by_id = {app_module.extract_task_id(t): t for t in merged}
    assert by_id[1]["title"] == "first-updated"  # last wins


def test_merge_tasks_by_id_skips_idless(app_module):
    merged = app_module.merge_tasks_by_id([{"title": "no id"}, {"id": 9}])
    assert [app_module.extract_task_id(t) for t in merged] == [9]


def test_compact_bitrix_registry_for_owner_shape(app_module):
    payload = {
        "tasks": [
            {"task_id": 1, "title": "T1", "status_text": "В работе", "deadline_text": "26.05",
             "is_overdue": True, "responsible_name": "Иван"},
        ],
        "stats": {"total": 1},
    }
    compact = app_module._compact_bitrix_registry_for_owner(payload)
    assert compact["stats"] == {"total": 1}
    assert len(compact["tasks"]) == 1
    task = compact["tasks"][0]
    assert task["task_id"] == 1
    assert task["title"] == "T1"
    assert task["status"] == "В работе"
    assert task["is_overdue"] is True


def test_compact_bitrix_registry_caps_at_200(app_module):
    payload = {"tasks": [{"task_id": i, "title": f"T{i}"} for i in range(500)], "stats": None}
    compact = app_module._compact_bitrix_registry_for_owner(payload)
    assert len(compact["tasks"]) == 200


def test_compact_bitrix_registry_handles_garbage(app_module):
    compact = app_module._compact_bitrix_registry_for_owner("not-a-dict")
    assert compact == {"tasks": [], "stats": None}
