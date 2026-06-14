"""arcana/repos/sessions_tables.py — SQLAlchemy Core table definitions for the sessions slice.

Mirrors migration a1f2e3d4c5b6 exactly.
Shares payment_source and engagement_type lookup tables with rituals.
"""
from __future__ import annotations

from sqlalchemy import (
    BigInteger, Boolean, Column, Date, ForeignKey,
    MetaData, Numeric, SmallInteger, Table, Text,
    TIMESTAMP, text,
)

from arcana.repos.rituals_tables import (
    metadata,
    payment_source,
    engagement_type,
)

session_outcome = Table(
    "session_outcome",
    metadata,
    Column("id",    SmallInteger, primary_key=True, autoincrement=True),
    Column("code",  Text,         nullable=False,  unique=True),
    Column("emoji", Text),
    Column("label", Text,         nullable=False),
    Column("sort",  SmallInteger, server_default=text("0")),
)

sessions = Table(
    "sessions",
    metadata,
    Column("id",              BigInteger,   primary_key=True, autoincrement=True),
    Column("title",           Text,         nullable=False),
    Column("occurred_at",     Date),

    Column("question",        Text),
    Column("cards",           Text),
    Column("interpretation",  Text),
    Column("triplet_summary", Text),
    Column("bottom_card",     Text),
    Column("session_name",    Text),
    Column("spread_type",     Text),
    Column("area",            Text),
    Column("deck",            Text),

    Column("amount",          Numeric(10, 2), server_default=text("0")),
    Column("paid",            Numeric(10, 2), server_default=text("0")),

    Column("type_id",         SmallInteger, ForeignKey("engagement_type.id")),
    Column("payment_src_id",  SmallInteger, ForeignKey("payment_source.id")),
    Column("outcome_id",      SmallInteger, ForeignKey("session_outcome.id")),
    Column("client_id",       BigInteger,   ForeignKey("clients.id")),

    Column("barter_what",      Text),
    Column("user_notion_id",  Text),
    Column("archived",        Boolean,      server_default=text("false")),

    Column("created_at", TIMESTAMP(timezone=True), server_default=text("now()")),
    Column("updated_at", TIMESTAMP(timezone=True), server_default=text("now()")),
)
