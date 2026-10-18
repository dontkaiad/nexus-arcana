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
    Column("fact_text",      Text,       nullable=False),
    Column("key_name",       Text,       nullable=False, server_default=text("''")),
    Column("category",       Text,       nullable=False, server_default=text("''")),
    # scope: "global" | "nexus" | "arcana"  (replaces Бот select)
    Column("scope",          Text,       nullable=False, server_default=text("'global'")),
    # source: "manual" | "auto"  (replaces Источник select)
    Column("source",         Text,       nullable=False, server_default=text("'manual'")),
    Column("related_to",     Text,       nullable=False, server_default=text("''")),
    Column("is_current",     Boolean,    nullable=False, server_default=text("true")),
    Column("is_archived",    Boolean,    nullable=False, server_default=text("false")),
    Column("user_id", Text,       nullable=False, server_default=text("''")),
    # People domain (ADR-0027). FK memories.person_id → people.id (ON DELETE SET
    # NULL) lives in the migration; `people` is in another MetaData, so the
    # Core column stays a plain BigInteger.
    Column("person_id",      BigInteger),
    # pending | confirmed | rejected. Every read for context/search shows
    # only 'confirmed'; destructive paths see all.
    # цитата-основание автоизвлечённого факта (показывается при ревью); ADR-0027
    Column("source_quote",   Text),
    Column("review_status",  Text,       nullable=False, server_default=text("'confirmed'")),
    Column("created_at",     TIMESTAMP(timezone=True), server_default=text("now()")),
    Column("updated_at",     TIMESTAMP(timezone=True), server_default=text("now()")),
)
