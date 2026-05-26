"""Date / formatting helpers (pure, no DB or network).

These are used pervasively when rendering pulled Bitrix/Zoom data, and were
among the functions the audit cleanup accidentally deleted.
"""
from __future__ import annotations

from datetime import date, datetime

import pytest


def test_format_datetime_ru_blank(app_module):
    assert app_module.format_datetime_ru(None) == "-"
    assert app_module.format_datetime_ru("") == "-"


def test_format_datetime_ru_naive_datetime(app_module):
    # Naive datetimes are treated as MSK and printed as-is.
    assert app_module.format_datetime_ru(datetime(2026, 5, 26, 14, 30)) == "26.05.2026 14:30"


def test_format_datetime_ru_unparseable_passthrough(app_module):
    assert app_module.format_datetime_ru("not-a-date") == "not-a-date"


def test_format_date_ru(app_module):
    assert app_module.format_date_ru(None) == "-"
    assert app_module.format_date_ru(date(2026, 5, 26)) == "26.05.2026"
    assert app_module.format_date_ru("2026-05-26") == "26.05.2026"
    assert app_module.format_date_ru("garbage") == "garbage"


def test_format_datetime_msk_label(app_module):
    assert app_module.format_datetime_msk_label(None) == ""
    label = app_module.format_datetime_msk_label(datetime(2026, 5, 26, 9, 5))
    assert label == "26.05.2026 в 09:05 МСК"


def test_parse_datetime(app_module):
    assert app_module.parse_datetime(None) is None
    dt = datetime(2026, 5, 26, 14, 30)
    assert app_module.parse_datetime(dt) is dt
    parsed = app_module.parse_datetime("2026-05-26T14:30:00")
    assert (parsed.year, parsed.month, parsed.day, parsed.hour) == (2026, 5, 26, 14)
    aware = app_module.parse_datetime("2026-05-26T14:30:00Z")
    assert aware.tzinfo is not None


def test_parse_date_field_iso_and_ru(app_module):
    assert app_module.parse_date_field("2026-05-26", "from") == date(2026, 5, 26)
    assert app_module.parse_date_field("26.05.2026", "from") == date(2026, 5, 26)


def test_parse_date_field_rejects_garbage(app_module):
    with pytest.raises(ValueError):
        app_module.parse_date_field("", "from")
    with pytest.raises(ValueError):
        app_module.parse_date_field("not-a-date", "from")


def test_period_bounds(app_module):
    start, end = app_module.period_bounds(date(2026, 5, 1), date(2026, 5, 31))
    assert start <= end
    assert (start.hour, start.minute, start.second) == (0, 0, 0)
    assert (end.hour, end.minute) == (23, 59)


def test_is_dt_in_period(app_module):
    start, end = app_module.period_bounds(date(2026, 5, 1), date(2026, 5, 31))
    inside = app_module.make_aware(datetime(2026, 5, 15, 12, 0))
    outside = app_module.make_aware(datetime(2026, 6, 1, 12, 0))
    assert app_module.is_dt_in_period(inside, start, end) is True
    assert app_module.is_dt_in_period(outside, start, end) is False
    assert app_module.is_dt_in_period(None, start, end) is False


def test_base_task_created_in_period(app_module):
    start, end = app_module.period_bounds(date(2026, 5, 1), date(2026, 5, 31))
    assert app_module.base_task_created_in_period({"created": "2026-05-10T09:00:00"}, start, end) is True
    assert app_module.base_task_created_in_period({"created": "2026-04-10T09:00:00"}, start, end) is False
    assert app_module.base_task_created_in_period({}, start, end) is False
