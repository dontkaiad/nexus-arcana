"""Unit tests для miniapp/backend/_moon.py."""
from __future__ import annotations

from datetime import datetime, timezone

from miniapp.backend._moon import moon_phase, MOON_GLYPHS, MOON_NAMES


def test_moon_phase_at_known_new_moon():
    """2000-01-06 18:14 UTC — известное новолуние."""
    dt = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
    p = moon_phase(dt)
    assert p["idx"] == 0
    assert p["glyph"] == "🌑"
    assert p["name"] == "Новолуние"
    assert p["illum"] == 0


def test_moon_phase_at_full_moon():
    """~14.77 дней после новолуния → полнолуние."""
    dt = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
    # сдвигаем на половину синодического месяца
    from datetime import timedelta
    dt_full = dt + timedelta(days=14.765294)
    p = moon_phase(dt_full)
    assert p["idx"] == 4
    assert p["glyph"] == "🌕"
    assert p["name"] == "Полнолуние"
    assert 98 <= p["illum"] <= 100


def test_moon_phase_waxing_between_new_and_full():
    """~7 дней после новолуния → первая четверть, растущая."""
    from datetime import timedelta
    dt = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc) + timedelta(days=7.38)
    p = moon_phase(dt)
    assert p["idx"] == 2
    assert p["glyph"] == "🌓"
    assert p["name"] == "Первая четверть"


def test_moon_phase_waning_between_full_and_new():
    """~22 дня после новолуния → последняя четверть, убывающая."""
    from datetime import timedelta
    dt = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc) + timedelta(days=22.15)
    p = moon_phase(dt)
    assert p["idx"] == 6
    assert p["glyph"] == "🌗"
    assert p["name"] == "Последняя четверть"


def test_moon_phase_naive_datetime_treated_as_utc():
    """Наивный datetime без tz должен интерпретироваться как UTC."""
    naive = datetime(2000, 1, 6, 18, 14)
    aware = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
    assert moon_phase(naive) == moon_phase(aware)


def test_moon_phase_glyphs_and_names_are_synced():
    """Индексы glyphs/names совпадают по длине и порядку."""
    assert len(MOON_GLYPHS) == 8 == len(MOON_NAMES)
    # Новолуние / Полнолуние в ожидаемых позициях
    assert MOON_GLYPHS[0] == "🌑" and MOON_NAMES[0] == "Новолуние"
    assert MOON_GLYPHS[4] == "🌕" and MOON_NAMES[4] == "Полнолуние"
