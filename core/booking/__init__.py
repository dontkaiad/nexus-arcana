"""core.booking — Lark Booking domain (#23 / ADR-0026).

Calendar/booking: friends see busy/free and book meetings; esoteric clients
book readings/rituals. Roles come from the shared heylark `grants` table
(not `core_identity`), keyed on the sender's tg_id.

This package currently ships the free/busy engine (`busy_intervals`); the
availability→slots layer, backend routes, Nexus booking-skill and the web
entry land in later phases (B3–B8).
"""
from core.booking.busy import BusyInterval, busy_intervals
from core.booking.repo import (
    Booking, create_booking, get_booking, list_bookings, set_booking_status,
)
from core.booking.slots import Slot, free_slots

__all__ = [
    "BusyInterval", "busy_intervals", "Slot", "free_slots",
    "Booking", "create_booking", "get_booking", "list_bookings", "set_booking_status",
]
