"""История изменений задач и похудевшие снимки состояния.

Задача владельца (06.08.2026): «нам надо отслеживать, кто просрачивал задачи». Ответить было
нечем — снимок состояния писался по расписанию синхронизации (18 раз в сутки все 937 задач,
~17 000 строк в день при 32 реальных изменениях), автора изменения не хранил вовсе, и ни один
из 148 инструментов агента в него не заглядывал. При этом синхронизация УЖЕ звала
tasks.task.history.list на каждую задачу и складывала ответ в raw_json, откуда его никто
не доставал: там родной аудит портала — кто, какое поле, из чего в что, когда.

Данные во всех тестах — настоящие, снятые с прода 06.08.2026:
- запись истории id 9454 (Анастасия Андрусяк, поле NEW);
- переносы дедлайна по задачам 2208 (Горюнова, дважды подряд вперёд) и 2414 (Хапова, назад).
"""
from __future__ import annotations

from datetime import datetime, timezone


# Живой элемент истории с портала — форма ответа tasks.task.history.list.
REAL_HISTORY_ITEM = {
    "id": 9454,
    "user": {
        "id": 42,
        "name": "Анастасия",
        "login": "buhgalter.kuz@gmail.com",
        "lastName": "Андрусяк",
        "secondName": "",
    },
    "field": "NEW",
    "value": {"to": None, "from": None},
    "createdDate": "2026-08-06T16:42:29+03:00",
}

# Задача 2208: срок двигали дважды подряд, каждый раз на сутки вперёд.
REAL_DEADLINE_MOVES_2208 = [
    {
        "id": 9001, "field": "DEADLINE",
        "user": {"id": 30, "name": "Наталья", "lastName": "Горюнова", "secondName": ""},
        "value": {"from": "1785229200", "to": "1785315600"},
        "createdDate": "2026-07-28T17:14:16+03:00",
    },
    {
        "id": 9002, "field": "DEADLINE",
        "user": {"id": 30, "name": "Наталья", "lastName": "Горюнова", "secondName": ""},
        "value": {"from": "1785315600", "to": "1785402000"},
        "createdDate": "2026-07-29T12:25:59+03:00",
    },
]


def _record(task_id, items):
    return {"task_id": task_id, "history": {"items": items}}


# --- журнал изменений -------------------------------------------------------


def test_history_row_carries_who_what_when(bitrix_module):
    """Ровно те четыре факта, которых не хватало для ответа про просрочки."""
    rows = bitrix_module.task_history_rows(_record(2530, [REAL_HISTORY_ITEM]))
    assert len(rows) == 1
    row = rows[0]
    assert row["bitrix_history_id"] == 9454
    assert row["bitrix_task_id"] == 2530
    assert row["field"] == "NEW"
    assert row["changed_by_bitrix_user_id"] == 42
    assert row["changed_by_name"] == "Андрусяк Анастасия"
    assert row["changed_at"] is not None


def test_deadline_moves_are_captured_with_author(bitrix_module):
    """Классический способ спрятать просрочку — подвинуть срок. Он обязан быть виден."""
    rows = bitrix_module.task_history_rows(_record(2208, REAL_DEADLINE_MOVES_2208))
    assert [r["field"] for r in rows] == ["DEADLINE", "DEADLINE"]
    assert {r["changed_by_name"] for r in rows} == {"Горюнова Наталья"}
    assert rows[0]["value_from"] == "1785229200"
    assert rows[0]["value_to"] == "1785315600"
    # Сдвиг ровно на сутки, дважды подряд.
    shift = int(rows[0]["value_to"]) - int(rows[0]["value_from"])
    assert shift == 86400
    assert int(rows[1]["value_from"]) == int(rows[0]["value_to"]), "второй перенос идёт от первого"


def test_history_ids_are_stable_for_dedup(bitrix_module):
    """История перечитывается КАЖДОЙ синхронизацией: без ключа дедупа мы плодили бы дубли.

    Ключ — собственный id записи на портале; он и стоит UNIQUE в таблице.
    """
    first = bitrix_module.task_history_rows(_record(2208, REAL_DEADLINE_MOVES_2208))
    second = bitrix_module.task_history_rows(_record(2208, REAL_DEADLINE_MOVES_2208))
    assert [r["bitrix_history_id"] for r in first] == [r["bitrix_history_id"] for r in second]
    assert len({r["bitrix_history_id"] for r in first}) == 2


def test_history_survives_garbage_from_portal(bitrix_module):
    """Портал отдаёт разнородное: без id, без поля, не-словари. Синк не должен падать."""
    rows = bitrix_module.task_history_rows(_record(2208, [
        REAL_HISTORY_ITEM,
        {"field": "DEADLINE"},          # нет id
        {"id": 5},                       # нет поля
        "мусор",                        # не словарь
        None,
    ]))
    assert len(rows) == 1


def test_no_history_is_not_an_error(bitrix_module):
    assert bitrix_module.task_history_rows({"task_id": 1}) == []
    assert bitrix_module.task_history_rows({"task_id": 1, "history": {}}) == []
    assert bitrix_module.task_history_rows({"task_id": 1, "history": {"items": []}}) == []


# --- снимок только при изменении -------------------------------------------


def _state(status="2", priority="1", responsible="u-1", deadline=None, closed=None):
    return {
        "status": status, "priority": priority, "responsible_id": responsible,
        "deadline_at": deadline, "closed_at_bitrix": closed,
    }


def test_identical_state_writes_nothing(bitrix_module):
    """Суть экономии: 18 прогонов в сутки не должны давать 18 одинаковых строк.

    До правки таблица набрала 2453 МБ за 42 дня и росла на 100 МБ в сутки, ни разу
    не будучи прочитанной.
    """
    assert bitrix_module.snapshot_differs(_state(), _state()) is False


def test_first_ever_snapshot_is_written(bitrix_module):
    assert bitrix_module.snapshot_differs(None, _state()) is True


def test_every_tracked_field_triggers_a_snapshot(bitrix_module):
    base = _state()
    changes = {
        "status": "5",
        "priority": "2",
        "responsible_id": "u-2",
        "deadline_at": datetime(2026, 8, 10, tzinfo=timezone.utc),
        "closed_at_bitrix": datetime(2026, 8, 9, tzinfo=timezone.utc),
    }
    for field, new_value in changes.items():
        changed = {**base, field: new_value}
        assert bitrix_module.snapshot_differs(base, changed) is True, f"{field} обязан давать снимок"


def test_deadline_move_is_never_swallowed(bitrix_module):
    """Перенос срока — самое важное событие для отчёта о просрочках."""
    before = _state(deadline=datetime(2026, 7, 28, 21, 0, tzinfo=timezone.utc))
    after = _state(deadline=datetime(2026, 7, 29, 21, 0, tzinfo=timezone.utc))
    assert bitrix_module.snapshot_differs(before, after) is True


def test_untracked_noise_does_not_write(bitrix_module):
    """Правка описания или заголовка снимок состояния не создаёт — она есть в журнале."""
    before = _state()
    after = {**_state(), "title": "новое название", "description": "другое"}
    assert bitrix_module.snapshot_differs(before, after) is False
