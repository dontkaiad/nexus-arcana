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
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from core.booking.busy import BusyInterval, busy_intervals
from core.booking.ics import IcsEvent, build_ics
from core.config import config
from miniapp.backend.auth import current_user_id

logger = logging.getLogger("miniapp.booking")

router = APIRouter()
feed_router = APIRouter(include_in_schema=False)

_FEED_WINDOW_BACK = timedelta(days=7)
_FEED_WINDOW_FWD = timedelta(days=120)


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


@router.get("/booking/feed-url")
async def booking_feed_url(request: Request, tg_id: int = Depends(current_user_id)) -> dict:
    from core.user_manager import get_user_id
    uid = await get_user_id(tg_id)
    if not uid:
        raise HTTPException(status_code=404, detail="no user")
    base = str(request.base_url).rstrip("/")
    return {"url": f"{base}/feed/{feed_token(uid)}.ics"}
