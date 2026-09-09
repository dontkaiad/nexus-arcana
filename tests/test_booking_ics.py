"""tests/test_booking_ics.py — .ics writer + feed route (#23 B3 / ADR-0026)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from core.booking.busy import BusyInterval
from core.booking.ics import IcsEvent, build_ics
from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id
from miniapp.backend.routes.booking import feed_token

UTC = timezone.utc
T0 = datetime(2026, 9, 15, 9, 0, tzinfo=UTC)
FAKE_TG = 999001
FAKE_UID = "owner-uid-1"


# ── ICS writer ──────────────────────────────────────────────────────────────

def test_build_ics_structure_and_utc_format():
    ev = IcsEvent(uid="a@heylark", start=T0, end=T0 + timedelta(hours=1), summary="созвон")
    out = build_ics([ev])
    assert out.startswith("BEGIN:VCALENDAR\r\n")
    assert out.rstrip().endswith("END:VCALENDAR")
    assert "BEGIN:VEVENT\r\n" in out
    assert "DTSTART:20260915T090000Z" in out
    assert "DTEND:20260915T100000Z" in out
    assert "SUMMARY:созвон" in out
    assert "UID:a@heylark" in out


def test_build_ics_escapes_special_chars():
    ev = IcsEvent(uid="b", start=T0, end=T0, summary="кофе, чай; с\\кем-то\nвторая строка")
    out = build_ics([ev])
    assert "SUMMARY:кофе\\, чай\\; с\\\\кем-то\\nвторая строка" in out


def test_build_ics_folds_long_lines():
    long = "x" * 200
    out = build_ics([IcsEvent(uid="c", start=T0, end=T0, summary=long)])
    for line in out.split("\r\n"):
        assert len(line.encode("utf-8")) <= 75


# ── feed route ──────────────────────────────────────────────────────────────

@pytest.fixture
def _feed_env():
    ivs = [
        BusyInterval(T0, T0 + timedelta(hours=1), "task", "сдать отчёт", "7"),
        BusyInterval(T0 + timedelta(hours=3), T0 + timedelta(hours=4), "work", "расклад", "12"),
        BusyInterval(T0 + timedelta(hours=6), T0 + timedelta(hours=7), "booking", "Аня", "3"),
        BusyInterval(T0 + timedelta(days=1), T0 + timedelta(days=2), "block", "отпуск", "1"),
    ]
    with patch("core.config.config.allowed_ids", [FAKE_TG]), \
         patch("core.config.config.session_secret", "test-secret"), \
         patch("core.user_manager.get_user_id", AsyncMock(return_value=FAKE_UID)), \
         patch("miniapp.backend.routes.booking.busy_intervals", AsyncMock(return_value=ivs)):
        yield TestClient(app)


def test_feed_valid_token_returns_calendar(_feed_env):
    tok = feed_token(FAKE_UID)
    r = _feed_env.get(f"/feed/{tok}.ics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/calendar")
    body = r.text
    assert body.count("BEGIN:VEVENT") == 4
    assert "SUMMARY:сдать отчёт" in body
    assert "SUMMARY:🔮 расклад" in body
    assert "SUMMARY:🗓 Аня" in body
    assert "SUMMARY:отпуск" in body


def test_feed_bad_token_404(_feed_env):
    assert _feed_env.get("/feed/deadbeef.ics").status_code == 404


def test_feed_url_endpoint_roundtrips(_feed_env):
    app.dependency_overrides[current_user_id] = lambda: FAKE_TG
    try:
        r = _feed_env.get("/api/booking/feed-url")
        assert r.status_code == 200
        url = r.json()["url"]
        assert url.endswith(".ics") and "/feed/" in url
        tok = url.rsplit("/feed/", 1)[1][:-4]
        assert _feed_env.get(f"/feed/{tok}.ics").status_code == 200
    finally:
        app.dependency_overrides.clear()
