"""tests/test_recurrence_monthly.py — next_cycle_date для «Ежемесячно» держит число месяца от старой даты.

Раньше база была max(old, today): запись на 25-е, отмеченная 27-го, переезжала на 27-е насовсем.
Время «сегодня» подменяется, названия вымышленные.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import core.recurrence as R

MSK = timezone(timedelta(hours=3))


class _FrozenDT(datetime):
    frozen = None

    @classmethod
    def now(cls, tz=None):
        return cls.frozen.astimezone(tz) if tz else cls.frozen


@pytest.fixture
def at(monkeypatch):
    monkeypatch.setattr(R, "datetime", _FrozenDT)

    def _at(stamp: str):
        _FrozenDT.frozen = datetime.strptime(stamp, "%Y-%m-%d %H:%M").replace(tzinfo=MSK)
    return _at


def nxt(cur, repeat="Ежемесячно", **kw):
    return R.next_cycle_date(cur, repeat, 3, **kw)


def test_done_on_the_day_goes_to_next_month_same_day(at):
    at("2026-11-25 16:05")
    assert nxt("2026-11-25T16:00") == "2026-12-25T16:00"


def test_done_late_keeps_the_25th(at):
    at("2026-11-27 18:00")
    assert nxt("2026-11-25T16:00") == "2026-12-25T16:00"          # раньше было 2026-12-27
    at("2026-11-26 09:00")
    assert nxt("2026-11-25T16:00") == "2026-12-25T16:00"


def test_done_early_before_the_day(at):
    at("2026-11-05 10:00")
    assert nxt("2026-11-25T16:00") == "2026-12-25T16:00"


def test_late_drift_does_not_accumulate_over_months(at):
    cur = "2026-11-25T16:00"
    for today, expect in [("2026-11-28 12:00", "2026-12-25T16:00"), ("2026-12-29 12:00", "2027-01-25T16:00"),
                          ("2027-01-26 12:00", "2027-02-25T16:00")]:
        at(today)
        cur = nxt(cur)
        assert cur == expect


def test_year_change(at):
    at("2026-12-25 16:05")
    assert nxt("2026-12-25T16:00") == "2027-01-25T16:00"


def test_stale_by_more_than_a_month_jumps_to_first_future_occurrence(at):
    at("2026-12-03 10:00")
    assert nxt("2026-10-25T16:00") == "2026-12-25T16:00"           # 25 нояб. уже прошло
    at("2026-12-25 10:00")
    assert nxt("2026-10-25T16:00") == "2027-01-25T16:00"           # строго после сегодня


def test_31st_clamps_in_short_month(at):
    at("2027-01-31 12:00")
    assert nxt("2027-01-31") == "2027-02-28"
    at("2028-01-31 12:00")
    assert nxt("2028-01-31") == "2028-02-29"                       # високосный
    at("2027-03-31 12:00")
    assert nxt("2027-03-31") == "2027-04-30"


def test_31st_returns_after_short_month_when_anchor_known(at):
    at("2027-02-28 12:00")
    assert nxt("2027-02-28", anchor_day=31) == "2027-03-31"
    at("2027-04-30 12:00")
    assert nxt("2027-04-30", anchor_day=31) == "2027-05-31"
    at("2027-11-30 12:00")
    assert nxt("2027-11-30", anchor_day=31) == "2027-12-31"


def test_without_anchor_uses_old_day(at):
    at("2027-02-28 12:00")
    assert nxt("2027-02-28") == "2027-03-28"                       # число старой даты, анкера нет


def test_time_part_and_override_time(at):
    at("2026-11-27 18:00")
    assert nxt("2026-11-25T16:00", override_time="17:30") == "2026-12-25T17:30"
    assert nxt("2026-11-25") == "2026-12-25"                       # дата без времени остаётся датой


def test_old_date_in_the_utc_evening_uses_local_day(at):
    at("2026-11-25 16:05")
    assert nxt("2026-11-25T13:00:00+00:00") == "2026-12-25T16:00"  # 13:00 UTC = 16:00 МСК, тот же день


# ── Остальные повторы без изменений ──────────────────────────────────────────

def test_daily_unchanged(at):
    at("2026-11-05 10:00")
    assert nxt("2026-11-01", "Ежедневно") == "2026-11-06"          # base = max(old, today)
    assert nxt("2026-11-10", "Ежедневно") == "2026-11-11"
    assert nxt("2026-11-10", "Ежедневно", interval_days=2) == "2026-11-12"


def test_weekly_unchanged(at):
    at("2026-11-27 12:00")
    assert nxt("2026-11-25", "Еженедельно") == "2026-12-04"        # от сегодня, как раньше
    at("2026-11-05 12:00")
    assert nxt("2026-11-25", "Еженедельно") == "2026-12-02"


def test_unknown_repeat_unchanged(at):
    at("2026-11-05 10:00")
    assert nxt("2026-11-05", "Нет") == "2026-11-06"
