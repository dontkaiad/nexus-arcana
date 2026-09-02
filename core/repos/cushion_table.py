"""core/repos/cushion_table.py — SQLAlchemy table for the financial cushion (#подушка).

Подушка — отдельная сущность, НЕ цель_-факт памяти. Один ряд на пользователя:
накопленный баланс + необязательная цель-ориентир + planned_contribution
(взнос из последнего принятого плана — 20% дохода в комфортный месяц, 0 в
тяжёлый; кредитуется в баланс на payday-переходе вместе с реальной экономией).
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
    Column("planned_contribution", Numeric, nullable=False, server_default=text("0")),
    Column("created_at",           TIMESTAMP(timezone=True), server_default=text("now()")),
    Column("updated_at",           TIMESTAMP(timezone=True), server_default=text("now()")),
)

# Лог каждого пополнения баланса подушки — отдельно от cushion (текущий срез).
# source: 'manual' (команда «положила в подушку X») | 'payday_auto' (кредит
# месячным взносом на payday-переходе).
cushion_transactions = Table(
    "cushion_transactions", metadata,
    Column("id",             BigInteger, primary_key=True, autoincrement=True),
    Column("user_notion_id", Text, nullable=False, server_default=text("''")),
    Column("amount",         Numeric, nullable=False),
    Column("source",         Text, nullable=False, server_default=text("'manual'")),
    Column("note",           Text, nullable=False, server_default=text("''")),
    Column("created_at",     TIMESTAMP(timezone=True), server_default=text("now()")),
)
