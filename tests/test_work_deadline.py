"""tests/test_work_deadline.py — _compute_deadline + keyboards."""
from datetime import datetime, timedelta, timezone

from arcana.handlers.work_kb import (
    _compute_deadline, deadline_keyboard, reminder_keyboard,
)


def test_today_deadline_at_2359_local():
    iso = _compute_deadline("today", 3)
    assert iso is not None
    assert "T23:59:00" in iso
    # часовой пояс +03:00 в строке формата %z
    assert iso.endswith("+0300") or "+03" in iso


def test_tomorrow_deadline_one_day_ahead():
    today = _compute_deadline("today", 3)
    tomorrow = _compute_deadline("tomorrow", 3)
    assert today is not None and tomorrow is not None
    # tomorrow > today
    today_dt = datetime.strptime(today, "%Y-%m-%dT%H:%M:%S%z")
    tomorrow_dt = datetime.strptime(tomorrow, "%Y-%m-%dT%H:%M:%S%z")
    delta = tomorrow_dt - today_dt
    # ровно 24 часа
    assert delta == timedelta(hours=24)


def test_week_deadline_within_7_days():
    today = _compute_deadline("today", 3)
    week = _compute_deadline("week", 3)
    today_dt = datetime.strptime(today, "%Y-%m-%dT%H:%M:%S%z")
    week_dt = datetime.strptime(week, "%Y-%m-%dT%H:%M:%S%z")
    delta_days = (week_dt - today_dt).days
    assert 0 < delta_days <= 7


def test_none_returns_none():
    assert _compute_deadline("none", 3) is None
    assert _compute_deadline("garbage", 3) is None


def test_deadline_keyboard_4_buttons():
    kb = deadline_keyboard("aaaa-bbbb-cccc-dddd")
    flat = [b for row in kb.inline_keyboard for b in row]
    assert len(flat) == 4
    cbs = [b.callback_data for b in flat]
    assert any("today" in c for c in cbs)
    assert any("tomorrow" in c for c in cbs)
    assert any("week" in c for c in cbs)
    assert any("none" in c for c in cbs)


def test_reminder_keyboard_3_options():
    kb = reminder_keyboard("aaaa-bbbb")
    flat = [b for row in kb.inline_keyboard for b in row]
    assert len(flat) == 3
    cbs = [b.callback_data for b in flat]
    assert any("24h" in c for c in cbs)
    assert any("3h" in c for c in cbs)
    assert any("none" in c for c in cbs)
