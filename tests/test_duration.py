"""tests/test_duration.py — core.duration parse/format (#241)."""
from __future__ import annotations

import pytest

from core.duration import format_duration, parse_duration_minutes


@pytest.mark.parametrize("text,expected", [
    ("2 часа", 120),
    ("30 минут", 30),
    ("1.5 часа", 90),
    ("1,5 часа", 90),
    ("1 час 30 минут", 90),
    ("полчаса", 30),
    ("час", 60),
    ("2ч", 120),
    ("45 мин", 45),
    ("1 час", 60),
    ("", None),
    ("   ", None),
    ("дичь", None),
    ("0 минут", None),
])
def test_parse_duration_minutes(text, expected):
    assert parse_duration_minutes(text) == expected


@pytest.mark.parametrize("minutes,expected", [
    (120, "2 ч"),
    (90, "1 ч 30 мин"),
    (30, "30 мин"),
    (0, "0 мин"),
])
def test_format_duration(minutes, expected):
    assert format_duration(minutes) == expected


# ── диапазон времени суток: «с 14 до 17» → длительность (Работы Arcana, ADR-0043 п. 8) ──

@pytest.mark.parametrize("text,minutes", [
    ("с 14 до 17", 180), ("с 14:30 до 16", 90), ("14-17", 180), ("с 9 до 9:45", 45),
])
def test_time_range_is_a_duration(text, minutes):
    from core.duration import parse_duration_minutes
    assert parse_duration_minutes(text) == minutes


@pytest.mark.parametrize("text,minutes", [
    ("10-12 минут", 12), ("2-3 часа", 180), ("с 9 до 10 часов", 600),    # после чисел стоит единица: не диапазон суток
    ("2 часа", 120), ("полчаса", 30),
])
def test_a_range_with_a_unit_is_not_read_as_clock_time(text, minutes):
    from core.duration import parse_duration_minutes
    assert parse_duration_minutes(text) == minutes


@pytest.mark.parametrize("text", ["с 17 до 14", "с 25 до 26", "до 17", "в 19:30"])
def test_nonsense_ranges_are_not_a_duration(text):
    from core.duration import parse_duration_minutes
    assert parse_duration_minutes(text) is None
