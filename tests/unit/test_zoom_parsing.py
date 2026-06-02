"""Pure Zoom operational-task extraction (no DB or network).

Covers "вытягивание задач из созвонов": turning the LLM analysis / report
section into a normalized, Bitrix-ready task list.
"""
from __future__ import annotations


def test_normalize_from_analysis_operational_tasks(app_module):
    analysis = {
        "operational_tasks": [
            {
                "task_text": "подготовить отчёт по продажам",
                "responsible": "Иван Петров",
                "deadline_text": "до пятницы",
                "result_criteria": "отчёт отправлен руководителю",
                "bitrix_user_id": "42",
            }
        ]
    }
    tasks = app_module.normalize_zoom_operational_tasks(analysis=analysis)
    assert len(tasks) == 1
    task = tasks[0]
    assert task["assignee_name"] == "Иван Петров"
    assert task["bitrix_user_id"] == 42
    assert "отчёт" in task["task_text"].lower()
    assert task["deadline_text"] == "до пятницы"
    assert "руководителю" in task["result_criteria"].lower()
    assert task["number"] == 1


def test_normalize_skips_items_without_task_text(app_module):
    analysis = {"operational_tasks": [{"responsible": "Иван"}, {"task_text": "сделать X"}]}
    tasks = app_module.normalize_zoom_operational_tasks(analysis=analysis)
    assert len(tasks) == 1
    assert "x" in tasks[0]["task_text"].lower()


def test_normalize_assignee_fallback(app_module):
    analysis = {"operational_tasks": [{"task_text": "проверить договор"}]}
    tasks = app_module.normalize_zoom_operational_tasks(analysis=analysis)
    assert tasks[0]["assignee_name"] == "Требует назначения"
    assert tasks[0]["deadline_text"] == "срок не указан"


def test_normalize_empty_returns_empty_list(app_module):
    assert app_module.normalize_zoom_operational_tasks() == []
    assert app_module.normalize_zoom_operational_tasks(analysis={}) == []


def test_format_for_bitrix_numbers_and_deadline(app_module):
    tasks = [
        {"task_text": "сделать отчёт", "result_criteria": "готов", "deadline_text": "завтра"},
        {"task_text": "позвонить клиенту", "result_criteria": "", "deadline_text": "срок не указан"},
    ]
    text = app_module.format_zoom_operational_tasks_for_bitrix(tasks)
    lines = text.splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("1.")
    assert lines[1].startswith("2.")
    assert "Срок: завтра" in lines[0]
    assert "Критерий: готов" in lines[0]
    # No result criteria -> no criterion clause.
    assert "Критерий:" not in lines[1]


def test_zoom_dispatch_title_includes_date_and_time_range(app_module):
    call = {"date": "2026-06-02", "time_text": "14:00 - 14:42"}

    assert app_module.zoom_dispatch_title(call) == "Итоги созвона 02.06, 14:00 - 14:42"


def test_build_zoom_card_description_for_leader_is_personal(app_module):
    description = app_module.build_zoom_card_description(
        "Обсуждали: поставки.\nРешили: сверить таблицу.",
        "Вы хорошо удержали повестку и мягко вернули обсуждение к срокам.",
        [
            {
                "task_text": "проверить таблицу фабрик",
                "deadline_text": "03.06.2026",
                "result_criteria": "таблица обновлена и отправлена Артуру",
            }
        ],
    )

    assert "Оценка Вас как руководителя: Вы хорошо удержали повестку" in description
    assert app_module.ZOOM_OPERATIONAL_TASKS_DISPATCH_INTRO in description
    assert "1. Проверить таблицу фабрик. Срок: 03.06.2026. Критерий: таблица обновлена и отправлена Артуру." in description
