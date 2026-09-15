"""core/booking/ics.py — minimal RFC 5545 iCalendar writer for the booking feed (#23 B3 / ADR-0026).

One outbound `.ics` subscription: Kai adds the URL to Apple Calendar (read-only),
sees her tasks-with-deadline, scheduled Arcana works, confirmed bookings and
manual blocks. No dependency — iCal is line-based text.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, List

PRODID = "-//heylark//Booking//RU"
_MSK = timezone(timedelta(hours=3))  # display zone for all-day VALUE=DATE, same convention as busy.py


@dataclass(frozen=True)
class IcsEvent:
    uid: str
    start: datetime      # tz-aware
    end: datetime        # tz-aware
    summary: str
    description: str = ""
    all_day: bool = False  # VALUE=DATE, no time-of-day


def _esc(text: str) -> str:
    return (
        (text or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _dt(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _fold(line: str) -> str:
    """RFC 5545 line folding at 75 octets, continuation lines start with a space."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    out = []
    cur = b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > 75:
            out.append(cur.decode("utf-8"))
            cur = b" " + b
        else:
            cur += b
    out.append(cur.decode("utf-8"))
    return "\r\n".join(out)


def build_ics(events: Iterable[IcsEvent], *, cal_name: str = "heylark Booking") -> str:
    now = _dt(datetime.now(timezone.utc))
    lines: List[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_esc(cal_name)}",
    ]
    for ev in events:
        if ev.all_day:
            # exclusive end per RFC 5545 (DTEND;VALUE=DATE is the day AFTER
            # the last all-day day) — `ev.end` is already the next midnight.
            dtstart = f"DTSTART;VALUE=DATE:{ev.start.astimezone(_MSK).strftime('%Y%m%d')}"
            dtend = f"DTEND;VALUE=DATE:{ev.end.astimezone(_MSK).strftime('%Y%m%d')}"
        else:
            dtstart = f"DTSTART:{_dt(ev.start)}"
            dtend = f"DTEND:{_dt(ev.end)}"
        lines += [
            "BEGIN:VEVENT",
            f"UID:{_esc(ev.uid)}",
            f"DTSTAMP:{now}",
            dtstart,
            dtend,
            f"SUMMARY:{_esc(ev.summary)}",
        ]
        if ev.description:
            lines.append(f"DESCRIPTION:{_esc(ev.description)}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(ln) for ln in lines) + "\r\n"
