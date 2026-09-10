"""zarya/scheduler.py — booking reminders (T-24h + T-2h), both sides (#23 Z2).

In-memory APScheduler, same fragility model as nexus reminders
([[nexus-reminder-scheduler-fragility]]): PG `booking` is the source of truth,
jobs are lost on restart → `restore_on_startup()` re-arms every future
confirmed booking, and a 5-min sweep re-arms anything that slipped (job
missed while the bot was down, DB row added by the web while Zarya restarted).

Each confirmed booking gets up to two jobs:
  bk:<id>:24  — fires at start − 24h
  bk:<id>:2   — fires at start − 2h
Each fires a DM to the requester AND to every owner tg_id.
Cancelling a booking (`cancel(id)`) drops both jobs.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from core.booking.repo import list_bookings
from core.config import config
from zarya.formatting import MSK

logger = logging.getLogger("zarya.scheduler")

_LEADS = ((24, "за сутки"), (2, "через 2 часа"))
_SWEEP_MIN = 5
_UTC = timezone.utc

_scheduler: "AsyncIOScheduler | None" = None
_bot = None


def _job_id(booking_id: int, hours: int) -> str:
    return f"bk:{booking_id}:{hours}"


async def _send(tg_id: int, text: str) -> None:
    if not (_bot and tg_id):
        return
    try:
        await _bot.send_message(tg_id, text, disable_web_page_preview=True)
    except Exception as e:  # noqa: BLE001 — a blocked/deleted chat must not kill the job
        logger.warning("reminder send to %s failed: %s", tg_id, e)


async def _fire(booking_id: int, phrase: str) -> None:
    from core.booking.repo import get_booking

    b = await get_booking(booking_id=booking_id)
    if not b or b.status != "confirmed":
        return
    when = b.start_at.astimezone(MSK).strftime("%d.%m в %H:%M")
    kb = _cancel_kb(b.id)
    await _send(
        b.requester_tg_id or 0,
        f"⏰ Напоминание: встреча с Кай <b>{phrase}</b> — {when} (МСК).\n"
        f"Если не сможешь — отмени 👇",
    )
    for owner in config.allowed_ids:
        await _send(
            owner,
            f"⏰ <b>{b.requester_name}</b> {phrase} — {when} (МСК)"
            + (f"\n💬 {b.note}" if b.note else ""),
        )


def _cancel_kb(booking_id: int):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Отменить бронь", callback_data=f"z:cx:{booking_id}"),
    ]])


def init(bot, scheduler: "AsyncIOScheduler | None" = None) -> None:
    global _scheduler, _bot
    _bot = bot
    _scheduler = scheduler or AsyncIOScheduler(timezone=_UTC)
    if not _scheduler.running:
        _scheduler.start()


def schedule(b) -> None:
    """(Re-)arm both reminders for a confirmed booking. Past leads are skipped."""
    if not _scheduler or b.status != "confirmed":
        return
    now = datetime.now(_UTC)
    for hours, phrase in _LEADS:
        fire_at = b.start_at - timedelta(hours=hours)
        jid = _job_id(b.id, hours)
        if fire_at <= now:
            continue
        try:
            _scheduler.add_job(
                _fire, DateTrigger(run_date=fire_at),
                args=[b.id, phrase], id=jid, replace_existing=True,
                misfire_grace_time=3600,
            )
        except Exception as e:  # noqa: BLE001
            logger.error("add_job %s failed: %s", jid, e)


def cancel(booking_id: int) -> None:
    if not _scheduler:
        return
    for hours, _ in _LEADS:
        try:
            _scheduler.remove_job(_job_id(booking_id, hours))
        except Exception:  # noqa: BLE001 — job may already be gone / fired
            pass


async def restore_on_startup() -> None:
    """Re-arm every future confirmed booking + start the periodic sweep."""
    if not _scheduler:
        return
    try:
        rows = await list_bookings(statuses=("confirmed",), upcoming_only=True)
        for b in rows:
            schedule(b)
        logger.info("restored booking reminders for %d confirmed bookings", len(rows))
    except Exception as e:  # noqa: BLE001
        logger.error("restore_on_startup failed: %s", e)
    try:
        _scheduler.add_job(
            _sweep, IntervalTrigger(minutes=_SWEEP_MIN),
            id="bk:sweep", replace_existing=True,
        )
    except Exception as e:  # noqa: BLE001
        logger.error("sweep add_job failed: %s", e)


async def _sweep() -> None:
    """Belt-and-braces: re-arm confirmed bookings whose jobs are missing
    (added via web while Zarya was down, or a lost in-memory job)."""
    if not _scheduler:
        return
    try:
        rows = await list_bookings(statuses=("confirmed",), upcoming_only=True)
    except Exception as e:  # noqa: BLE001
        logger.warning("sweep list failed: %s", e)
        return
    for b in rows:
        for hours, _ in _LEADS:
            fire_at = b.start_at - timedelta(hours=hours)
            if fire_at > datetime.now(_UTC) and not _scheduler.get_job(_job_id(b.id, hours)):
                schedule(b)
                break
