"""arcana/repos/sessions_tables.py — SQLAlchemy Core table definitions for the sessions slice.

Mirrors migration a1f2e3d4c5b6 + w3x4y5z6a7b8 exactly.
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

# ── Session-specific lookup ────────────────────────────────────────────────────

session_category = Table(
    "session_category",
    metadata,
    Column("id",    SmallInteger, primary_key=True, autoincrement=True),
    Column("code",  Text,         nullable=False, unique=True),
    Column("emoji", Text),
    Column("label", Text,         nullable=False),
    Column("sort",  SmallInteger, server_default=text("0")),
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
    Column("session_summary", Text),  # саммари СОБЫТИЯ — триплеты одной отправки, #162
    Column("theme_summary",   Text),  # кросс-дневная сводка ТЕМЫ (name, client), #165
    Column("bottom_card",     Text),
    Column("session_name",    Text),
    Column("category_id",     SmallInteger, ForeignKey("session_category.id")),
    Column("area",            Text),
    Column("deck",            Text),

    Column("amount",          Numeric(10, 2), server_default=text("0")),
    Column("paid",            Numeric(10, 2), server_default=text("0")),

    Column("type_id",         SmallInteger, ForeignKey("engagement_type.id")),
    Column("payment_src_id",  SmallInteger, ForeignKey("payment_source.id")),
    Column("outcome_id",      SmallInteger, ForeignKey("session_outcome.id")),
    Column("client_id",       BigInteger,   ForeignKey("clients.id")),
    # work_id: FK → works.id на уровне БД (миграция s9t0u1v2w3x4); связь #151.
    Column("work_id",          BigInteger),

    Column("barter_what",      Text),
    Column("photo_url",        Text),
    Column("user_notion_id",  Text),
    Column("archived",        Boolean,      server_default=text("false")),

    Column("created_at", TIMESTAMP(timezone=True), server_default=text("now()")),
    Column("updated_at", TIMESTAMP(timezone=True), server_default=text("now()")),
)
