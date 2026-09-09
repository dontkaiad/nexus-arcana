"""tests/test_booking_api.py — /api/booking/{public,slots} + principal (#23 / ADR-0026)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from core.booking.busy import BusyInterval
from core.booking.slots import Slot
from miniapp.backend.app import app

UTC = timezone.utc
T0 = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
FRIEND_TG = 555001


@pytest.fixture
def _env():
    ivs = [
        BusyInterval(T0, T0 + timedelta(hours=1), "task", "x", "1"),
        BusyInterval(T0 + timedelta(minutes=30), T0 + timedelta(hours=2), "block", "y", "2"),
    ]
    slots = [Slot(T0 + timedelta(hours=5), T0 + timedelta(hours=6))]
    with patch("miniapp.backend.routes.booking._owner_user_id", AsyncMock(return_value="uid-1")), \
         patch("miniapp.backend.routes.booking.busy_intervals", AsyncMock(return_value=ivs)), \
         patch("miniapp.backend.routes.booking.free_slots", AsyncMock(return_value=slots)), \
         patch("core.config.config.booking_service_token", "svc-secret"):
        yield TestClient(app)


def test_public_is_open_and_opaque(_env):
    r = _env.get("/api/booking/public?context=friends")
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "guest"
    # two overlapping busy intervals → one merged block, no labels
    assert len(body["busy"]) == 1
    assert set(body["busy"][0]) == {"start", "end"}


def test_public_bad_context_400(_env):
    assert _env.get("/api/booking/public?context=nope").status_code == 400


def test_slots_friends_requires_grant_guest_403(_env):
    assert _env.get("/api/booking/slots?context=friends").status_code == 403


def test_slots_friends_ok_for_friend(_env):
    with patch("miniapp.backend.routes.booking.booking_role", AsyncMock(return_value="friend")):
        r = _env.get(
            "/api/booking/slots?context=friends",
            headers={"X-Booking-Service-Token": "svc-secret", "X-Booking-As-Tg": str(FRIEND_TG)},
        )
    assert r.status_code == 200
    assert r.json()["role"] == "friend"
    assert len(r.json()["slots"]) == 1


def test_slots_arcana_is_public(_env):
    r = _env.get("/api/booking/slots?context=arcana")
    assert r.status_code == 200
    assert r.json()["context"] == "arcana"


def test_service_token_mismatch_falls_back_to_guest(_env):
    r = _env.get(
        "/api/booking/slots?context=friends",
        headers={"X-Booking-Service-Token": "wrong", "X-Booking-As-Tg": str(FRIEND_TG)},
    )
    assert r.status_code == 403  # not trusted → guest → blocked
