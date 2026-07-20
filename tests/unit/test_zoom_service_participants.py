"""Служебный аккаунт Zoom не должен попадать в отчёт как участник.

В техническом логе Zoom рядом с людьми числится аккаунт зала («Координатор»). Человеком он не
является, но доходил до отчёта строкой «не сопоставлен с оргструктурой, требуется уточнение» —
фантомный участник в каждом созвоне (сообщил владелец 20.07.2026).

Правка намеренно узкая: только служебные имена. Живых участников, включая молчавших, фильтр
не трогает — форма отчёта и рассылки остаётся прежней.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def drop(app_module):
    return app_module.zoom.drop_service_participants


def test_room_account_is_dropped(drop):
    people = [{"name": "Оксана Хапова"}, {"name": "Координатор"}, {"name": "Дмитрий Строгонов"}]

    assert [p["name"] for p in drop(people)] == ["Оксана Хапова", "Дмитрий Строгонов"]


def test_other_service_accounts_are_dropped(drop):
    people = [{"name": "Zoom Room"}, {"name": "Recorder"}, {"name": "Гость"}, {"name": "Наталья"}]

    assert [p["name"] for p in drop(people)] == ["Наталья"]


def test_case_and_spaces_do_not_help_a_service_account(drop):
    assert drop([{"name": "  КООРДИНАТОР "}]) == []


def test_real_people_are_never_touched(drop):
    """Молчавший на созвоне человек — всё равно участник; фильтр только про служебные имена."""
    people = [{"name": "Наталья Викторовна Горюнова"}, {"name": "Анастасия Докучаева"},
              {"name": "Погорелова Софья"}]

    assert drop(people) == people


def test_empty_input_is_safe(drop):
    assert drop([]) == []
    assert drop(None) == []


def test_prompt_forbids_taking_a_participant_from_the_topic(app_module):
    """Второй источник «Координатора» — название зала в теме созвона."""
    from pathlib import Path

    prompt = (Path(__file__).resolve().parents[2] / "scripts" / "zoom_processing_prompt_v9.md").read_text(
        encoding="utf-8")
    assert "Зал персональной конференции Координатор" in prompt
    assert "в участники брать нельзя" in prompt
