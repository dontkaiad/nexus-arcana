"""core/repos/finance_repo.py — repository seam for 💰 Финансы (shared Nexus + Arcana).

Финансы — общий домен (Notion: Общие, scope по полю «Бот»). Все Notion-формы
(prop-словари, raw db_query фильтры, page_create/update_page) запечатаны здесь;
потребители (nexus/handlers/finance.py + arcana payment/barter/rituals) работают
через доменные методы.

ADR-0003: бизнес-аналитика (бюджет nexus / P&L arcana) остаётся в потребителях —
репо отдаёт только сырой доступ к данным.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from core import notion_client as _notion

logger = logging.getLogger("core.finance_repo")


@dataclass
class FinanceEntry:
    """Domain representation of one 💰 Финансы page."""
    id: str
    description: str = ""
    amount: float = 0.0
    category: str = ""
    type_: str = ""
    source: str = ""
    date: str = ""
    bot: str = ""
    raw_props: dict = field(default_factory=dict)


def _parse_entry(page: dict) -> FinanceEntry:
    props = page.get("properties", {})
    title_parts = props.get("Описание", {}).get("title", [])
    description = title_parts[0].get("plain_text", "") if title_parts else ""
    amount = props.get("Сумма", {}).get("number") or 0
    category = (props.get("Категория", {}).get("select") or {}).get("name", "")
    type_ = (props.get("Тип", {}).get("select") or {}).get("name", "")
    source = (props.get("Источник", {}).get("select") or {}).get("name", "")
    date = ((props.get("Дата", {}).get("date") or {}).get("start", "") or "")[:10]
    bot = (props.get("Бот", {}).get("select") or {}).get("name", "")
    return FinanceEntry(
        id=page.get("id", ""), description=description, amount=amount,
        category=category, type_=type_, source=source, date=date, bot=bot,
        raw_props=props,
    )


class FinanceRepo:
    # ── canonical writes (delegate to notion_client) ─────────────────────────
    async def add(
        self,
        *,
        date: str,
        amount: float,
        category: str,
        type_: str,
        source: str = "💳 Карта",
        bot_label: str = "☀️ Nexus",
        description: str = "",
        user_notion_id: str = "",
    ) -> Optional[str]:
        """Add a finance record (через match_select). Returns page_id."""
        return await _notion.finance_add(
            date=date, amount=amount, category=category, type_=type_,
            source=source, bot_label=bot_label, description=description,
            user_notion_id=user_notion_id,
        )

    async def month(
        self,
        month: str,
        user_notion_id: str = "",
        description_filter: str = "",
        type_filter: str = "",
    ) -> List[dict]:
        """Return raw pages for a month (YYYY-MM)."""
        return await _notion.finance_month(
            month, user_notion_id=user_notion_id,
            description_filter=description_filter, type_filter=type_filter,
        )

    async def update_last(self, target_type: str, field: str, new_value: str) -> bool:
        """Update the most-recent record of a type. Delegates to finance_update."""
        return await _notion.finance_update(target_type, field, new_value)

    # ── sealed inline-dict writes (from handlers) ────────────────────────────
    async def create_entry(
        self,
        db_id: str,
        *,
        description: str,
        date: str,
        amount: float,
        category: str,
        type_: str,
        source: str,
        bot_label: str,
        user_notion_id: str = "",
    ) -> Optional[str]:
        """Build a finance props dict and create the page. Returns page_id.

        NB #96: select-поля пишутся через сырой `_select` БЕЗ `match_select` —
        сохранено 1:1 (вызывающий обязан передать каноничные значения сам).
        """
        props = {
            "Описание":  _notion._title(description or ""),
            "Дата":      _notion._date(date),
            "Сумма":     _notion._number(float(amount)),
            "Категория": _notion._select(category),
            "Тип":       _notion._select(type_),
            "Источник":  _notion._select(source),
            "Бот":       _notion._select(bot_label),
        }
        if user_notion_id:
            props["🪪 Пользователи"] = _notion._relation(user_notion_id)
        return await _notion.page_create(db_id, props)

    async def update_field(self, page_id: str, field: str, value: str) -> bool:
        """Update one field of a finance page. Returns True on success.

        NB: значения для ВСЕХ полей вычисляются eagerly (как в исходнике) —
        `_number(float(value))` сработает даже для нечисловых полей; сохранено 1:1.
        """
        field_map = {
            "source":      ("Источник",  _notion._select(value)),
            "category":    ("Категория", _notion._select(value)),
            "description": ("Описание",  _notion._title(value)),
            "amount":      ("Сумма",     _notion._number(float(value))),
            "type_":       ("Тип",       _notion._select(value)),
        }
        if field not in field_map:
            return False
        notion_key, notion_val = field_map[field]
        try:
            await _notion.update_page(page_id, {notion_key: notion_val})
            return True
        except Exception as e:
            logger.error("update_field error: %s", e)
            return False

    # ── sealed raw-filter reads (from handlers) ──────────────────────────────
    async def query_records(
        self,
        *,
        date_from: str,
        date_to: str,
        type_: Optional[str] = None,
        category: Optional[str] = None,
        page_size: int = 200,
        db_id: Optional[str] = None,
    ) -> List[dict]:
        """Query finance records by date range + optional type/category.

        Returns raw Notion pages (callers parse Сумма/Категория/Тип themselves).
        Filter order: тип → категория → дата(after) → дата(before), как в исходниках.
        """
        from core.config import config
        if db_id is None:
            db_id = config.nexus.db_finance
        conditions: list = []
        if type_:
            conditions.append({"property": "Тип", "select": {"equals": type_}})
        if category:
            conditions.append({"property": "Категория", "select": {"equals": category}})
        conditions.append({"property": "Дата", "date": {"on_or_after": date_from}})
        conditions.append({"property": "Дата", "date": {"on_or_before": date_to}})
        return await _notion.db_query(db_id, filter_obj={"and": conditions}, page_size=page_size)


_repo = FinanceRepo()
