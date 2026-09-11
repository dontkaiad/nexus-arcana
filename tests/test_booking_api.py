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
    r = _book_env.post("/api/booking/book", json={"context": "friends", "start": _FUT.isoformat(), "note": "созвон"})
    assert r.status_code == 403


def test_book_friends_auto_confirms_for_friend(_book_env):
    with patch("miniapp.backend.routes.booking.booking_role", AsyncMock(return_value="friend")):
        r = _book_env.post(
            "/api/booking/book",
            json={"context": "friends", "start": _FUT.isoformat(), "hours": 2, "note": "созвон"},
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
                           json={"context": "friends", "start": past, "note": "созвон"},
                           headers={"X-Booking-Service-Token": "svc-secret", "X-Booking-As-Tg": "1"})
    assert r.status_code == 400


def test_book_slot_conflict_409(_book_env):
    from core.booking.busy import BusyInterval
    clash = [BusyInterval(_FUT, _FUT + timedelta(hours=1), "task", "x", "1")]
    with patch("miniapp.backend.routes.booking.busy_intervals", AsyncMock(return_value=clash)), \
         patch("miniapp.backend.routes.booking.booking_role", AsyncMock(return_value="friend")):
        r = _book_env.post("/api/booking/book",
                           json={"context": "friends", "start": _FUT.isoformat(), "note": "созвон"},
                           headers={"X-Booking-Service-Token": "svc-secret", "X-Booking-As-Tg": "1"})
    assert r.status_code == 409


# ── #238: display-name override + личка владельцу для веб-брони ────────────

def test_book_web_uses_people_display_name_not_tg_id(_book_env):
    """Раньше веб-бронь без имени в форме сохраняла requester_name='tg:<id>' —
    Кай видела только айдишник, даже для гранченного друга с именем в people."""
    captured = {}

    async def fake_create_booking(**kw):
        captured.update(kw)
        return _mk_booking(requester_name=kw.get("requester_name", ""))

    with patch("miniapp.backend.routes.booking.booking_role", AsyncMock(return_value="friend")), \
         patch("miniapp.backend.routes.booking.create_booking", AsyncMock(side_effect=fake_create_booking)), \
         patch("core.auth_grants.get_display_name", AsyncMock(return_value="Мишган Роман")):
        r = _book_env.post(
            "/api/booking/book",
            json={"context": "friends", "start": _FUT.isoformat(), "hours": 1, "note": "шашлыки"},
            headers={"X-Booking-Service-Token": "svc-secret", "X-Booking-As-Tg": "555001"},
        )
    assert r.status_code == 200
    assert captured["requester_name"] == "Мишган Роман"


def test_book_web_falls_back_to_tg_id_without_override(_book_env):
    captured = {}

    async def fake_create_booking(**kw):
        captured.update(kw)
        return _mk_booking(requester_name=kw.get("requester_name", ""))

    with patch("miniapp.backend.routes.booking.booking_role", AsyncMock(return_value="friend")), \
         patch("miniapp.backend.routes.booking.create_booking", AsyncMock(side_effect=fake_create_booking)), \
         patch("core.auth_grants.get_display_name", AsyncMock(return_value=None)):
        r = _book_env.post(
            "/api/booking/book",
            json={"context": "friends", "start": _FUT.isoformat(), "hours": 1, "note": "шашлыки"},
            headers={"X-Booking-Service-Token": "svc-secret", "X-Booking-As-Tg": "555001"},
        )
    assert r.status_code == 200
    assert captured["requester_name"] == "tg:555001"


def test_book_web_dms_every_owner_not_just_log_topic(_book_env):
    """Раньше веб-бронь шла ТОЛЬКО в лог-топик — Кай видела запись только в
    логах, ни разу лично в чате."""
    with patch("miniapp.backend.routes.booking.booking_role", AsyncMock(return_value="friend")), \
         patch("core.auth_grants.get_display_name", AsyncMock(return_value="Мишган Роман")), \
         patch("core.config.config.allowed_ids", [111, 222]), \
         patch("core.bot_notify.notify_user", AsyncMock(return_value=True)) as notify_mock:
        r = _book_env.post(
            "/api/booking/book",
            json={"context": "friends", "start": _FUT.isoformat(), "hours": 1, "note": "шашлыки"},
            headers={"X-Booking-Service-Token": "svc-secret", "X-Booking-As-Tg": "555001"},
        )
    assert r.status_code == 200
    # notify_user также шлёт подтверждение самому бронирующему (555001) —
    # тут проверяем именно что ОБА owner-а из этого получили личку с именем.
    dmed = {c.args[0] for c in notify_mock.await_args_list}
    assert {111, 222} <= dmed
    owner_calls = [c for c in notify_mock.await_args_list if c.args[0] in (111, 222)]
    assert len(owner_calls) == 2
    for c in owner_calls:
        assert c.kwargs.get("bot") == "zarya"
        assert "Мишган Роман" in c.args[1]


# ── #239: обязательный повод встречи для friends, не для arcana ────────────

def test_book_friends_without_note_422(_book_env):
    """«Не просто встреча с тем-то, а НА ЧТО» — обязательно для друзей."""
    with patch("miniapp.backend.routes.booking.booking_role", AsyncMock(return_value="friend")):
        r = _book_env.post(
            "/api/booking/book",
            json={"context": "friends", "start": _FUT.isoformat(), "hours": 1},
            headers={"X-Booking-Service-Token": "svc-secret", "X-Booking-As-Tg": "555001"},
        )
    assert r.status_code == 422


def test_book_friends_blank_note_422(_book_env):
    with patch("miniapp.backend.routes.booking.booking_role", AsyncMock(return_value="friend")):
        r = _book_env.post(
            "/api/booking/book",
            json={"context": "friends", "start": _FUT.isoformat(), "hours": 1, "note": "   "},
            headers={"X-Booking-Service-Token": "svc-secret", "X-Booking-As-Tg": "555001"},
        )
    assert r.status_code == 422


def test_book_arcana_without_note_is_fine(_book_env):
    """Публичная эзо-запись не требует повода — там свой meeting_type."""
    with patch("miniapp.backend.routes.booking.create_booking",
               AsyncMock(return_value=_mk_booking(status="pending", context="arcana"))):
        r = _book_env.post("/api/booking/book",
                           json={"context": "arcana", "start": _FUT.isoformat(),
                                 "requester_name": "Клиент", "requester_contact": "@x"})
    assert r.status_code == 200


# ── #220 (B7): история броней + перенос времени ─────────────────────────────

def test_history_owner_only():
    from miniapp.backend.auth import current_user_id
    c = TestClient(app)
    assert c.get("/api/booking/history").status_code in (401, 403)


def test_history_returns_any_status_sorted_desc():
    from miniapp.backend.auth import current_user_id
    older = _mk_booking(bid=1, status="declined", start_at=_FUT - timedelta(days=5),
                        end_at=_FUT - timedelta(days=5) + timedelta(hours=1))
    newer = _mk_booking(bid=2, status="confirmed", start_at=_FUT)
    with patch("miniapp.backend.routes.booking._owner_user_id", AsyncMock(return_value="uid-1")), \
         patch("miniapp.backend.routes.booking.list_bookings",
               AsyncMock(return_value=[older, newer])):
        app.dependency_overrides[current_user_id] = lambda: 111
        try:
            r = TestClient(app).get("/api/booking/history")
        finally:
            app.dependency_overrides.clear()
    assert r.status_code == 200
    ids = [b["id"] for b in r.json()["bookings"]]
    assert ids == [2, 1]  # newest first


def test_reschedule_owner_only():
    c = TestClient(app)
    assert c.post("/api/booking/7/reschedule", json={"start": _FUT.isoformat()}).status_code in (401, 403)


def test_reschedule_moves_booking_and_relinks():
    from miniapp.backend.auth import current_user_id
    new_start = _FUT + timedelta(days=1)
    existing = _mk_booking(bid=7, status="confirmed", nexus_task_id="42")
    moved = _mk_booking(bid=7, status="confirmed", nexus_task_id="42",
                        start_at=new_start, end_at=new_start + timedelta(hours=1))
    with patch("miniapp.backend.routes.booking._owner_user_id", AsyncMock(return_value="uid-1")), \
         patch("miniapp.backend.routes.booking.get_booking", AsyncMock(return_value=existing)), \
         patch("miniapp.backend.routes.booking.busy_intervals", AsyncMock(return_value=[])), \
         patch("miniapp.backend.routes.booking.reschedule_booking",
               AsyncMock(return_value=moved)) as resched, \
         patch("core.booking.linkage.update_linked_time", AsyncMock()) as relink, \
         patch("core.bot_notify.notify_user", AsyncMock(return_value=True)):
        app.dependency_overrides[current_user_id] = lambda: 111
        try:
            r = TestClient(app).post("/api/booking/7/reschedule",
                                     json={"start": new_start.isoformat()})
        finally:
            app.dependency_overrides.clear()
    assert r.status_code == 200
    resched.assert_awaited_once()
    relink.assert_awaited_once_with(moved)
    assert r.json()["start"] == moved.start_at.isoformat()


def test_reschedule_conflict_409_excludes_self():
    """Новое время не должно ложно конфликтовать с СВОЕЙ же старой бронью."""
    from miniapp.backend.auth import current_user_id
    from core.booking.busy import BusyInterval
    existing = _mk_booking(bid=7, status="confirmed")
    new_start = _FUT + timedelta(days=1)
    self_iv = BusyInterval(new_start, new_start + timedelta(hours=1), "booking", "Аня", "7")
    other_iv = BusyInterval(new_start, new_start + timedelta(hours=1), "task", "x", "99")
    moved = _mk_booking(bid=7, status="confirmed", start_at=new_start, end_at=new_start + timedelta(hours=1))
    with patch("miniapp.backend.routes.booking._owner_user_id", AsyncMock(return_value="uid-1")), \
         patch("miniapp.backend.routes.booking.get_booking", AsyncMock(return_value=existing)), \
         patch("miniapp.backend.routes.booking.busy_intervals", AsyncMock(return_value=[self_iv])), \
         patch("miniapp.backend.routes.booking.reschedule_booking", AsyncMock(return_value=moved)), \
         patch("core.bot_notify.notify_user", AsyncMock(return_value=True)):
        app.dependency_overrides[current_user_id] = lambda: 111
        try:
            r = TestClient(app).post("/api/booking/7/reschedule",
                                     json={"start": new_start.isoformat()})
        finally:
            app.dependency_overrides.clear()
    assert r.status_code == 200  # self-overlap ignored

    with patch("miniapp.backend.routes.booking._owner_user_id", AsyncMock(return_value="uid-1")), \
         patch("miniapp.backend.routes.booking.get_booking", AsyncMock(return_value=existing)), \
         patch("miniapp.backend.routes.booking.busy_intervals", AsyncMock(return_value=[other_iv])):
        app.dependency_overrides[current_user_id] = lambda: 111
        try:
            r2 = TestClient(app).post("/api/booking/7/reschedule",
                                      json={"start": new_start.isoformat()})
        finally:
            app.dependency_overrides.clear()
    assert r2.status_code == 409  # a REAL conflict still blocks


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
