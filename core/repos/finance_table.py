"""core/repos/finance_table.py — SQLAlchemy tables for 💰 Финансы split by Бот.

nexus_budget — Nexus personal finance (income, expenses, salary)
arcana_pnl   — Arcana P&L (practice income, costs, barter)

GUARD: source='🔄 Бартер' → ONLY arcana_pnl; nexus_budget sanitises to '💳 Карта'.
"""
from __future__ import annotations

from sqlalchemy import (
    BigInteger, Column, Date, MetaData, Numeric,
    Text, TIMESTAMP, Table, text,
)

metadata = MetaData()

nexus_budget = Table(
    "nexus_budget",
    metadata,
    Column("id",             BigInteger, primary_key=True, autoincrement=True),
    Column("description",    Text,       nullable=False, server_default=text("''")),
    Column("amount",         Numeric(12, 2), nullable=False, server_default=text("0")),
    Column("category",       Text,       nullable=False, server_default=text("''")),
    # type_: "💰 Доход" | "💸 Расход"
    Column("type_",          Text,       nullable=False, server_default=text("''")),
    # source: "💳 Карта" | "💵 Наличные" | etc.  GUARD: never "🔄 Бартер"
    Column("source",         Text,       nullable=False, server_default=text("''")),
    Column("date",           Date,       nullable=True),
    Column("user_notion_id", Text,       nullable=False, server_default=text("''")),
    Column("created_at",     TIMESTAMP(timezone=True), server_default=text("now()")),
)

arcana_pnl = Table(
    "arcana_pnl",
    metadata,
    Column("id",             BigInteger, primary_key=True, autoincrement=True),
    Column("description",    Text,       nullable=False, server_default=text("''")),
    Column("amount",         Numeric(12, 2), nullable=False, server_default=text("0")),
    Column("category",       Text,       nullable=False, server_default=text("''")),
    # type_: "💰 Доход" | "💸 Расход"
    Column("type_",          Text,       nullable=False, server_default=text("''")),
    # source: "💳 Карта" | "💵 Наличные" | "🔄 Бартер" | "📱 СБП" | etc.
    Column("source",         Text,       nullable=False, server_default=text("''")),
    Column("date",           Date,       nullable=True),
    Column("user_notion_id", Text,       nullable=False, server_default=text("''")),
    Column("created_at",     TIMESTAMP(timezone=True), server_default=text("now()")),
)
