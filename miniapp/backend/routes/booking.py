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

from core.auth_grants import booking_role
from core.booking.busy import BusyInterval, busy_intervals, merge_intervals
from core.booking.ics import IcsEvent, build_ics
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


@router.get("/booking/feed-url")
async def booking_feed_url(request: Request, tg_id: int = Depends(current_user_id)) -> dict:
    from core.user_manager import get_user_id
    uid = await get_user_id(tg_id)
    if not uid:
        raise HTTPException(status_code=404, detail="no user")
    base = str(request.base_url).rstrip("/")
    return {"url": f"{base}/feed/{feed_token(uid)}.ics"}
