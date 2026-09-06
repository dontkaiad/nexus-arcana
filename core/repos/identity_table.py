"""core/repos/identity_table.py — SQLAlchemy table definition for core_identity.

core_identity is the PG source of truth for Telegram user identity,
replacing Notion 🪪 Пользователи DB as the read-path for user resolution.

notion_id TEXT PRIMARY KEY is the per-device identity id; user_id TEXT is
the shared owner key that matches the user_id column in every other PG table
(owner-key pattern — no FK constraint). Two device rows of the same person
carry the same user_id (#202); a fresh identity self-owns (user_id = notion_id).
"""
from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, Column, MetaData, Table, Text, TIMESTAMP, text

metadata = MetaData()

core_identity = Table(
    "core_identity",
    metadata,
    Column("notion_id", Text, primary_key=True),
    Column("user_id", Text, nullable=False, server_default=text("''")),
    Column("tg_id", BigInteger, nullable=False),
    Column("name", Text, nullable=False, server_default=text("''")),
    Column("role", Text, nullable=False, server_default=text("'Тест'")),
    Column("perm_nexus", Boolean, nullable=False, server_default=text("false")),
    Column("perm_arcana", Boolean, nullable=False, server_default=text("false")),
    Column("perm_finance", Boolean, nullable=False, server_default=text("false")),
    Column("created_at", TIMESTAMP(timezone=True), server_default=text("now()")),
)
