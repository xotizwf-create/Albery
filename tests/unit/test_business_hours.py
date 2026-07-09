"""Working-hours deadline rules (owner, 2026-07-09): business day 9:00-18:00 MSK, Mon-Fri.

Zoom lead card: 18:00 today, but < 3 hours before 18:00 (or a day off) -> next working day 11:00.
Owner recommendations task: 12:00 of the next working day after the report date.
"""
from __future__ import annotations

from datetime import date, datetime

import business_hours as bh


def msk(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=bh.MSK_TZ)


# --- zoom lead deadline -------------------------------------------------------------------

def test_zoom_morning_dispatch_is_due_today_1800():
    # Wednesday 10:00 -> plenty of time -> today 18:00.
    assert bh.zoom_lead_deadline_at(msk(2026, 7, 8, 10)) == msk(2026, 7, 8, 18)


def test_zoom_exactly_three_hours_left_keeps_today():
    # 15:00 leaves exactly 3 hours — "less than 3" has not happened yet.
    assert bh.zoom_lead_deadline_at(msk(2026, 7, 8, 15)) == msk(2026, 7, 8, 18)


def test_zoom_less_than_three_hours_rolls_to_next_morning():
    assert bh.zoom_lead_deadline_at(msk(2026, 7, 8, 15, 1)) == msk(2026, 7, 9, 11)


def test_zoom_evening_dispatch_rolls_to_next_morning():
    assert bh.zoom_lead_deadline_at(msk(2026, 7, 8, 19, 30)) == msk(2026, 7, 9, 11)


def test_zoom_friday_afternoon_rolls_to_monday():
    # Friday 16:00 -> Monday 11:00 (weekend skipped).
    assert bh.zoom_lead_deadline_at(msk(2026, 7, 10, 16)) == msk(2026, 7, 13, 11)


def test_zoom_weekend_dispatch_rolls_to_monday():
    assert bh.zoom_lead_deadline_at(msk(2026, 7, 11, 12)) == msk(2026, 7, 13, 11)


def test_zoom_dispatch_deadline_wrapper(zoom_module, monkeypatch):
    monkeypatch.setattr(bh, "msk_now", lambda: msk(2026, 7, 8, 10))
    iso, text = zoom_module.zoom_dispatch_deadline({})
    assert iso == msk(2026, 7, 8, 18).isoformat()
    assert text == "08.07.2026 18:00 МСК"


def test_zoom_dispatch_deadline_ignores_stale_call_date(zoom_module, monkeypatch):
    # An old call dispatched in the evening still gets a workable deadline (next morning),
    # never 19:00 of a date in the past.
    monkeypatch.setattr(bh, "msk_now", lambda: msk(2026, 7, 8, 17))
    iso, text = zoom_module.zoom_dispatch_deadline({"date": "2026-07-01"})
    assert iso == msk(2026, 7, 9, 11).isoformat()
    assert text == "09.07.2026 11:00 МСК"


# --- owner recommendations deadline -------------------------------------------------------

def test_recommendations_midweek_report_due_next_day_1200():
    assert bh.recommendations_deadline_at(date(2026, 7, 8)) == msk(2026, 7, 9, 12)


def test_recommendations_friday_report_due_monday_1200():
    assert bh.recommendations_deadline_at(date(2026, 7, 10)) == msk(2026, 7, 13, 12)


def test_recommendations_saturday_anchor_due_monday_1200():
    assert bh.recommendations_deadline_at(date(2026, 7, 11)) == msk(2026, 7, 13, 12)


def test_owner_recommendations_task_deadline_uses_working_day(app_module):
    iso, text = app_module.owner_recommendations_task_deadline(
        {"report_date": "2026-07-10"}, "daily"
    )
    assert iso == msk(2026, 7, 13, 12).isoformat()
    assert text == "13.07.2026 12:00 МСК"


# --- env tunables --------------------------------------------------------------------------

def test_zoom_min_gap_is_env_tunable(monkeypatch):
    monkeypatch.setenv("ZOOM_LEAD_MIN_GAP_HOURS", "1")
    # With a 1-hour minimum, 16:30 still fits today.
    assert bh.zoom_lead_deadline_at(msk(2026, 7, 8, 16, 30)) == msk(2026, 7, 8, 18)
