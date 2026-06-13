"""arcana/repos/rituals_tables.py — SQLAlchemy Core table definitions for the rituals slice.

These definitions mirror migration 022e99f6431d exactly.
All lookup tables share the same shape (id/code/emoji/label/sort).
"""
from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Column,
    ForeignKey,
    Integer,
    MetaData,
    Numeric,
    SmallInteger,
    Table,
    Text,
    TIMESTAMP,
    text,
)

metadata = MetaData()

# ── Lookup tables ─────────────────────────────────────────────────────────────

def _lookup(name: str) -> Table:
    return Table(
        name,
        metadata,
        Column("id",    SmallInteger, primary_key=True, autoincrement=True),
        Column("code",  Text,         nullable=False,  unique=True),
        Column("emoji", Text),
        Column("label", Text,         nullable=False),
        Column("sort",  SmallInteger, server_default=text("0")),
    )


payment_source  = _lookup("payment_source")
engagement_type = _lookup("engagement_type")
magical_purpose = _lookup("magical_purpose")
outcome_status  = _lookup("outcome_status")
ritual_place    = _lookup("ritual_place")

# ── Main table ─────────────────────────────────────────────────────────────────

rituals = Table(
    "rituals",
    metadata,
    Column("id",             BigInteger,   primary_key=True, autoincrement=True),
    Column("title",          Text,         nullable=False),
    Column("occurred_at",    TIMESTAMP(timezone=True)),
    Column("client_id",      BigInteger),  # FK deferred — clients not in PG yet

    Column("payment_src_id", SmallInteger, ForeignKey("payment_source.id")),
    Column("type_id",        SmallInteger, ForeignKey("engagement_type.id")),
    Column("purpose_id",     SmallInteger, ForeignKey("magical_purpose.id")),
    Column("outcome_id",     SmallInteger, ForeignKey("outcome_status.id")),
    Column("place_id",       SmallInteger, ForeignKey("ritual_place.id")),

    Column("price",         Numeric(10, 2)),
    Column("paid",          Numeric(10, 2), server_default=text("0")),
    Column("offerings_sum", Numeric(10, 2)),
    Column("duration_min",  Integer),

    Column("photo_url",   Text),
    Column("forces",      Text),
    Column("structure",   Text),
    Column("consumables", Text),
    Column("offerings",   Text),
    Column("barter_what", Text),
    Column("notes",       Text),

    Column("created_at", TIMESTAMP(timezone=True), server_default=text("now()")),
    Column("updated_at", TIMESTAMP(timezone=True), server_default=text("now()")),
)
