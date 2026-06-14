"""arcana/repos/grimoire_tables.py — SQLAlchemy Core table definitions for 📖 Гримуар.

Mirrors migration c3f4e5d6a7b8 exactly.
"""
from __future__ import annotations

from sqlalchemy import (
    BigInteger, Boolean, Column, ForeignKey,
    SmallInteger, Table, Text, TIMESTAMP, text,
)

from arcana.repos.rituals_tables import metadata

grimoire_category = Table(
    "grimoire_category",
    metadata,
    Column("id",    SmallInteger, primary_key=True, autoincrement=True),
    Column("code",  Text,         nullable=False,  unique=True),
    Column("emoji", Text),
    Column("label", Text,         nullable=False),
    Column("sort",  SmallInteger, server_default=text("0")),
)

grimoire_entries = Table(
    "grimoire_entries",
    metadata,
    Column("id",          BigInteger,   primary_key=True, autoincrement=True),
    Column("title",       Text,         nullable=False),
    Column("category_id", SmallInteger, ForeignKey("grimoire_category.id")),
    Column("themes",      Text),
    Column("verified",    Boolean,      server_default=text("false")),
    Column("text",        Text),
    Column("source",      Text),
    Column("user_notion_id", Text),
    Column("created_at", TIMESTAMP(timezone=True), server_default=text("now()")),
)
