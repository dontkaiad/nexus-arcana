"""zarya — ⭐ Zarya, the heylark Booking concierge bot (@heylark_booking_bot).

Separate bot process (not Nexus — its WhitelistMiddleware blocks non-owner
messages before handlers). Thin front over `core/booking/` + the shared
`grants` table: shows Kai's free/busy, takes bookings, and gives Kai a small
approval cockpit in DM. Group-chat aware. #23 / ADR-0026.
"""
