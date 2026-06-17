"""core/repos/debts_table.py — SQLAlchemy table for personal debts (#8)."""
from __future__ import annotations

from sqlalchemy import (
    BigInteger, Boolean, Column, MetaData, Numeric, Table, Text,
    TIMESTAMP, text,
)

metadata = MetaData()

debts = Table(
    "debts", metadata,
    Column("id",              BigInteger, primary_key=True, autoincrement=True),
    Column("user_notion_id",  Text,    nullable=False, server_default=text("''")),
    Column("name",            Text,    nullable=False),
    Column("kind",            Text,    nullable=False, server_default=text("'i_owe'")),
    Column("amount",          Numeric, nullable=False),
    Column("deadline",        Text,    nullable=True),
    Column("strategy",        Text,    nullable=True),
    Column("monthly_payment", Numeric, nullable=False, server_default=text("0")),
    Column("is_active",       Boolean, nullable=False, server_default=text("true")),
    Column("created_at",      TIMESTAMP(timezone=True), server_default=text("now()")),
    Column("updated_at",      TIMESTAMP(timezone=True), server_default=text("now()")),
)
