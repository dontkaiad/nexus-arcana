"""miniapp/backend/routes/booking.py — heylark Booking API (#23 / ADR-0026).

- `feed_router` (no /api prefix): `GET /feed/<token>.ics` — the outbound
  subscription feed for Apple Calendar. Token is an HMAC of the owner's
  user_id under SESSION_SECRET; no login needed (Apple can't send cookies).
- `router` (/api prefix): `GET /api/booking/feed-url` (owner) hands Kai her
  feed URL. Slots / public / booking endpoints land in later phases (B5).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from core.auth_grants import booking_role
from core.booking.busy import BusyInterval, busy_intervals, merge_intervals
from core.booking.ics import IcsEvent, build_ics
from core.booking import repo as bkrepo
from core.booking.repo import (
    Booking, create_booking, get_booking, list_bookings, set_booking_status,
)
from core.booking.slots import free_slots
from core.config import config
from miniapp.backend import tg_auth
from miniapp.backend.auth import current_user_id, verify_init_data

logger = logging.getLogger("miniapp.booking")

router = APIRouter()
feed_router = APIRouter(include_in_schema=False)

_FEED_WINDOW_BACK = timedelta(days=7)
_FEED_WINDOW_FWD = timedelta(days=120)
_CONTEXTS = ("friends", "arcana")
_UTC = timezone.utc

_owner_uid_cache: Optional[str] = None


async def _owner_user_id() -> Optional[str]:
    """The single owner's user_id (booking data is owner-scoped)."""
    global _owner_uid_cache
    if _owner_uid_cache:
        return _owner_uid_cache
    from core.user_manager import get_user_id
    for tg_id in config.allowed_ids:
        try:
            uid = await get_user_id(tg_id)
        except Exception:
            uid = None
        if uid:
            _owner_uid_cache = uid
            return uid
    return None


@dataclass
class Principal:
    role: str  # 'admin' | 'friend' | 'guest'
    tg_id: Optional[int] = None


async def booking_principal(
    request: Request,
    x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data"),
    hl_session: Optional[str] = Cookie(None),
    x_booking_service_token: Optional[str] = Header(None, alias="X-Booking-Service-Token"),
    x_booking_as_tg: Optional[int] = Header(None, alias="X-Booking-As-Tg"),
) -> Principal:
    """Resolve the caller's booking role. A missing/invalid session is a
    valid `guest`, never a 401 — the public view needs no auth.

    Service path: the Zarya bot sends `X-Booking-Service-Token` (matches
    `BOOKING_SERVICE_TOKEN`) plus `X-Booking-As-Tg` — it has already checked
    `grants` itself; the API re-resolves the role and trusts it.
    """
    svc = config.booking_service_token
    if x_booking_service_token and svc and hmac.compare_digest(x_booking_service_token, svc):
        tg = x_booking_as_tg or 0
        role = await booking_role(tg) if tg else "guest"
        return Principal(role=role, tg_id=(tg or None))

    tg_id: Optional[int] = None
    if x_telegram_init_data:
        try:
            tg_id = verify_init_data(x_telegram_init_data)
        except ValueError:
            tg_id = None
    if tg_id is None and hl_session and config.session_secret:
        tg_id = tg_auth.read_session(hl_session, secret=config.session_secret)
    if tg_id is None:
        return Principal(role="guest")
    return Principal(role=await booking_role(tg_id), tg_id=tg_id)


def feed_token(user_id: str) -> str:
    secret = (config.session_secret or "dev-only-secret").encode("utf-8")
    return hmac.new(secret, f"booking-feed:{user_id}".encode("utf-8"),
                    hashlib.sha256).hexdigest()[:40]


async def _owner_for_token(token: str) -> Optional[str]:
    from core.user_manager import get_user_id
    for tg_id in config.allowed_ids:
        try:
            uid = await get_user_id(tg_id)
        except Exception:
            uid = None
        if uid and hmac.compare_digest(feed_token(uid), token):
            return uid
    return None


def _summary(iv: BusyInterval) -> str:
    if iv.source == "work":
        return f"🔮 {iv.label}" if iv.label else "🔮 Работа"
    if iv.source == "booking":
        return f"🗓 {iv.label}" if iv.label else "🗓 Бронь"
    if iv.source == "block":
        return iv.label or "Занято"
    return iv.label or "Задача"


@feed_router.get("/feed/{token}.ics")
async def booking_feed(token: str) -> Response:
    uid = await _owner_for_token(token)
    if not uid:
        raise HTTPException(status_code=404, detail="not found")
    now = datetime.now(timezone.utc)
    ivs = await busy_intervals(uid, now - _FEED_WINDOW_BACK, now + _FEED_WINDOW_FWD)
    events = [
        IcsEvent(
            uid=f"booking-{iv.source}-{iv.ref_id or id(iv)}@heylark",
            start=iv.start,
            end=iv.end,
            summary=_summary(iv),
        )
        for iv in ivs
    ]
    body = build_ics(events, cal_name="heylark Booking — Заря")
    return Response(
        content=body,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": 'inline; filename="booking.ics"',
            "Cache-Control": "no-cache",
        },
    )


@router.get("/booking/public")
async def booking_public(
    context: str = Query("friends"),
    days: int = Query(21, ge=1, le=90),
    p: Principal = Depends(booking_principal),
) -> dict:
    """Merged busy blocks (opaque — start/end only), for anyone."""
    if context not in _CONTEXTS:
        raise HTTPException(status_code=400, detail="bad context")
    uid = await _owner_user_id()
    if not uid:
        return {"busy": [], "role": p.role}
    now = datetime.now(_UTC)
    ivs = await busy_intervals(uid, now, now + timedelta(days=days))
    merged = merge_intervals(ivs)
    return {
        "busy": [{"start": s.isoformat(), "end": e.isoformat()} for s, e in merged],
        "role": p.role,
    }


@router.get("/booking/slots")
async def booking_slots(
    context: str = Query("friends"),
    days: int = Query(21, ge=1, le=90),
    p: Principal = Depends(booking_principal),
) -> dict:
    """Bookable slots. `friends` context needs a friend/admin role; `arcana`
    is public (esoteric clients aren't in `grants`)."""
    if context not in _CONTEXTS:
        raise HTTPException(status_code=400, detail="bad context")
    if context == "friends" and p.role not in ("friend", "admin"):
        raise HTTPException(status_code=403, detail="need a booking grant")
    uid = await _owner_user_id()
    if not uid:
        return {"slots": [], "role": p.role, "context": context}
    from datetime import date as _date
    today = _date.today()
    slots = await free_slots(uid, context, day_from=today, day_to=today + timedelta(days=days))
    return {
        "slots": [s.as_dict() for s in slots],
        "role": p.role,
        "context": context,
    }


class BookBody(BaseModel):
    context: str = "friends"
    start: str  # ISO datetime (a slot start)
    hours: float = Field(1.0, gt=0, le=12)
    meeting_type_id: Optional[int] = None
    requester_name: str = ""
    requester_contact: str = ""
    note: str = ""


def _parse_dt(s: str) -> datetime:
    v = (s or "").strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_UTC)
    return dt.astimezone(_UTC)


def _booking_view(b: Booking) -> dict:
    return {
        "id": b.id, "token": b.token, "status": b.status, "context": b.context,
        "start": b.start_at.isoformat(), "end": b.end_at.isoformat(),
        "requester_name": b.requester_name, "note": b.note,
    }


@router.post("/booking/book")
async def booking_book(body: BookBody, p: Principal = Depends(booking_principal)) -> dict:
    if body.context not in _CONTEXTS:
        raise HTTPException(status_code=400, detail="bad context")
    if body.context == "friends" and p.role not in ("friend", "admin"):
        raise HTTPException(status_code=403, detail="need a booking grant")

    try:
        start = _parse_dt(body.start)
    except ValueError:
        raise HTTPException(status_code=400, detail="bad start datetime")
    now = datetime.now(_UTC)
    if start <= now:
        raise HTTPException(status_code=400, detail="start is in the past")
    end = start + timedelta(hours=body.hours)

    uid = await _owner_user_id()
    if not uid:
        raise HTTPException(status_code=503, detail="owner not resolved")

    # conflict check — a holding booking is itself counted, so a repeat POST
    # for the same slot will 409 on the second call.
    ivs = await busy_intervals(uid, start, end)
    if any(iv.overlaps(start, end) for iv in ivs):
        raise HTTPException(status_code=409, detail="slot taken")

    status = "confirmed" if body.context == "friends" else "pending"
    name = (body.requester_name or "").strip()
    if not name and p.tg_id:
        name = f"tg:{p.tg_id}"
    b = await create_booking(
        user_id=uid, context=body.context, start_at=start, end_at=end,
        status=status, hours=body.hours, meeting_type_id=body.meeting_type_id,
        requester_tg_id=p.tg_id, requester_name=name or "гость",
        requester_contact=body.requester_contact.strip(), note=body.note.strip(),
        source="web",
    )

    try:
        from core.bot_notify import notify_booking_log
        when = start.astimezone(timezone(timedelta(hours=3))).strftime("%d.%m %H:%M")
        if status == "confirmed":
            await notify_booking_log(
                f"⭐ <b>{name}</b> записался · {when} ({body.hours:g}ч)"
                + (f"\n💬 {body.note}" if body.note else "")
            )
        else:
            await notify_booking_log(
                f"🃏 <b>Заявка</b> на эзо-запись · {name} · {when}\n"
                f"Подтверди: <code>POST /api/booking/requests/{b.id}/confirm</code>"
                + (f"\n💬 {body.note}" if body.note else "")
            )
    except Exception as e:
        logger.warning("booking notify failed: %s", e)

    return _booking_view(b)


@router.get("/booking/request/{token}")
async def booking_request_status(token: str) -> dict:
    b = await get_booking(token=token)
    if not b:
        raise HTTPException(status_code=404, detail="not found")
    return _booking_view(b)


@router.get("/booking/requests")
async def booking_requests(
    status: str = Query("open"),
    tg_id: int = Depends(current_user_id),
) -> dict:
    uid = await _owner_user_id()
    statuses = {"open": ("pending", "confirmed"), "pending": ("pending",),
               "all": None}.get(status, ("pending", "confirmed"))
    items = await list_bookings(uid or "", statuses=statuses, upcoming_only=True)
    return {"requests": [_booking_view(b) for b in items]}


async def _decide(booking_id: int, new_status: str) -> dict:
    b = await set_booking_status(booking_id, new_status)
    if not b:
        raise HTTPException(status_code=404, detail="not found")
    try:
        from core.bot_notify import notify_booking_log
        verb = "подтверждена" if new_status == "confirmed" else "отклонена"
        await notify_booking_log(f"🃏 Заявка #{b.id} ({b.requester_name}) — {verb}")
    except Exception as e:
        logger.warning("decide notify failed: %s", e)
    return _booking_view(b)


@router.post("/booking/requests/{booking_id}/confirm")
async def booking_confirm(booking_id: int, tg_id: int = Depends(current_user_id)) -> dict:
    return await _decide(booking_id, "confirmed")


@router.post("/booking/requests/{booking_id}/decline")
async def booking_decline(booking_id: int, tg_id: int = Depends(current_user_id)) -> dict:
    return await _decide(booking_id, "declined")


# ── owner config: availability windows / meeting types / manual blocks ──────

class AvailBody(BaseModel):
    context: str = "friends"
    weekday: int = Field(ge=0, le=6)
    start_time: str          # "HH:MM"
    end_time: str
    tz: str = "Europe/Moscow"
    slot_minutes: int = 60
    min_notice_hours: int = 12
    max_advance_days: int = 60
    buffer_before_min: int = 0
    buffer_after_min: int = 0
    active: bool = True


class MeetingTypeBody(BaseModel):
    context: str = "friends"
    slug: str
    title: str = ""
    duration_min: int = 60
    location_kind: str = "call"
    location_value: str = ""
    requires_approval: bool = False
    description: str = ""
    color: str = ""
    active: bool = True


class BlockBody(BaseModel):
    start: str
    end: str
    reason: str = ""


async def _owner_uid_or_404(tg_id: int) -> str:
    from core.user_manager import get_user_id
    uid = await get_user_id(tg_id)
    if not uid:
        raise HTTPException(status_code=404, detail="no user")
    return uid


@router.get("/booking/availability")
async def avail_list(tg_id: int = Depends(current_user_id)) -> dict:
    uid = await _owner_uid_or_404(tg_id)
    return {"windows": await bkrepo.list_availability(uid)}


@router.post("/booking/availability")
async def avail_add(body: AvailBody, tg_id: int = Depends(current_user_id)) -> dict:
    uid = await _owner_uid_or_404(tg_id)
    if body.context not in _CONTEXTS:
        raise HTTPException(status_code=400, detail="bad context")
    return await bkrepo.add_availability(uid, body.model_dump())


@router.patch("/booking/availability/{row_id}")
async def avail_edit(row_id: int, body: dict, tg_id: int = Depends(current_user_id)) -> dict:
    uid = await _owner_uid_or_404(tg_id)
    row = await bkrepo.edit_availability(row_id, uid, body)
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    return row


@router.delete("/booking/availability/{row_id}")
async def avail_del(row_id: int, tg_id: int = Depends(current_user_id)) -> dict:
    uid = await _owner_uid_or_404(tg_id)
    return {"deleted": await bkrepo.del_availability(row_id, uid)}


@router.get("/booking/meeting-types")
async def mt_list(context: str = Query(""), p: Principal = Depends(booking_principal)) -> dict:
    uid = await _owner_user_id()
    items = await bkrepo.list_meeting_types(uid or "")
    if context:
        items = [m for m in items if m.get("context") == context]
    if p.role not in ("admin",):
        items = [m for m in items if m.get("active")]
    return {"types": items}


@router.post("/booking/meeting-types")
async def mt_add(body: MeetingTypeBody, tg_id: int = Depends(current_user_id)) -> dict:
    uid = await _owner_uid_or_404(tg_id)
    if body.context not in _CONTEXTS:
        raise HTTPException(status_code=400, detail="bad context")
    return await bkrepo.add_meeting_type(uid, body.model_dump())


@router.patch("/booking/meeting-types/{row_id}")
async def mt_edit(row_id: int, body: dict, tg_id: int = Depends(current_user_id)) -> dict:
    uid = await _owner_uid_or_404(tg_id)
    row = await bkrepo.edit_meeting_type(row_id, uid, body)
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    return row


@router.delete("/booking/meeting-types/{row_id}")
async def mt_del(row_id: int, tg_id: int = Depends(current_user_id)) -> dict:
    uid = await _owner_uid_or_404(tg_id)
    return {"deleted": await bkrepo.del_meeting_type(row_id, uid)}


@router.get("/booking/blocks")
async def block_list(tg_id: int = Depends(current_user_id)) -> dict:
    uid = await _owner_uid_or_404(tg_id)
    return {"blocks": await bkrepo.list_blocks(uid)}


@router.post("/booking/blocks")
async def block_add(body: BlockBody, tg_id: int = Depends(current_user_id)) -> dict:
    uid = await _owner_uid_or_404(tg_id)
    try:
        s, e = _parse_dt(body.start), _parse_dt(body.end)
    except ValueError:
        raise HTTPException(status_code=400, detail="bad datetime")
    if e <= s:
        raise HTTPException(status_code=400, detail="end before start")
    return await bkrepo.add_block(uid, s, e, body.reason.strip())


@router.delete("/booking/blocks/{row_id}")
async def block_del(row_id: int, tg_id: int = Depends(current_user_id)) -> dict:
    uid = await _owner_uid_or_404(tg_id)
    return {"deleted": await bkrepo.del_block(row_id, uid)}


@router.get("/booking/feed-url")
async def booking_feed_url(request: Request, tg_id: int = Depends(current_user_id)) -> dict:
    from core.user_manager import get_user_id
    uid = await get_user_id(tg_id)
    if not uid:
        raise HTTPException(status_code=404, detail="no user")
    base = str(request.base_url).rstrip("/")
    return {"url": f"{base}/feed/{feed_token(uid)}.ics"}
