"""core/booking/ics.py — minimal RFC 5545 iCalendar writer for the booking feed (#23 B3 / ADR-0026).

One outbound `.ics` subscription: Kai adds the URL to Apple Calendar (read-only),
sees her tasks-with-deadline, scheduled Arcana works, confirmed bookings and
manual blocks. No dependency — iCal is line-based text.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, List

PRODID = "-//heylark//Booking//RU"


@dataclass(frozen=True)
class IcsEvent:
    uid: str
    start: datetime      # tz-aware
    end: datetime        # tz-aware
    summary: str
    description: str = ""


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
        lines += [
            "BEGIN:VEVENT",
            f"UID:{_esc(ev.uid)}",
            f"DTSTAMP:{now}",
            f"DTSTART:{_dt(ev.start)}",
            f"DTEND:{_dt(ev.end)}",
            f"SUMMARY:{_esc(ev.summary)}",
        ]
        if ev.description:
            lines.append(f"DESCRIPTION:{_esc(ev.description)}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(ln) for ln in lines) + "\r\n"
