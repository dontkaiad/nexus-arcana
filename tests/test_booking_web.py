"""tests/test_booking_web.py — B5: role-aware /calendar, /me, /tip, root page (#23)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from core.booking.busy import BusyInterval
from core.booking.tips import compute_tip
from miniapp.backend.app import app

UTC = timezone.utc
T0 = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


# ── tips (pure) ─────────────────────────────────────────────────────────────

def test_tip_no_windows():
    assert "не настроены" in compute_tip([])


def test_tip_afternoon_person():
    w = [{"context": "friends", "active": True, "weekday": 5, "start_time": "14:00", "end_time": "20:00"}]
    t = compute_tip(w)
    assert "не жаворонок" in t and "выходным" in t


def test_tip_morning():
    w = [{"context": "friends", "active": True, "weekday": 1, "start_time": "08:00", "end_time": "11:00"}]
    assert "Утренние" in compute_tip(w)


def test_tip_context_filter():
    w = [{"context": "arcana", "active": True, "weekday": 2, "start_time": "18:00", "end_time": "21:00"}]
    assert "не настроены" in compute_tip(w, context="friends")
    assert "не настроены" not in compute_tip(w, context="arcana")


# ── routes ─────────────────────────────────────────────────────────────────

@pytest.fixture
def _env():
    ivs = [
        BusyInterval(T0, T0 + timedelta(hours=1), "task", "юрист", "1"),
        BusyInterval(T0 + timedelta(hours=3), T0 + timedelta(hours=4), "work", "расклад Ане", "2"),
        BusyInterval(T0 + timedelta(hours=6), T0 + timedelta(hours=7), "booking", "Петя", "3"),
    ]
    with patch("miniapp.backend.routes.booking._owner_user_id", AsyncMock(return_value="uid-1")), \
         patch("miniapp.backend.routes.booking.busy_intervals", AsyncMock(return_value=ivs)), \
         patch("miniapp.backend.routes.booking.bkrepo.list_availability", AsyncMock(return_value=[])):
        yield TestClient(app)


def test_calendar_guest_is_opaque(_env):
    r = _env.get("/api/booking/calendar")
    assert r.status_code == 200
    ev = r.json()["events"]
    assert len(ev) == 3
    assert "kind" not in ev[0] and "title" not in ev[0]
    assert r.json()["role"] == "guest"


def test_calendar_friend_gets_kind_no_title(_env):
    with patch("miniapp.backend.routes.booking.booking_role", AsyncMock(return_value="friend")), \
         patch("core.config.config.booking_service_token", "s"):
        r = _env.get("/api/booking/calendar",
                     headers={"X-Booking-Service-Token": "s", "X-Booking-As-Tg": "5"})
    ev = r.json()["events"]
    assert {e["kind"] for e in ev} == {"nexus", "arcana", "booking"}
    assert all("title" not in e for e in ev)


def test_calendar_admin_gets_title(_env):
    with patch("core.config.config.allowed_ids", [111]), \
         patch("core.config.config.booking_service_token", "s"):
        r = _env.get("/api/booking/calendar",
                     headers={"X-Booking-Service-Token": "s", "X-Booking-As-Tg": "111"})
    assert r.json()["role"] == "admin"
    titles = {e.get("title") for e in r.json()["events"]}
    assert "юрист" in titles


def test_me_and_tip(_env):
    assert _env.get("/api/booking/me").json()["role"] == "guest"
    assert "tip" in _env.get("/api/booking/tip").json()


def test_root_serves_zarya_for_booking_host(_env):
    r = _env.get("/", headers={"host": "booking.heylark.dev"})
    assert r.status_code == 200
    assert "Календарь Кай" in r.text and "/api/booking" in r.text
