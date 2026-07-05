"""cron_schedule — the dependency-free cron engine behind agent automations."""
from __future__ import annotations

from datetime import datetime

import pytest

import cron_schedule as cs


def dt(y=2026, mo=7, d=6, h=9, mi=0):  # 2026-07-06 is a Monday
    return datetime(y, mo, d, h, mi)


class TestParse:
    def test_rejects_wrong_field_count(self):
        with pytest.raises(ValueError):
            cs.parse("0 9 * *")
        with pytest.raises(ValueError):
            cs.parse("")

    def test_rejects_out_of_range(self):
        with pytest.raises(ValueError):
            cs.parse("60 * * * *")
        with pytest.raises(ValueError):
            cs.parse("* 24 * * *")
        with pytest.raises(ValueError):
            cs.parse("* * 0 * *")

    def test_dow_seven_is_sunday(self):
        assert cs.parse("0 9 * * 7")[4] == {0}

    def test_lists_ranges_steps(self):
        assert cs.parse("*/15 * * * *")[0] == {0, 15, 30, 45}
        assert cs.parse("0 9-11 * * *")[1] == {9, 10, 11}
        assert cs.parse("0 9 * * 1,3,5")[4] == {1, 3, 5}


class TestMatches:
    def test_daily_time(self):
        assert cs.matches("0 18 * * *", dt(h=18, mi=0))
        assert not cs.matches("0 18 * * *", dt(h=18, mi=1))
        assert not cs.matches("0 18 * * *", dt(h=17, mi=0))

    def test_weekday_filter(self):
        assert cs.matches("0 9 * * 1-5", dt())  # Monday
        assert not cs.matches("0 9 * * 1-5", dt(d=5))  # Sunday 2026-07-05
        assert cs.matches("0 9 * * 0", dt(d=5))

    def test_owner_daily_skips_friday(self):
        # the live owner-daily schedule: every day except Friday at 18:00
        expr = "0 18 * * 0-4,6"
        assert not cs.matches(expr, dt(d=10, h=18))  # 2026-07-10 is a Friday
        assert cs.matches(expr, dt(d=9, h=18))  # Thursday

    def test_every_five_minutes(self):
        assert cs.matches("*/5 * * * *", dt(mi=55))
        assert not cs.matches("*/5 * * * *", dt(mi=56))

    def test_dom_dow_vixie_or(self):
        # both restricted -> fires when EITHER matches
        expr = "0 9 13 * 5"
        assert cs.matches(expr, dt(d=13, h=9))  # the 13th (a Monday)
        assert cs.matches(expr, dt(d=10, h=9))  # a Friday, not the 13th
        assert not cs.matches(expr, dt(d=14, h=9))


class TestNextRun:
    def test_next_daily(self):
        assert cs.next_run("0 18 * * *", dt(h=9)) == dt(h=18)
        assert cs.next_run("0 18 * * *", dt(h=19)) == dt(d=7, h=18)

    def test_strictly_after(self):
        assert cs.next_run("0 9 * * *", dt(h=9, mi=0)) == dt(d=7, h=9)

    def test_next_weekly(self):
        # next Friday 18:00 from Monday
        assert cs.next_run("0 18 * * 5", dt()) == dt(d=10, h=18)

    def test_impossible_date_returns_none(self):
        assert cs.next_run("0 0 30 2 *", dt()) is None


class TestGuards:
    def test_max_fires_per_day(self):
        assert cs.max_fires_per_day("0 9 * * *") == 1
        assert cs.max_fires_per_day("*/15 * * * *") == 96
        assert cs.max_fires_per_day("*/5 * * * *") == 288
        assert cs.max_fires_per_day("0 * * * *") == 24


class TestDescribe:
    def test_labels(self):
        assert cs.describe("0 9 * * *") == "ежедневно в 9:00"
        assert cs.describe("0 9 * * 1-5") == "по будням в 9:00"
        assert cs.describe("0 19 * * 3") == "по средам в 19:00"
        assert cs.describe("*/5 * * * *") == "каждые 5 мин"
        assert cs.describe("0 18 * * 0-4,6") == "ежедневно, кроме пт, в 18:00"
        assert cs.describe("0 * * * *") == "каждый час в :00"
        assert cs.describe("0 10 1 * *") == "1-го числа в 10:00"

    def test_invalid_falls_back_to_raw(self):
        assert cs.describe("не крон") == "не крон"
