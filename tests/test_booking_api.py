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


# ── /book + approval ────────────────────────────────────────────────────────

from core.booking.repo import Booking as _Bk  # noqa: E402

_FUT = (datetime.now(UTC) + timedelta(days=3)).replace(microsecond=0)


def _mk_booking(status="confirmed", bid=7, **kw):
    d = dict(id=bid, context="friends", meeting_type_id=None, requester_tg_id=None,
             requester_name="Аня", requester_contact="", start_at=_FUT,
             end_at=_FUT + timedelta(hours=1), hours=1.0, status=status, note="",
             source="web", token="tok-abc", nexus_task_id=None, arcana_work_id=None,
             created_at=None, decided_at=None)
    d.update(kw)
    return _Bk(**d)


@pytest.fixture
def _book_env():
    with patch("miniapp.backend.routes.booking._owner_user_id", AsyncMock(return_value="uid-1")), \
         patch("miniapp.backend.routes.booking.busy_intervals", AsyncMock(return_value=[])), \
         patch("miniapp.backend.routes.booking.create_booking", AsyncMock(return_value=_mk_booking())), \
         patch("core.bot_notify.notify_booking_log", AsyncMock(return_value=True)), \
         patch("core.config.config.booking_service_token", "svc-secret"):
        yield TestClient(app)


def test_book_friends_needs_grant_guest_403(_book_env):
    r = _book_env.post("/api/booking/book", json={"context": "friends", "start": _FUT.isoformat()})
    assert r.status_code == 403


def test_book_friends_auto_confirms_for_friend(_book_env):
    with patch("miniapp.backend.routes.booking.booking_role", AsyncMock(return_value="friend")):
        r = _book_env.post(
            "/api/booking/book",
            json={"context": "friends", "start": _FUT.isoformat(), "hours": 2},
            headers={"X-Booking-Service-Token": "svc-secret", "X-Booking-As-Tg": "555001"},
        )
    assert r.status_code == 200
    assert r.json()["status"] == "confirmed"
    assert r.json()["token"] == "tok-abc"


def test_book_arcana_is_public_and_pending(_book_env):
    with patch("miniapp.backend.routes.booking.create_booking",
               AsyncMock(return_value=_mk_booking(status="pending", context="arcana"))):
        r = _book_env.post("/api/booking/book",
                           json={"context": "arcana", "start": _FUT.isoformat(),
                                 "requester_name": "Клиент", "requester_contact": "@x"})
    assert r.status_code == 200
    assert r.json()["status"] == "pending"


def test_book_past_start_400(_book_env):
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    with patch("miniapp.backend.routes.booking.booking_role", AsyncMock(return_value="admin")):
        r = _book_env.post("/api/booking/book",
                           json={"context": "friends", "start": past},
                           headers={"X-Booking-Service-Token": "svc-secret", "X-Booking-As-Tg": "1"})
    assert r.status_code == 400


def test_book_slot_conflict_409(_book_env):
    from core.booking.busy import BusyInterval
    clash = [BusyInterval(_FUT, _FUT + timedelta(hours=1), "task", "x", "1")]
    with patch("miniapp.backend.routes.booking.busy_intervals", AsyncMock(return_value=clash)), \
         patch("miniapp.backend.routes.booking.booking_role", AsyncMock(return_value="friend")):
        r = _book_env.post("/api/booking/book",
                           json={"context": "friends", "start": _FUT.isoformat()},
                           headers={"X-Booking-Service-Token": "svc-secret", "X-Booking-As-Tg": "1"})
    assert r.status_code == 409


def test_confirm_decline_owner_only():
    from miniapp.backend.auth import current_user_id
    with patch("miniapp.backend.routes.booking._owner_user_id", AsyncMock(return_value="uid-1")), \
         patch("miniapp.backend.routes.booking.set_booking_status",
               AsyncMock(return_value=_mk_booking(status="confirmed"))), \
         patch("core.bot_notify.notify_booking_log", AsyncMock(return_value=True)):
        c = TestClient(app)
        assert c.post("/api/booking/requests/7/confirm").status_code in (401, 403)
        app.dependency_overrides[current_user_id] = lambda: 111
        try:
            r = c.post("/api/booking/requests/7/confirm")
            assert r.status_code == 200 and r.json()["status"] == "confirmed"
        finally:
            app.dependency_overrides.clear()
