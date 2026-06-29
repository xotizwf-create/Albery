from __future__ import annotations


def _sample_call(dispatched: bool = False):
    ai_report = {
        "analysis": {
            "dispatch_summary": {"discussed": "План продаж и акции", "decided": "Запустить проверку витрины"},
            "people": {
                "actual_participants": [
                    {"person_name": "Иван Иванов", "bitrix_user_id": 101, "role_on_call": "host"},
                    {"person_name": "Мария Петрова", "bitrix_user_id": 102, "role_on_call": "participant"},
                ],
                "mentioned_people": [
                    {"person_name": "Сергей Сидоров", "bitrix_user_id": 103},
                ],
            },
            "person_summaries": [
                {"person_name": "Мария Петрова", "bitrix_user_id": 102, "score": 9, "message_for_person": "Хорошо включалась в обсуждение, следующий шаг — короче фиксировать решения."}
            ],
            "leader_evaluations": [
                {"person_name": "Иван Иванов", "bitrix_user_id": 101, "verdict": "issue", "message_for_leader": "Стоит лучше фиксировать решения в конце встречи."}
            ],
            "operational_tasks": [
                {"assignee_name": "Мария Петрова", "bitrix_user_id": 102, "task_text": "Проверить витрину", "deadline_text": "срок не указан", "result_criteria": "Выводы отправлены", "status": "planned", "source": "00:01"}
            ],
        }
    }
    if dispatched:
        ai_report["participant_reports_dispatched_at"] = "2026-06-29T10:00:00+03:00"
    return {
        "id": "call-1",
        "date": "2026-06-29",
        "time_text": "10:00-10:30",
        "analytical_note": "Отчет",
        "raw_json": {"ai_report": ai_report},
    }


def test_zoom_participant_reports_uses_actual_participants_only(app_module, monkeypatch):
    monkeypatch.setattr(app_module, "load_zoom_call_detail", lambda call_id: _sample_call())
    monkeypatch.setattr(app_module, "load_team_members", lambda: [{"name": "Иван Иванов", "user_id": 101}, {"name": "Мария Петрова", "user_id": 102}])
    monkeypatch.setattr(app_module, "load_employee_name_aliases", lambda: {})

    payload = app_module.build_zoom_participant_reports_dispatch("call-1")

    assert len(payload["task_cards"]) == 2
    by_name = {card["recipient"]["name"]: card for card in payload["task_cards"]}
    assert set(by_name) == {"Иван Иванов", "Мария Петрова"}
    assert "Сергей Сидоров" not in by_name
    assert "План продаж" in by_name["Мария Петрова"]["description"]
    assert "Хорошо включалась" in by_name["Мария Петрова"]["description"]
    assert "поддерживающая обратная связь" in by_name["Иван Иванов"]["description"].lower()
    assert payload["unmatched_participants"] == []


def test_operational_dispatch_preview_combines_tasks_and_participant_reports(app_module, monkeypatch):
    monkeypatch.setattr(app_module, "load_zoom_call_detail", lambda call_id: _sample_call())
    monkeypatch.setattr(app_module, "load_team_members", lambda: [{"name": "Иван Иванов", "user_id": 101}, {"name": "Мария Петрова", "user_id": 102}])
    monkeypatch.setattr(app_module, "load_employee_name_aliases", lambda: {})

    preview = app_module.preview_zoom_operational_tasks("call-1")

    assert [card["card_kind"] for card in preview["task_cards"]] == ["operational", "participant_report", "participant_report"]
    assert preview["task_cards"][0]["recipient"]["user_id"] == 101
    assert preview["task_cards"][0]["is_lead_card"] is True
    assert "Задачи для постановки" in preview["task_cards"][0]["description"]
    assert len(preview["participant_task_cards"]) == 2
    assert preview["participant_reports_error"] == ""


def test_participant_reports_refuse_duplicate_without_touching_operational_status(app_module, monkeypatch):
    monkeypatch.setattr(app_module, "load_zoom_call_detail", lambda call_id: _sample_call(dispatched=True))
    try:
        app_module.preview_zoom_participant_reports("call-1")
    except ValueError as exc:
        assert "уже отправлены" in str(exc)
    else:
        raise AssertionError("expected duplicate dispatch guard")
