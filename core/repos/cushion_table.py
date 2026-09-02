"""core/repos/cushion_table.py — SQLAlchemy table for the financial cushion (#подушка).

Подушка — отдельная сущность, НЕ цель_-факт памяти. Один ряд на пользователя:
накопленный баланс + необязательная цель-ориентир + месячный взнос (для
авто-кредита баланса на payday-переходе).
"""
from __future__ import annotations

from sqlalchemy import (
    BigInteger, Column, MetaData, Numeric, Table, Text, TIMESTAMP, text,
)

metadata = MetaData()

cushion = Table(
    "cushion", metadata,
    Column("id",                   BigInteger, primary_key=True, autoincrement=True),
    Column("user_notion_id",       Text, nullable=False, server_default=text("''")),
    Column("balance",              Numeric, nullable=False, server_default=text("0")),
    Column("target",               Numeric, nullable=True),
    Column("monthly_contribution", Numeric, nullable=False, server_default=text("0")),
    Column("created_at",           TIMESTAMP(timezone=True), server_default=text("now()")),
    Column("updated_at",           TIMESTAMP(timezone=True), server_default=text("now()")),
)
