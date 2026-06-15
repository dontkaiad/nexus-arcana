"""core/repos/lists_table.py — SQLAlchemy tables for 🗒️ Списки split by бот.

nexus_lists  — Nexus: Покупки, Чеклист, Инвентарь (80 backfill rows)
arcana_inventory — Arcana: расходники практики + бартер-чеклисты (0 initially)
"""
from __future__ import annotations

from sqlalchemy import (
    BigInteger, Boolean, Column, Date, MetaData, Numeric,
    Text, TIMESTAMP, Table, text,
)

metadata = MetaData()

nexus_lists = Table(
    "nexus_lists",
    metadata,
    Column("id",             BigInteger, primary_key=True, autoincrement=True),
    Column("notion_id",      Text,       unique=True, nullable=True),
    Column("name",           Text,       nullable=False, server_default=text("''")),
    # list_type: "покупки" | "чеклист" | "инвентарь"
    Column("list_type",      Text,       nullable=False, server_default=text("'покупки'")),
    # status: "not_started" | "in_progress" | "done" | "archived"
    Column("status",         Text,       nullable=False, server_default=text("'not_started'")),
    Column("category",       Text,       nullable=False, server_default=text("''")),
    Column("quantity",       Numeric,    nullable=True),
    Column("note",           Text,       nullable=False, server_default=text("''")),
    Column("price_actual",   Numeric,    nullable=True),   # Notion «Цена»
    Column("price_plan",     Numeric,    nullable=True),   # Notion «Цена план»
    Column("store",          Text,       nullable=False, server_default=text("''")),   # Notion «Магазин»
    # priority: "" | "можно_потом" | "важно" | "срочно"
    Column("priority",       Text,       nullable=False, server_default=text("''")),
    Column("group_name",     Text,       nullable=False, server_default=text("''")),
    Column("is_recurring",   Boolean,    nullable=False, server_default=text("false")),
    Column("remind_days",    BigInteger, nullable=True),
    Column("expires_at",     Date,       nullable=True),
    Column("stage",          BigInteger, nullable=True),
    Column("task_id",        Text,       nullable=False, server_default=text("''")),   # ✅ Задачи page_id
    Column("works_id",       Text,       nullable=False, server_default=text("''")),   # 🔮 Работы page_id
    Column("user_notion_id", Text,       nullable=False, server_default=text("''")),
    Column("created_at",     TIMESTAMP(timezone=True), server_default=text("now()")),
    Column("updated_at",     TIMESTAMP(timezone=True), server_default=text("now()")),
)

arcana_inventory = Table(
    "arcana_inventory",
    metadata,
    Column("id",             BigInteger, primary_key=True, autoincrement=True),
    Column("notion_id",      Text,       unique=True, nullable=True),
    Column("name",           Text,       nullable=False, server_default=text("''")),
    # list_type: "инвентарь" | "чеклист"
    Column("list_type",      Text,       nullable=False, server_default=text("'инвентарь'")),
    # status: "not_started" | "in_progress" | "done" | "archived"
    Column("status",         Text,       nullable=False, server_default=text("'not_started'")),
    # category: "🕯️ Расходники" | "🌿 Травы/Масла" | "🃏 Карты/Колоды" | "🔄 Бартер" | ...
    # GUARD: "🔄 Бартер" — ONLY here, never in nexus_lists
    Column("category",       Text,       nullable=False, server_default=text("''")),
    Column("quantity",       Numeric,    nullable=True),
    Column("note",           Text,       nullable=False, server_default=text("''")),
    Column("group_name",     Text,       nullable=False, server_default=text("''")),  # barter session/ritual title
    Column("is_recurring",   Boolean,    nullable=False, server_default=text("false")),
    Column("remind_days",    BigInteger, nullable=True),
    Column("expires_at",     Date,       nullable=True),
    Column("works_id",       Text,       nullable=False, server_default=text("''")),   # 🔮 Работы page_id
    Column("user_notion_id", Text,       nullable=False, server_default=text("''")),
    Column("created_at",     TIMESTAMP(timezone=True), server_default=text("now()")),
    Column("updated_at",     TIMESTAMP(timezone=True), server_default=text("now()")),
)
