"""tests/test_zarya_scheduler.py — booking reminders arm/skip/cancel/fire (#23 Z2)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import zarya.scheduler as sch

UTC = timezone.utc


class _FakeSched:
    def __init__(self):
        self.jobs = {}

    def add_job(self, fn, trigger, args=None, id=None, **kw):
        self.jobs[id] = SimpleNamespace(fn=fn, args=args, next_run_time=getattr(trigger, "run_date", None))

    def remove_job(self, jid):
        if jid not in self.jobs:
            raise KeyError(jid)
        del self.jobs[jid]

    def get_job(self, jid):
        return self.jobs.get(jid)


def _bk(**kw):
    d = dict(
        id=7, status="confirmed",
        start_at=datetime(2099, 1, 1, 12, 0, tzinfo=UTC),
        requester_tg_id=100, requester_name="Марина", note="", hours=2,
    )
    d.update(kw)
    return SimpleNamespace(**d)


@pytest.fixture(autouse=True)
def _isolate():
    prev_s, prev_b = sch._scheduler, sch._bot
    sch._scheduler = _FakeSched()
    yield
    sch._scheduler, sch._bot = prev_s, prev_b


def test_schedule_arms_both_leads():
    sch.schedule(_bk())
    assert set(sch._scheduler.jobs) == {"bk:7:24", "bk:7:2"}


def test_schedule_skips_past_leads():
    # meeting starts in 1h → both T-24h and T-2h are already in the past
    sch.schedule(_bk(start_at=datetime.now(UTC) + timedelta(hours=1)))
    assert sch._scheduler.jobs == {}


def test_schedule_noop_when_not_confirmed():
    sch.schedule(_bk(status="pending"))
    assert sch._scheduler.jobs == {}


def test_cancel_removes_both():
    sch.schedule(_bk())
    sch.cancel(7)
    assert sch._scheduler.jobs == {}


def test_cancel_is_safe_when_no_jobs():
    sch.cancel(999)  # must not raise


@pytest.mark.asyncio
async def test_fire_dms_requester_and_all_owners(monkeypatch):
    sent = []

    class _Bot:
        async def send_message(self, cid, text, **kw):
            sent.append(cid)

    sch._bot = _Bot()
    monkeypatch.setattr(sch.config, "allowed_ids", [111, 222])
    soon = _bk(start_at=datetime.now(UTC) + timedelta(hours=2))
    monkeypatch.setattr("core.booking.repo.get_booking", AsyncMock(return_value=soon))
    await sch._fire(7, "через 2 часа", 2)
    assert set(sent) == {100, 111, 222}


@pytest.mark.asyncio
async def test_fire_noop_if_booking_cancelled(monkeypatch):
    sent = []

    class _Bot:
        async def send_message(self, cid, text, **kw):
            sent.append(cid)

    sch._bot = _Bot()
    monkeypatch.setattr("core.booking.repo.get_booking",
                        AsyncMock(return_value=_bk(status="cancelled_by_owner")))
    await sch._fire(7, "за сутки", 24)
    assert sent == []


def _run_at(job):
    return job.next_run_time


@pytest.mark.asyncio
async def test_sweep_rearms_moved_booking_to_new_time(monkeypatch):
    b = _bk()
    sch.schedule(b)
    old = {k: _run_at(v) for k, v in sch._scheduler.jobs.items()}
    moved = _bk(start_at=b.start_at + timedelta(days=1))     # передвинули в БД
    monkeypatch.setattr(sch, "list_bookings", AsyncMock(return_value=[moved]))
    await sch._sweep()
    assert _run_at(sch._scheduler.jobs["bk:7:24"]) == moved.start_at - timedelta(hours=24)
    assert _run_at(sch._scheduler.jobs["bk:7:2"]) == moved.start_at - timedelta(hours=2)
    assert _run_at(sch._scheduler.jobs["bk:7:2"]) != old["bk:7:2"]


@pytest.mark.asyncio
async def test_sweep_leaves_correct_jobs_alone(monkeypatch):
    b = _bk()
    sch.schedule(b)
    before = dict(sch._scheduler.jobs)
    monkeypatch.setattr(sch, "list_bookings", AsyncMock(return_value=[b]))
    await sch._sweep()
    assert all(sch._scheduler.jobs[k] is before[k] for k in before)   # не пересоздавались


@pytest.mark.asyncio
async def test_sweep_tolerates_sub_minute_drift(monkeypatch):
    b = _bk()
    sch.schedule(b)
    before = dict(sch._scheduler.jobs)
    drifted = _bk(start_at=b.start_at + timedelta(seconds=30))
    monkeypatch.setattr(sch, "list_bookings", AsyncMock(return_value=[drifted]))
    await sch._sweep()
    assert all(sch._scheduler.jobs[k] is before[k] for k in before)


@pytest.mark.asyncio
async def test_sweep_drops_job_whose_lead_moved_into_the_past(monkeypatch):
    b = _bk()
    sch.schedule(b)
    # встречу придвинули к «через 1 час»: оба lead'а в прошлом, jobs должны уйти
    moved = _bk(start_at=datetime.now(UTC) + timedelta(hours=1))
    monkeypatch.setattr(sch, "list_bookings", AsyncMock(return_value=[moved]))
    await sch._sweep()
    assert sch._scheduler.jobs == {}


@pytest.mark.asyncio
async def test_fire_with_old_time_stays_silent(monkeypatch):
    sent = []

    class _Bot:
        async def send_message(self, cid, text, **kw):
            sent.append(cid)

    sch._bot = _Bot()
    # job армирован под старое время (старт через 2ч), а в БД бронь уже на завтра
    moved = _bk(start_at=datetime.now(UTC) + timedelta(days=1))
    monkeypatch.setattr("core.booking.repo.get_booking", AsyncMock(return_value=moved))
    await sch._fire(7, "через 2 часа", 2)
    assert sent == []


@pytest.mark.asyncio
async def test_schedule_passes_hours_to_fire():
    sch.schedule(_bk())
    assert sch._scheduler.jobs["bk:7:2"].args == [7, "через 2 часа", 2]
    assert sch._scheduler.jobs["bk:7:24"].args == [7, "за сутки", 24]
