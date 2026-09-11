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
