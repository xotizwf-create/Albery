"""Отчёт о дисциплине сроков — против настоящего PostgreSQL.

SQL здесь нетривиальный (CTE по истории, array_agg с FILTER, регексп на unix-строках,
арифметика по epoch), а именно непроверенный SQL и ломается на проде. Юнит-тесты рядом
(tests/unit/test_task_history_and_snapshots.py) закрывают чистые функции, этот — сами запросы.

Marked `db`: идёт только когда есть DATABASE_URL (CI поднимает Postgres со схемой через
scripts/ensure_postgres.py), локально пропускается.

Сценарий взят с прода 06.08.2026: задача 2208, срок двигали дважды подряд вперёд (Горюнова),
закрыта позже дедлайна; задача 2414 — срок подвинули НАЗАД (Хапова).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.db

TASK_MOVED_AND_LATE = 992208
TASK_MOVED_EARLIER = 992414
TASK_CLEAN = 992000


@pytest.fixture()
def seeded_tasks(app_module):
    """Три задачи с историей. Номера с префиксом 99 — чтобы не столкнуться с живыми."""
    ids = (TASK_MOVED_AND_LATE, TASK_MOVED_EARLIER, TASK_CLEAN)
    with app_module.pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM bitrix_task_history WHERE bitrix_task_id = ANY(%s)", (list(ids),))
            cur.execute("DELETE FROM bitrix_tasks WHERE bitrix_task_id = ANY(%s)", (list(ids),))

            cur.execute(
                """
                INSERT INTO bitrix_tasks (bitrix_task_id, title, status, deadline_at, closed_at_bitrix)
                VALUES
                    (%s, 'Просрочена, срок двигали', '5',
                     timestamptz '2026-07-30 18:00+03', timestamptz '2026-08-04 18:00+03'),
                    (%s, 'Срок подвинули назад', '5',
                     timestamptz '2026-08-01 18:00+03', timestamptz '2026-07-31 12:00+03'),
                    (%s, 'Закрыта в срок, без переносов', '5',
                     timestamptz '2026-07-29 18:00+03', timestamptz '2026-07-29 12:00+03')
                """,
                ids,
            )
            cur.execute(
                """
                INSERT INTO bitrix_task_history (
                    bitrix_history_id, bitrix_task_id, field, value_from, value_to,
                    changed_by_bitrix_user_id, changed_by_name, changed_at
                ) VALUES
                    (999001, %s, 'DEADLINE', '1785229200', '1785315600', 30, 'Горюнова Наталья',
                     timestamptz '2026-07-28 17:14+03'),
                    (999002, %s, 'DEADLINE', '1785315600', '1785402000', 30, 'Горюнова Наталья',
                     timestamptz '2026-07-29 12:25+03'),
                    (999003, %s, 'DEADLINE', '1786118400', '1785513600', 31, 'Хапова Ольга',
                     timestamptz '2026-07-31 10:24+03'),
                    (999004, %s, 'STATUS', '2', '5', 30, 'Горюнова Наталья',
                     timestamptz '2026-08-04 18:01+03')
                """,
                (TASK_MOVED_AND_LATE, TASK_MOVED_AND_LATE, TASK_MOVED_EARLIER, TASK_MOVED_AND_LATE),
            )
        conn.commit()
    yield ids
    with app_module.pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM bitrix_task_history WHERE bitrix_task_id = ANY(%s)", (list(ids),))
            cur.execute("DELETE FROM bitrix_tasks WHERE bitrix_task_id = ANY(%s)", (list(ids),))
        conn.commit()


def _row(result, task_id):
    return next(r for r in result["items"] if r["bitrix_task_id"] == task_id)


def test_late_closure_is_counted_even_though_task_is_closed(ctx, seeded_tasks):
    """Главный смысл отчёта: закрыта — не значит вовремя.

    list_overdue_tasks эту задачу не покажет вовсе (она закрыта), а просрочка была 5 дней.
    """
    result = ctx.tool_report_overdue_discipline({"date_from": "2026-07-01", "date_to": "2026-08-31"})
    row = _row(result, TASK_MOVED_AND_LATE)
    assert float(row["days_late"]) == pytest.approx(5.0, abs=0.1)
    assert row["was_late"] is True
    assert row["still_open"] is False


def test_deadline_moves_are_attributed_to_the_person(ctx, seeded_tasks):
    """Кто именно двигал срок — то, ради чего всё затевалось."""
    result = ctx.tool_report_overdue_discipline({"date_from": "2026-07-01", "date_to": "2026-08-31"})
    row = _row(result, TASK_MOVED_AND_LATE)
    assert row["deadline_moves"] == 2
    assert row["moved_by"] == ["Горюнова Наталья"]
    # Два переноса по суткам вперёд = +2 дня.
    assert float(row["deadline_shifted_days"]) == pytest.approx(2.0, abs=0.1)


def test_deadline_moved_backwards_gives_negative_shift(ctx, seeded_tasks):
    """Срок можно и сократить — знак сдвига обязан это показывать, а не терять."""
    result = ctx.tool_report_overdue_discipline({"date_from": "2026-07-01", "date_to": "2026-08-31"})
    row = _row(result, TASK_MOVED_EARLIER)
    assert row["deadline_moves"] == 1
    assert float(row["deadline_shifted_days"]) == pytest.approx(-7.0, abs=0.1)
    assert row["was_late"] is False, "закрыта раньше своего дедлайна"


def test_clean_task_has_no_late_marker(ctx, seeded_tasks):
    result = ctx.tool_report_overdue_discipline({"date_from": "2026-07-01", "date_to": "2026-08-31"})
    row = _row(result, TASK_CLEAN)
    assert row["days_late"] is None
    assert row["was_late"] is False
    assert row["deadline_moves"] == 0
    assert row["moved_by"] is None


def test_period_filter_excludes_tasks_outside_it(ctx, seeded_tasks):
    result = ctx.tool_report_overdue_discipline({"date_from": "2026-08-10", "date_to": "2026-08-31"})
    assert all(r["bitrix_task_id"] not in seeded_tasks for r in result["items"])


def test_task_history_tool_renders_deadlines_readably(ctx, seeded_tasks):
    """Сырые unix-строки модели показывать нельзя — она их не прочитает."""
    result = ctx.tool_get_task_history({"bitrix_task_id": TASK_MOVED_AND_LATE, "fields": ["DEADLINE"]})
    assert result["total"] == 2
    assert all(item["field"] == "DEADLINE" for item in result["items"])
    for item in result["items"]:
        assert item["value_from_readable"] and "." in item["value_from_readable"]
        assert item["value_to_readable"] and "." in item["value_to_readable"]
    assert result["items"][0]["changed_by_name"] == "Горюнова Наталья"


def test_task_history_returns_all_fields_when_not_filtered(ctx, seeded_tasks):
    result = ctx.tool_get_task_history({"bitrix_task_id": TASK_MOVED_AND_LATE})
    assert {item["field"] for item in result["items"]} == {"DEADLINE", "STATUS"}


def test_task_history_requires_a_task_id(ctx):
    with pytest.raises(ValueError):
        ctx.tool_get_task_history({})


def test_history_insert_is_idempotent(app_module, seeded_tasks):
    """История перечитывается каждой синхронизацией — повтор не должен плодить строки."""
    with app_module.pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO bitrix_task_history (
                    bitrix_history_id, bitrix_task_id, field, value_from, value_to,
                    changed_by_bitrix_user_id, changed_by_name, changed_at
                ) VALUES (999001, %s, 'DEADLINE', '1785229200', '1785315600', 30,
                          'Горюнова Наталья', timestamptz '2026-07-28 17:14+03')
                ON CONFLICT (bitrix_history_id) DO NOTHING
                """,
                (TASK_MOVED_AND_LATE,),
            )
            cur.execute(
                "SELECT count(*) AS c FROM bitrix_task_history WHERE bitrix_task_id = %s",
                (TASK_MOVED_AND_LATE,),
            )
            assert cur.fetchone()["c"] == 3
        conn.rollback()
