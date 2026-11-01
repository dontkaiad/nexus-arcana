from datetime import datetime, timezone

from core.date_extract import dt_to_local_wall, to_local_wall, wall_to_aware


def test_wall_to_aware_is_real_utc():
    dt = wall_to_aware("2026-11-01T19:45", 3)
    assert dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M") == "2026-11-01T16:45"


def test_roundtrip_wall_aware_wall():
    for tz in (3, 5, -5, 0):
        assert dt_to_local_wall(wall_to_aware("2026-11-01T19:30", tz), tz) == "2026-11-01T19:30"


def test_reminder_1930_msk_stored_as_utc_is_not_in_past():
    # строка в PG: 16:30 UTC = 19:30 МСК; на 16:17 UTC напоминание ещё в будущем
    stored = datetime(2026, 11, 1, 16, 30, tzinfo=timezone.utc)
    assert dt_to_local_wall(stored, 3) == "2026-11-01T19:30"


def test_edge_cases():
    assert wall_to_aware("", 3) is None and wall_to_aware("мусор", 3) is None
    assert wall_to_aware("2026-11-01", 3).hour == 0
    assert wall_to_aware("2026-11-01T19:45+00:00", 3).utcoffset().total_seconds() == 0
    assert to_local_wall("2026-11-01T16:30:00+00:00", 3) == "2026-11-01T19:30"
    assert dt_to_local_wall(None, 3) == ""
