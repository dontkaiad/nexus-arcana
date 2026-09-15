"""booking.status: add 'completed' — meeting happened, task→booking bridge

Marking the linked Nexus task "Done" had no effect on the booking calendar —
only cancel/decline changed `booking.status`, and those trigger a "встреча
отменена" DM to the requester, which is wrong for a meeting that already
happened. This adds a genuine terminal "it happened" status, set by
core.booking.linkage.complete_linked_booking() when the linked task/work is
marked done. Deliberately NOT added to BLOCKING_BOOKING_STATUSES (pending,
confirmed) — it stops blocking the calendar by omission, no busy.py change
needed.

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-09-16
"""
from __future__ import annotations

from alembic import op

revision = "d3e4f5a6b7c8"
down_revision = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None

_OLD_CHECK = (
    "status IN ('pending', 'confirmed', 'declined', "
    "'cancelled_by_owner', 'cancelled_by_requester', 'expired')"
)
_NEW_CHECK = (
    "status IN ('pending', 'confirmed', 'declined', "
    "'cancelled_by_owner', 'cancelled_by_requester', 'expired', 'completed')"
)


def upgrade() -> None:
    op.drop_constraint("ck_booking_status", "booking", type_="check")
    op.create_check_constraint("ck_booking_status", "booking", _NEW_CHECK)


def downgrade() -> None:
    op.execute("UPDATE booking SET status = 'confirmed' WHERE status = 'completed'")
    op.drop_constraint("ck_booking_status", "booking", type_="check")
    op.create_check_constraint("ck_booking_status", "booking", _OLD_CHECK)
