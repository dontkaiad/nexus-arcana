"""arcana/repos/works_tables.py — SQLAlchemy Core table definitions for the works slice.

Mirrors migration b2f3e4d5c6a7 exactly.
"""
from __future__ import annotations

from sqlalchemy import (
    BigInteger, Column, ForeignKey, MetaData,
    SmallInteger, Table, Text, TIMESTAMP, text,
)

from arcana.repos.rituals_tables import metadata

work_priority = Table(
    "work_priority",
    metadata,
    Column("id",    SmallInteger, primary_key=True, autoincrement=True),
    Column("code",  Text,         nullable=False,  unique=True),
    Column("emoji", Text),
    Column("label", Text,         nullable=False),
    Column("sort",  SmallInteger, server_default=text("0")),
)

work_status = Table(
    "work_status",
    metadata,
    Column("id",    SmallInteger, primary_key=True, autoincrement=True),
    Column("code",  Text,         nullable=False,  unique=True),
    Column("emoji", Text),
    Column("label", Text,         nullable=False),
    Column("sort",  SmallInteger, server_default=text("0")),
)

# repeat / day-of-week lookups — mirror nexus task_repeat / task_day_of_week
# (migration ab12cd34ef56). See ADR-0023 (repeat in scope, streaks not).
work_repeat = Table(
    "work_repeat",
    metadata,
    Column("id",    SmallInteger, primary_key=True, autoincrement=True),
    Column("code",  Text,         nullable=False,  unique=True),
    Column("emoji", Text),
    Column("label", Text,         nullable=False),
    Column("sort",  SmallInteger, server_default=text("0")),
)

work_day_of_week = Table(
    "work_day_of_week",
    metadata,
    Column("id",    SmallInteger, primary_key=True, autoincrement=True),
    Column("code",  Text,         nullable=False,  unique=True),
    Column("emoji", Text),
    Column("label", Text,         nullable=False),
    Column("sort",  SmallInteger, server_default=text("0")),
)

works = Table(
    "works",
    metadata,
    Column("id",          BigInteger,   primary_key=True, autoincrement=True),
    Column("title",       Text,         nullable=False),
    Column("deadline",    TIMESTAMP(timezone=True)),
    Column("category",    Text),

    Column("priority_id", SmallInteger, ForeignKey("work_priority.id")),
    Column("status_id",   SmallInteger, ForeignKey("work_status.id")),
    Column("client_id",   BigInteger,   ForeignKey("clients.id")),

    Column("repeat_id",      SmallInteger, ForeignKey("work_repeat.id")),
    Column("day_of_week_id", SmallInteger, ForeignKey("work_day_of_week.id")),
    Column("repeat_time",    Text),

    Column("reminder",     TIMESTAMP(timezone=True)),
    Column("user_notion_id", Text),

    Column("created_at", TIMESTAMP(timezone=True), server_default=text("now()")),
    Column("updated_at", TIMESTAMP(timezone=True), server_default=text("now()")),
)
