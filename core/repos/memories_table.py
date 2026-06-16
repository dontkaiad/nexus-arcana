"""core/repos/memories_table.py — SQLAlchemy table for 🧠 Память (per ADR-0005)."""
from __future__ import annotations

from sqlalchemy import (
    BigInteger, Boolean, Column, Index, MetaData, Table, Text,
    TIMESTAMP, text,
)

metadata = MetaData()

memories = Table(
    "memories", metadata,
    Column("id",             BigInteger, primary_key=True, autoincrement=True),
    Column("notion_id",      Text,       unique=True),
    Column("fact_text",      Text,       nullable=False),
    Column("key_name",       Text,       nullable=False, server_default=text("''")),
    Column("value_text",     Text,       nullable=False, server_default=text("''")),
    Column("category",       Text,       nullable=False, server_default=text("''")),
    # scope: "global" | "nexus" | "arcana"  (replaces Бот select)
    Column("scope",          Text,       nullable=False, server_default=text("'global'")),
    # source: "manual" | "auto"  (replaces Источник select)
    Column("source",         Text,       nullable=False, server_default=text("'manual'")),
    Column("related_to",     Text,       nullable=False, server_default=text("''")),
    Column("is_current",     Boolean,    nullable=False, server_default=text("true")),
    Column("is_archived",    Boolean,    nullable=False, server_default=text("false")),
    Column("user_notion_id", Text,       nullable=False, server_default=text("''")),
    Column("created_at",     TIMESTAMP(timezone=True), server_default=text("now()")),
    Column("updated_at",     TIMESTAMP(timezone=True), server_default=text("now()")),
)
