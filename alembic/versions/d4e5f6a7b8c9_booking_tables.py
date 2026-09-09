"""booking_tables — Lark Booking domain (#23 / ADR-0026)

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-09-10

Foundation for the calendar/booking epic:
- booking_availability — weekly availability windows, per context
- booking_meeting_type — kinds of meetings a requester can pick, per context
- booking — a booking/request row (friends auto-confirm, arcana needs approval)
- booking_block — one-off manual blackout intervals
- works.scheduled_at — forward-looking datetime for an esoteric appointment
  (Arcana works are otherwise retrospective, occurred_at only)

context ∈ ('friends', 'arcana'). Everything is owner-scoped by user_id.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CONTEXT_CHECK = "context IN ('friends', 'arcana')"
_STATUS_CHECK = (
    "status IN ('pending', 'confirmed', 'declined', "
    "'cancelled_by_owner', 'cancelled_by_requester', 'expired')"
)
_SOURCE_CHECK = "source IN ('web', 'tg_group', 'tg_dm')"


def upgrade() -> None:
    op.create_table(
        "booking_availability",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Text, nullable=False, server_default=""),
        sa.Column("context", sa.Text, nullable=False),
        sa.Column("weekday", sa.SmallInteger, nullable=False),  # 0=Mon .. 6=Sun
        sa.Column("start_time", sa.Time, nullable=False),
        sa.Column("end_time", sa.Time, nullable=False),
        sa.Column("tz", sa.Text, nullable=False, server_default="Europe/Moscow"),
        sa.Column("slot_minutes", sa.Integer, nullable=False, server_default="60"),
        sa.Column("min_notice_hours", sa.Integer, nullable=False, server_default="12"),
        sa.Column("max_advance_days", sa.Integer, nullable=False, server_default="60"),
        sa.Column("buffer_before_min", sa.Integer, nullable=False, server_default="0"),
        sa.Column("buffer_after_min", sa.Integer, nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint(_CONTEXT_CHECK, name="ck_booking_availability_context"),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_booking_availability_weekday"),
    )
    op.create_index("ix_booking_availability_user_ctx", "booking_availability", ["user_id", "context"])

    op.create_table(
        "booking_meeting_type",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Text, nullable=False, server_default=""),
        sa.Column("context", sa.Text, nullable=False),
        sa.Column("slug", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False, server_default=""),
        sa.Column("duration_min", sa.Integer, nullable=False, server_default="60"),
        sa.Column("location_kind", sa.Text, nullable=False, server_default="call"),  # call|in_person|custom
        sa.Column("location_value", sa.Text, nullable=False, server_default=""),
        sa.Column("requires_approval", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("color", sa.Text, nullable=False, server_default=""),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint(_CONTEXT_CHECK, name="ck_booking_meeting_type_context"),
        sa.UniqueConstraint("user_id", "context", "slug", name="uq_booking_meeting_type_slug"),
    )

    op.create_table(
        "booking",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Text, nullable=False, server_default=""),
        sa.Column("context", sa.Text, nullable=False),
        sa.Column("meeting_type_id", sa.BigInteger,
                  sa.ForeignKey("booking_meeting_type.id", ondelete="SET NULL"), nullable=True),
        sa.Column("requester_tg_id", sa.BigInteger, nullable=True),
        sa.Column("requester_name", sa.Text, nullable=False, server_default=""),
        sa.Column("requester_contact", sa.Text, nullable=False, server_default=""),
        sa.Column("start_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("end_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("hours", sa.Numeric, nullable=True),  # requester-picked (friends)
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
        sa.Column("source", sa.Text, nullable=False, server_default="web"),
        sa.Column("hold_expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("nexus_task_id", sa.Text, nullable=True),
        sa.Column("arcana_work_id", sa.Text, nullable=True),
        sa.Column("token", sa.Text, nullable=False),  # magic link for the requester
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("decided_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(_CONTEXT_CHECK, name="ck_booking_context"),
        sa.CheckConstraint(_STATUS_CHECK, name="ck_booking_status"),
        sa.CheckConstraint(_SOURCE_CHECK, name="ck_booking_source"),
        sa.UniqueConstraint("token", name="uq_booking_token"),
    )
    op.create_index("ix_booking_user_status", "booking", ["user_id", "status"])
    op.create_index("ix_booking_start_at", "booking", ["start_at"])

    op.create_table(
        "booking_block",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Text, nullable=False, server_default=""),
        sa.Column("start_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("end_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("reason", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_booking_block_user_start", "booking_block", ["user_id", "start_at"])

    op.add_column("works", sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("works", "scheduled_at")
    op.drop_index("ix_booking_block_user_start", table_name="booking_block")
    op.drop_table("booking_block")
    op.drop_index("ix_booking_start_at", table_name="booking")
    op.drop_index("ix_booking_user_status", table_name="booking")
    op.drop_table("booking")
    op.drop_table("booking_meeting_type")
    op.drop_index("ix_booking_availability_user_ctx", table_name="booking_availability")
    op.drop_table("booking_availability")
