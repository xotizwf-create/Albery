from __future__ import annotations

import pytest


def _full_analysis() -> dict:
    return {
        "dispatch_summary": "Обсуждали: продажи. Решили: проверить план.",
        "leader_evaluations": [],
        "people": {"actual_participants": [], "mentioned_people": []},
        "operational_tasks": [
            {
                "task_text": "проверить план продаж",
                "assignee_name": "Иван Петров",
                "bitrix_user_id": 42,
                "deadline_text": "сегодня",
                "result_criteria": "план проверен",
                "expected_artifact": "комментарий с выводом",
                "responsibility_check": {"owner_is_clear": True},
                "status": "planned",
                "source": "00:01:00",
            }
        ],
    }


def test_zoom_report_analysis_rejects_abbreviated_payload(ctx):
    with pytest.raises(ctx.McpError) as exc:
        ctx.validate_zoom_call_report_analysis(
            {
                "dispatch_summary": "Обсуждали склад.",
                "leader_evaluations": [],
                "leaders_present": ["Артур"],
                "operational_tasks_count": 6,
            }
        )

    assert exc.value.code == -32602
    assert "missing keys" in exc.value.message
    assert "people" in exc.value.message
    assert "operational_tasks" in exc.value.message


def test_zoom_report_analysis_rejects_incomplete_task(ctx):
    analysis = _full_analysis()
    analysis["operational_tasks"][0].pop("expected_artifact")

    with pytest.raises(ctx.McpError) as exc:
        ctx.validate_zoom_call_report_analysis(analysis)

    assert exc.value.code == -32602
    assert "expected_artifact" in exc.value.message


def test_zoom_report_analysis_accepts_full_payload(ctx):
    ctx.validate_zoom_call_report_analysis(_full_analysis())


def test_zoom_report_analysis_allows_error_status_without_full_payload(ctx):
    ctx.validate_zoom_call_report_analysis({}, status="error")
