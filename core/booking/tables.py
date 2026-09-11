"""core/booking/tables.py — SQLAlchemy Core mirror of the booking_* tables.

Column-for-column with `alembic/versions/d4e5f6a7b8c9_booking_tables.py`.
Update both in the same PR.
"""
from __future__ import annotations

from sqlalchemy import (
    BigInteger, Boolean, Column, Date, ForeignKey, Integer, MetaData, Numeric,
    SmallInteger, Table, Text, Time, TIMESTAMP, text,
)

metadata = MetaData()

# context ∈ ('friends', 'arcana')  ·  status / source — see migration CHECKs

booking_availability = Table(
    "booking_availability", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("user_id", Text, nullable=False, server_default=""),
    Column("context", Text, nullable=False),
    # #232: ровно один из weekday / specific_date — recurring vs one-off
    # (CHECK booking_availability_day_xor). weekday: 0=Mon .. 6=Sun.
    Column("weekday", SmallInteger, nullable=True),
    Column("specific_date", Date, nullable=True),
    Column("start_time", Time, nullable=False),
    Column("end_time", Time, nullable=False),
    Column("tz", Text, nullable=False, server_default="Europe/Moscow"),
    Column("slot_minutes", Integer, nullable=False, server_default="60"),
    Column("min_notice_hours", Integer, nullable=False, server_default="12"),
    Column("max_advance_days", Integer, nullable=False, server_default="60"),
    Column("buffer_before_min", Integer, nullable=False, server_default="0"),
    Column("buffer_after_min", Integer, nullable=False, server_default="0"),
    Column("active", Boolean, nullable=False, server_default=text("true")),
    Column("created_at", TIMESTAMP(timezone=True), server_default=text("now()")),
    Column("updated_at", TIMESTAMP(timezone=True), server_default=text("now()")),
)

booking_meeting_type = Table(
    "booking_meeting_type", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("user_id", Text, nullable=False, server_default=""),
    Column("context", Text, nullable=False),
    Column("slug", Text, nullable=False),
    Column("title", Text, nullable=False, server_default=""),
    Column("duration_min", Integer, nullable=False, server_default="60"),
    Column("location_kind", Text, nullable=False, server_default="call"),
    Column("location_value", Text, nullable=False, server_default=""),
    Column("requires_approval", Boolean, nullable=False, server_default=text("false")),
    Column("description", Text, nullable=False, server_default=""),
    Column("color", Text, nullable=False, server_default=""),
    Column("active", Boolean, nullable=False, server_default=text("true")),
    Column("created_at", TIMESTAMP(timezone=True), server_default=text("now()")),
    Column("updated_at", TIMESTAMP(timezone=True), server_default=text("now()")),
)

booking = Table(
    "booking", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("user_id", Text, nullable=False, server_default=""),
    Column("context", Text, nullable=False),
    Column("meeting_type_id", BigInteger,
           ForeignKey("booking_meeting_type.id", ondelete="SET NULL")),
    Column("requester_tg_id", BigInteger),
    Column("requester_name", Text, nullable=False, server_default=""),
    Column("requester_contact", Text, nullable=False, server_default=""),
    Column("start_at", TIMESTAMP(timezone=True), nullable=False),
    Column("end_at", TIMESTAMP(timezone=True), nullable=False),
    Column("hours", Numeric),
    Column("status", Text, nullable=False, server_default="pending"),
    Column("note", Text, nullable=False, server_default=""),
    Column("source", Text, nullable=False, server_default="web"),
    Column("hold_expires_at", TIMESTAMP(timezone=True)),
    Column("nexus_task_id", Text),
    Column("arcana_work_id", Text),
    Column("token", Text, nullable=False),
    Column("created_at", TIMESTAMP(timezone=True), server_default=text("now()")),
    Column("decided_at", TIMESTAMP(timezone=True)),
)

booking_block = Table(
    "booking_block", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("user_id", Text, nullable=False, server_default=""),
    Column("start_at", TIMESTAMP(timezone=True), nullable=False),
    Column("end_at", TIMESTAMP(timezone=True), nullable=False),
    Column("reason", Text, nullable=False, server_default=""),
    Column("created_at", TIMESTAMP(timezone=True), server_default=text("now()")),
)

# statuses that hold a slot (block the calendar)
BLOCKING_BOOKING_STATUSES = ("pending", "confirmed")
