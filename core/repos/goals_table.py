"""core/repos/goals_table.py — SQLAlchemy table for savings goals (#205)."""
from __future__ import annotations

from sqlalchemy import (
    BigInteger, Column, MetaData, Numeric, Table, Text, TIMESTAMP, text,
)

metadata = MetaData()

goals = Table(
    "goals", metadata,
    Column("id",         BigInteger, primary_key=True, autoincrement=True),
    Column("user_id",    Text,    nullable=False, server_default=text("''")),
    Column("name",       Text,    nullable=False),
    Column("target",     Numeric, nullable=False),
    Column("monthly",    Numeric, nullable=False, server_default=text("0")),
    Column("saved",      Numeric, nullable=False, server_default=text("0")),
    # active — копим; achieved — накопили/купили; dropped — убрали
    Column("status",     Text,    nullable=False, server_default=text("'active'")),
    Column("created_at", TIMESTAMP(timezone=True), server_default=text("now()")),
    Column("updated_at", TIMESTAMP(timezone=True), server_default=text("now()")),
    Column("closed_at",  TIMESTAMP(timezone=True), nullable=True),
)
