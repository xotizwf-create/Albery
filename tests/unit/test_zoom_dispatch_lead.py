"""Задачи созвона обязаны дойти до людей — даже когда ведущий размечен небрежно.

Инцидент 27.07.2026 (сообщил владелец): «не отправляются задачи по созвонам». По двум
созвонам этого дня отчёты были готовы, задачи с именами и bitrix_user_id тоже — а отправка
падала с «не удалось собрать ни одного получателя». Причина: агрегированная карточка
адресуется ВЕДУЩЕМУ созвона (правило владельца от 24.06.2026), а ведущий искался ровно по
двум признакам — `role_on_call == "host"` и `is_leader`. В обоих отчётах их не оказалось:

  * 07:01 — отчёт пришёл в другой схеме: список людей лежал в `analysis.participants`,
    а не в `people.actual_participants`, поэтому участников было ноль при том, что
    руководитель (Наталья Горюнова, b24=30) размечен и там, и в `leader_evaluations`;
  * 08:20 — схема штатная, пятеро участников, но у всех `role_on_call = "participant"`
    и ни одного `is_leader`.

Задачи молча исчезали. Тесты ниже воспроизводят оба случая на настоящих формах данных.
"""
from __future__ import annotations

import pytest


TEAM = [
    {"user_id": 30, "name": "Наталья Викторовна Горюнова"},
    {"user_id": 36, "name": "Софья Александровна Погорелова"},
    {"user_id": 38, "name": "Дмитрий Александрович Строгонов"},
    {"user_id": 42, "name": "Анастасия Андрусяк"},
    {"user_id": 16, "name": "Александр Дмитриевич Никитенко"},
]


@pytest.fixture
def build(app_module):
    return app_module.build_zoom_operational_task_cards


@pytest.fixture
def participants_of(app_module):
    return app_module.zoom.zoom_call_participants


def _call(analysis):
    return {"raw_json": {"ai_report": {"analysis": analysis}}}


def test_people_are_read_from_the_alternative_report_schema(participants_of):
    """Созвон 07:01: люди пришли в `analysis.participants`. Отчёт в другой схеме — не повод
    считать, что на созвоне никого не было."""
    call = _call({
        "people": {
            "leader_names": ["Наталья Викторовна Горюнова"],
            "participants_matched": ["Наталья Викторовна Горюнова"],
        },
        "participants": [
            {"name": "Наталья Викторовна Горюнова", "is_leader": True, "bitrix_user_id": 30,
             "org_match": "matched", "role_on_call": "руководитель направления маркетплейсов"},
            {"name": "Софья Александровна Погорелова", "is_leader": False, "bitrix_user_id": 36,
             "org_match": "matched", "role_on_call": "технический исполнитель"},
        ],
    })

    people = participants_of(call)

    assert [person["name"] for person in people] == [
        "Наталья Викторовна Горюнова",
        "Софья Александровна Погорелова",
    ]
    assert people[0]["is_leader"] is True
    assert people[0]["bitrix_user_id"] == 30


def test_lead_is_recovered_from_the_leader_evaluation(build, app_module):
    """Созвон 07:01: `role_on_call` — это должность, а не «host». Ведущего называет оценка
    руководителя, и по ней карточка обязана уйти именно ему."""
    participants = [
        {"name": "Наталья Викторовна Горюнова", "bitrix_user_id": 30, "is_leader": False,
         "role_on_call": "руководитель направления маркетплейсов", "org_match": "matched"},
        {"name": "Софья Александровна Погорелова", "bitrix_user_id": 36, "is_leader": False,
         "role_on_call": "технический исполнитель", "org_match": "matched"},
    ]
    tasks = [{
        "task_text": "Доработать процесс FBS по кабинету",
        "assignee_name": "Софья Александровна Погорелова",
        "bitrix_user_id": 36,
        "deadline_text": "28.07.2026",
    }]
    evaluations = [{
        "leader_name": "Наталья Викторовна Горюнова",
        "bitrix_user_id": 30,
        "message_for_leader": "Постановка требует усиления: не назван срок.",
        "verdict": "ok",
    }]

    cards, unmatched_assignees, unmatched_participants = build(
        tasks, participants, evaluations, "Сводка созвона.", TEAM,
        "Итоги созвона 10:01", None, "27.07.2026",
    )

    assert len(cards) == 1
    assert cards[0]["recipient"]["user_id"] == 30
    assert cards[0]["lead_unresolved"] is False
    assert unmatched_assignees == []
    assert unmatched_participants == []


def test_tasks_are_never_lost_when_nobody_is_marked_as_lead(build):
    """Созвон 08:20: ведущего в отчёте нет вовсе. Молчать нельзя — карточка уходит владельцу
    с прямой пометкой, что ведущий не определён, и со всеми задачами внутри."""
    participants = [
        {"name": "Анастасия Андрусяк", "bitrix_user_id": 42, "is_leader": False,
         "role_on_call": "participant", "org_match": "matched"},
        {"name": "Дмитрий Александрович Строгонов", "bitrix_user_id": 38, "is_leader": False,
         "role_on_call": "participant", "org_match": "matched"},
    ]
    tasks = [
        {"task_text": "Подготовить платёжный календарь", "assignee_name": "Анастасия Андрусяк",
         "bitrix_user_id": 42, "deadline_text": "27.07.2026"},
        {"task_text": "Проставить комментарии к платежам",
         "assignee_name": "Дмитрий Александрович Строгонов", "bitrix_user_id": 38,
         "deadline_text": "27.07.2026"},
    ]

    cards, _unmatched_assignees, _unmatched_participants = build(
        tasks, participants, [], "Сводка созвона.", TEAM,
        "Итоги созвона 11:20", None, "27.07.2026",
    )

    assert len(cards) == 1, "задачи обязаны уйти хоть кому-то, а не исчезнуть"
    assert cards[0]["recipient"]["user_id"] == 16, "адресат по умолчанию — владелец"
    assert cards[0]["lead_unresolved"] is True
    description = cards[0]["description"]
    assert "ведущий" in description.lower()
    # Сами задачи обязаны быть в карточке — иначе владельцу нечего раздавать.
    assert "платёжный календарь" in description
    assert "комментарии к платежам" in description


def test_a_marked_host_still_wins_over_every_fallback(build):
    """Штатный случай не меняется: если ведущий размечен, карточка идёт ему."""
    participants = [
        {"name": "Оксана Александровна Хапова", "bitrix_user_id": 32, "is_leader": False,
         "role_on_call": "host", "org_match": "matched"},
        {"name": "Наталья Викторовна Горюнова", "bitrix_user_id": 30, "is_leader": True,
         "role_on_call": "co_leader", "org_match": "matched"},
    ]
    evaluations = [{"leader_name": "Наталья Викторовна Горюнова", "bitrix_user_id": 30,
                    "message_for_leader": "Оценка."}]
    team = TEAM + [{"user_id": 32, "name": "Оксана Александровна Хапова"}]

    cards, _a, _p = build(
        [{"task_text": "Задача", "assignee_name": "Наталья Викторовна Горюнова",
          "bitrix_user_id": 30, "deadline_text": "27.07.2026"}],
        participants, evaluations, "Сводка.", team, "Итоги созвона 09:36", None, "24.07.2026",
    )

    assert len(cards) == 1
    assert cards[0]["recipient"]["user_id"] == 32
    assert cards[0]["lead_unresolved"] is False


def test_a_call_without_tasks_and_without_lead_stays_silent(build):
    """Пустой созвон никого не беспокоит: тревога — про потерянные задачи, а не про тишину."""
    cards, _a, _p = build([], [], [], "", TEAM, "Итоги созвона 12:00", None, "27.07.2026")

    assert cards == []
