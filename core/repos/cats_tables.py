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


# ── здоровье котов (миграция c9e2b7a4d815): вес, визиты / прививки, курсы лекарств. Денег нет. ──────────────────────────
from sqlalchemy import Boolean, Date, ForeignKey, Integer, SmallInteger, UniqueConstraint  # noqa: E402

VET_KINDS = ("visit", "vaccine", "deworm", "flea", "other")
VET_STATUSES = ("planned", "done", "cancelled")
MED_STATUSES = ("active", "done", "cancelled")

cat_weights = Table(
    "cat_weights", metadata,
    Column("id",             BigInteger, primary_key=True, autoincrement=True),
    Column("cat_id",         BigInteger, ForeignKey("cats.id", ondelete="CASCADE"), nullable=False),
    Column("user_id",        Text, nullable=False, server_default=text("''")),
    Column("weighed_on",     Date, nullable=False),
    Column("grams",          Integer, nullable=False),
    Column("note",           Text),
    Column("sitter_visible", Boolean, nullable=False, server_default=text("false")),
    Column("created_at",     TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")),
    Column("updated_at",     TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")),
    UniqueConstraint("cat_id", "weighed_on", name="uq_cat_weights_day"),
)

cat_vet_visits = Table(
    "cat_vet_visits", metadata,
    Column("id",             BigInteger, primary_key=True, autoincrement=True),
    Column("cat_id",         BigInteger, ForeignKey("cats.id", ondelete="CASCADE"), nullable=False),
    Column("user_id",        Text, nullable=False, server_default=text("''")),
    Column("kind",           Text, nullable=False, server_default=text("'visit'")),
    Column("title",          Text, nullable=False),
    Column("visit_on",       Date, nullable=False),
    Column("clinic",         Text),
    Column("note",           Text),
    Column("next_due",       Date),
    Column("status",         Text, nullable=False, server_default=text("'planned'")),
    Column("task_id",        BigInteger),
    Column("sitter_visible", Boolean, nullable=False, server_default=text("false")),
    Column("created_at",     TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")),
    Column("updated_at",     TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")),
)

cat_medications = Table(
    "cat_medications", metadata,
    Column("id",            BigInteger, primary_key=True, autoincrement=True),
    Column("cat_id",        BigInteger, ForeignKey("cats.id", ondelete="CASCADE"), nullable=False),
    Column("user_id",       Text, nullable=False, server_default=text("''")),
    Column("name",          Text, nullable=False),
    Column("dose",          Text),
    Column("starts_on",     Date, nullable=False),
    Column("days",          Integer),
    Column("ends_on",       Date),
    Column("times_per_day", SmallInteger, nullable=False, server_default=text("1")),
    Column("status",        Text, nullable=False, server_default=text("'active'")),
    Column("note",          Text),
    Column("task_id",       BigInteger),
    Column("sitter_hidden", Boolean, nullable=False, server_default=text("false")),
    Column("created_at",    TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")),
    Column("updated_at",    TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")),
)
