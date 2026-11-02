"""core/repos/cats_tables.py — SQLAlchemy Core table for cats (subject of Memory, like People).

Mirrors Alembic migration c4a7e2b95d13. `memories.cat_id` (FK → cats.id, ON DELETE SET NULL) lives in
memories_table.py as a plain BigInteger, same as person_id: `cats` is in another MetaData.
"""
from __future__ import annotations

from sqlalchemy import (
    BigInteger, CheckConstraint, Column, MetaData, Table, Text, TIMESTAMP, text,
)
from sqlalchemy.dialects.postgresql import ARRAY

metadata = MetaData()

CAT_STATUSES = ("pending", "confirmed", "rejected")

cats = Table(
    "cats", metadata,
    Column("id",           BigInteger, primary_key=True, autoincrement=True),
    Column("user_id",      Text,       nullable=False, server_default=text("''")),
    Column("name",         Text,       nullable=False),
    Column("aliases",      ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")),
    # нормализованные формы имени+кличек для резолва упоминаний (GIN); ведёт pg_cats_repo
    Column("aliases_norm", ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")),
    Column("photo_url",    Text),
    Column("status",       Text,       nullable=False, server_default=text("'confirmed'")),
    Column("created_at",   TIMESTAMP(timezone=True), server_default=text("now()")),
    Column("updated_at",   TIMESTAMP(timezone=True), server_default=text("now()")),
    CheckConstraint("status IN ('pending','confirmed','rejected')", name="ck_cats_status"),
)
