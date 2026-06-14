"""nexus/repos/notes_tables.py — SQLAlchemy Core table definitions for nexus notes."""
from __future__ import annotations

from sqlalchemy import (
    BigInteger, Boolean, Column, Date, ForeignKey, MetaData,
    SmallInteger, Table, Text, TIMESTAMP, text,
)

metadata = MetaData()

note_tags = Table(
    "note_tags", metadata,
    Column("id", SmallInteger, primary_key=True, autoincrement=True),
    Column("code", Text, nullable=False, unique=True),
)

notes = Table(
    "notes", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("notion_id", Text, unique=True),
    Column("title", Text, nullable=False),
    Column("date", Date),
    Column("user_notion_id", Text, nullable=False, server_default=text("''")),
    Column("is_archived", Boolean, nullable=False, server_default=text("false")),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False,
           server_default=text("now()")),
    Column("updated_at", TIMESTAMP(timezone=True), nullable=False,
           server_default=text("now()")),
)

note_tag_map = Table(
    "note_tag_map", metadata,
    Column("note_id", BigInteger, ForeignKey("notes.id", ondelete="CASCADE"),
           nullable=False, primary_key=True),
    Column("tag_id", SmallInteger, ForeignKey("note_tags.id", ondelete="CASCADE"),
           nullable=False, primary_key=True),
)
