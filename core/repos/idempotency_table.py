"""core/repos/idempotency_table.py — SQLAlchemy table for idempotency_keys."""
from __future__ import annotations

from sqlalchemy import BigInteger, Column, MetaData, Text, TIMESTAMP, Table, text
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()

idempotency_keys = Table(
    "idempotency_keys",
    metadata,
    Column("id",          BigInteger, primary_key=True, autoincrement=True),
    Column("tg_id",       BigInteger, nullable=False),
    Column("key",         Text,       nullable=False),
    Column("result_json", JSONB,      nullable=True),
    Column("created_at",  TIMESTAMP(timezone=True), server_default=text("now()")),
)
