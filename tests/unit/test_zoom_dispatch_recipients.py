"""Готовые задачи созвона обязаны дойти до людей, даже если блок участников пустой.

20.07.2026, созвон 11:02 (1bb5ce87): модель вернула людей не в `people.actual_participants`, а в
самопридуманных `participants_matched`/`technical_silent_participants`. Рассылка читала только
первое поле, получила ноль получателей и упала — в интерфейсе 400 BAD REQUEST, шесть готовых
задач с назначенными исполнителями не ушли никому.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def zoom_mod(app_module):
    return app_module.zoom


def _call(analysis: dict) -> dict:
    return {"raw_json": {"ai_report": {"analysis": analysis}}, "analytical_note": ""}


def test_participants_read_from_alternative_shape(zoom_mod, monkeypatch):
    """Точная форма ответа модели из инцидента."""
    monkeypatch.setattr(zoom_mod, "org_user_id_for_name",
                        lambda name: {"Дмитрий Александрович Строгонов": 38,
                                      "Оксана Александровна Хапова": 32}.get(name))
    call = _call({"people": {
        "participants_matched": ["Дмитрий Александрович Строгонов", "Оксана Александровна Хапова"],
        "technical_silent_participants": ["Координатор"],
    }})

    people = zoom_mod.zoom_call_participants(call)

    assert [p["name"] for p in people] == ["Дмитрий Александрович Строгонов",
                                           "Оксана Александровна Хапова"]
    assert [p["bitrix_user_id"] for p in people] == [38, 32]
    assert "Координатор" not in [p["name"] for p in people]


def test_documented_shape_still_wins(zoom_mod):
    call = _call({"people": {
        "actual_participants": [{"person_name": "Наталья Викторовна Горюнова",
                                 "bitrix_user_id": 30, "org_match": "matched", "is_leader": True}],
        "participants_matched": ["Кто-то Другой"],
    }})

    people = zoom_mod.zoom_call_participants(call)

    assert [p["name"] for p in people] == ["Наталья Викторовна Горюнова"]


def test_recipients_rebuilt_from_assigned_tasks(zoom_mod):
    """Шесть задач с исполнителями — это не «созвон без получателей»."""
    tasks = [
        {"assignee_name": "Дмитрий Александрович Строгонов", "bitrix_user_id": 38},
        {"assignee_name": "Оксана Александровна Хапова", "bitrix_user_id": 32},
        {"assignee_name": "Оксана Александровна Хапова", "bitrix_user_id": 32},  # дубль
        {"assignee_name": "Без исполнителя", "bitrix_user_id": None},
    ]
    leaders = [{"leader_name": "Наталья Викторовна Горюнова", "bitrix_user_id": 30}]

    people = zoom_mod.participants_from_tasks_and_leaders(tasks, leaders)

    assert [p["bitrix_user_id"] for p in people] == [30, 38, 32], "лидер первым, дубли схлопнуты"
    assert people[0]["is_leader"] is True
    assert all(p["name"] for p in people)


def test_no_tasks_no_invented_recipients(zoom_mod):
    """Если слать нечего — не выдумывать получателей."""
    assert zoom_mod.participants_from_tasks_and_leaders([], []) == []
    assert zoom_mod.participants_from_tasks_and_leaders(None, None) == []
    assert zoom_mod.participants_from_tasks_and_leaders(
        [{"assignee_name": "Кто-то", "bitrix_user_id": None}], []) == []


def test_alias_used_when_resolving_a_name(zoom_mod, monkeypatch):
    monkeypatch.setattr(zoom_mod, "name_alias_pairs",
                        lambda: [("Анастасия Докучаева", "Анастасия Андрусяк")])
    monkeypatch.setattr(zoom_mod, "load_team_members", lambda: [], raising=False)
    import app as app_module
    monkeypatch.setattr(app_module, "load_team_members",
                        lambda: [{"name": "Анастасия Андрусяк", "user_id": 42}])

    assert zoom_mod.org_user_id_for_name("Анастасия Докучаева") == 42
    assert zoom_mod.org_user_id_for_name("Анастасия Клеблеева") is None


def test_host_restored_from_leader_evaluation(zoom_mod, monkeypatch):
    """Без ведущего рассылка не собирает НИ ОДНОЙ карточки — это и убило задачи 20.07."""
    monkeypatch.setattr(zoom_mod, "name_alias_pairs", lambda: [])
    participants = [
        {"name": "Наталья Викторовна Горюнова", "bitrix_user_id": 30, "is_leader": False,
         "role_on_call": "participant"},
        {"name": "Дмитрий Александрович Строгонов", "bitrix_user_id": 38, "is_leader": False,
         "role_on_call": "participant"},
    ]
    leaders = [{"leader_name": "Наталья Викторовна Горюнова", "bitrix_user_id": 30}]

    people = zoom_mod.ensure_call_host(participants, leaders)

    host = [p for p in people if p["role_on_call"] == "host"]
    assert len(host) == 1 and host[0]["bitrix_user_id"] == 30
    assert len(people) == 2, "остальных участников трогать нельзя"


def test_existing_host_is_left_alone(zoom_mod):
    participants = [{"name": "Наталья", "bitrix_user_id": 30, "role_on_call": "host", "is_leader": True},
                    {"name": "Софья", "bitrix_user_id": 36, "role_on_call": "participant"}]

    people = zoom_mod.ensure_call_host(participants, [])

    assert people == participants


def test_host_falls_back_to_tasks_when_leader_not_among_participants(zoom_mod, monkeypatch):
    monkeypatch.setattr(zoom_mod, "name_alias_pairs", lambda: [])
    participants = [{"name": "Кто-то Посторонний", "bitrix_user_id": 99, "role_on_call": "participant"}]
    leaders = [{"leader_name": "Наталья Викторовна Горюнова", "bitrix_user_id": 30}]
    tasks = [{"assignee_name": "Дмитрий Александрович Строгонов", "bitrix_user_id": 38}]

    people = zoom_mod.ensure_call_host(participants, leaders, tasks)

    assert people[0]["bitrix_user_id"] == 30 and people[0]["role_on_call"] == "host"
    assert 99 in [p["bitrix_user_id"] for p in people], "известных участников не теряем"


def test_host_matched_through_alias(zoom_mod, monkeypatch):
    monkeypatch.setattr(zoom_mod, "name_alias_pairs",
                        lambda: [("Анастасия Докучаева", "Анастасия Андрусяк")])
    participants = [{"name": "Анастасия Докучаева", "bitrix_user_id": None, "role_on_call": "participant"}]
    leaders = [{"leader_name": "Анастасия Андрусяк", "bitrix_user_id": 42}]

    people = zoom_mod.ensure_call_host(participants, leaders)

    assert people[0]["role_on_call"] == "host"


def test_prompt_pins_the_participants_field(app_module):
    """Промпт обязан запрещать переименование поля — именно это и сломало рассылку."""
    from pathlib import Path

    prompt = (Path(__file__).resolve().parents[2] / "scripts" / "zoom_processing_prompt_v9.md").read_text(
        encoding="utf-8")
    assert "СТРУКТУРУ JSON МЕНЯТЬ НЕЛЬЗЯ" in prompt
    assert "participants_matched" in prompt, "в промпте должен быть явный запрет на это поле"
