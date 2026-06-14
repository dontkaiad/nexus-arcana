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

    Column("user_notion_id", Text),

    Column("created_at", TIMESTAMP(timezone=True), server_default=text("now()")),
    Column("updated_at", TIMESTAMP(timezone=True), server_default=text("now()")),
)
