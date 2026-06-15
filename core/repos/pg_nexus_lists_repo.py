"""core/repos/pg_nexus_lists_repo.py — PG implementation for 🗒️ Списки (split by Бот).

nexus_lists  → PgNexusListsRepo  + ListItem domain object
arcana_inventory → PgArcanaInventoryRepo + InventoryItem domain object

All async methods use asyncio.to_thread over sync SQLAlchemy (no asyncpg).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, List, Optional

from sqlalchemy import and_, select, text

from core.repos.lists_table import arcana_inventory, nexus_lists

logger = logging.getLogger("core.pg_nexus_lists_repo")

# ── Notion ↔ PG value mappings ────────────────────────────────────────────────

NOTION_TYPE_TO_PG: Dict[str, str] = {
    "🛒 Покупки": "покупки",
    "📋 Чеклист": "чеклист",
    "📦 Инвентарь": "инвентарь",
}
PG_TYPE_TO_NOTION: Dict[str, str] = {v: k for k, v in NOTION_TYPE_TO_PG.items()}

NOTION_STATUS_TO_PG: Dict[str, str] = {
    "Not started": "not_started",
    "In progress": "in_progress",
    "Done": "done",
    "Archived": "archived",
}
PG_STATUS_TO_NOTION: Dict[str, str] = {v: k for k, v in NOTION_STATUS_TO_PG.items()}

NOTION_PRIORITY_TO_PG: Dict[str, str] = {
    "⚪ Можно потом": "можно_потом",
    "🟡 Важно": "важно",
    "🔴 Срочно": "срочно",
}
PG_PRIORITY_TO_NOTION: Dict[str, str] = {v: k for k, v in NOTION_PRIORITY_TO_PG.items()}

# GUARD: бартер категория — ТОЛЬКО arcana_inventory
BARTER_CATEGORY = "🔄 Бартер"


def _notion_type(pg_type: str) -> str:
    return PG_TYPE_TO_NOTION.get(pg_type, "🛒 Покупки")


def _pg_type(notion_type: str) -> str:
    return NOTION_TYPE_TO_PG.get(notion_type, "покупки")


def _pg_status(notion_status: str) -> str:
    return NOTION_STATUS_TO_PG.get(notion_status, "not_started")


def _notion_status(pg_status: str) -> str:
    return PG_STATUS_TO_NOTION.get(pg_status, "Not started")


def _pg_priority(notion_priority: str) -> str:
    return NOTION_PRIORITY_TO_PG.get(notion_priority, "")


def _notion_priority(pg_priority: str) -> str:
    return PG_PRIORITY_TO_NOTION.get(pg_priority, "")


def _parse_date(val) -> Optional[str]:
    """Row date/datetime field → 'YYYY-MM-DD' string or ''."""
    if val is None:
        return ""
    if isinstance(val, (date, datetime)):
        return val.isoformat()[:10]
    return str(val)[:10]


# ── Engine ────────────────────────────────────────────────────────────────────

def _get_engine():
    from arcana.repos.pg_sessions_repo import get_engine
    return get_engine()


# ── Domain objects ─────────────────────────────────────────────────────────────

@dataclass
class ListItem:
    """One row from nexus_lists."""
    id: str
    name: str
    list_type: str       # "покупки" | "чеклист" | "инвентарь"
    status: str          # "not_started" | "in_progress" | "done" | "archived"
    category: str = ""
    quantity: Optional[float] = None
    note: str = ""
    price_actual: Optional[float] = None
    price_plan: Optional[float] = None
    store: str = ""
    priority: str = ""   # "" | "можно_потом" | "важно" | "срочно"
    group_name: str = ""
    is_recurring: bool = False
    remind_days: Optional[int] = None
    expires_at: str = ""  # "YYYY-MM-DD" or ""
    stage: Optional[int] = None
    task_id: str = ""
    works_id: str = ""
    user_notion_id: str = ""
    date: str = ""        # created_at[:10]


@dataclass
class InventoryItem:
    """One row from arcana_inventory."""
    id: str
    name: str
    list_type: str       # "инвентарь" | "чеклист"
    status: str
    category: str = ""   # may be "🔄 Бартер"
    quantity: Optional[float] = None
    note: str = ""
    group_name: str = ""  # barter: session/ritual title
    is_recurring: bool = False
    remind_days: Optional[int] = None
    expires_at: str = ""
    works_id: str = ""
    user_notion_id: str = ""
    date: str = ""


# ── Row converters ─────────────────────────────────────────────────────────────

def _row_to_list_item(row) -> ListItem:
    created = getattr(row, "created_at", None)
    date_str = _parse_date(created)
    return ListItem(
        id=str(row.id),
        name=row.name or "",
        list_type=row.list_type or "покупки",
        status=row.status or "not_started",
        category=row.category or "",
        quantity=float(row.quantity) if row.quantity is not None else None,
        note=row.note or "",
        price_actual=float(row.price_actual) if row.price_actual is not None else None,
        price_plan=float(row.price_plan) if row.price_plan is not None else None,
        store=row.store or "",
        priority=row.priority or "",
        group_name=row.group_name or "",
        is_recurring=bool(row.is_recurring),
        remind_days=int(row.remind_days) if row.remind_days is not None else None,
        expires_at=_parse_date(getattr(row, "expires_at", None)),
        stage=int(row.stage) if row.stage is not None else None,
        task_id=row.task_id or "",
        works_id=row.works_id or "",
        user_notion_id=row.user_notion_id or "",
        date=date_str,
    )


def _row_to_inventory_item(row) -> InventoryItem:
    created = getattr(row, "created_at", None)
    return InventoryItem(
        id=str(row.id),
        name=row.name or "",
        list_type=row.list_type or "инвентарь",
        status=row.status or "not_started",
        category=row.category or "",
        quantity=float(row.quantity) if row.quantity is not None else None,
        note=row.note or "",
        group_name=row.group_name or "",
        is_recurring=bool(row.is_recurring),
        remind_days=int(row.remind_days) if row.remind_days is not None else None,
        expires_at=_parse_date(getattr(row, "expires_at", None)),
        works_id=row.works_id or "",
        user_notion_id=row.user_notion_id or "",
        date=_parse_date(created),
    )


# ── nexus_lists: sync helpers ─────────────────────────────────────────────────

def _nl_add_sync(
    name: str,
    list_type: str,
    status: str,
    category: str,
    quantity: Optional[float],
    note: str,
    price_actual: Optional[float],
    price_plan: Optional[float],
    store: str,
    priority: str,
    group_name: str,
    is_recurring: bool,
    remind_days: Optional[int],
    expires_at: Optional[str],
    stage: Optional[int],
    task_id: str,
    works_id: str,
    user_notion_id: str,
    notion_id: Optional[str] = None,
) -> ListItem:
    exp = None
    if expires_at:
        try:
            exp = date.fromisoformat(expires_at[:10])
        except ValueError:
            pass
    with _get_engine().begin() as conn:
        result = conn.execute(
            nexus_lists.insert().values(
                notion_id=notion_id,
                name=name,
                list_type=list_type,
                status=status,
                category=category or "",
                quantity=quantity,
                note=note or "",
                price_actual=price_actual,
                price_plan=price_plan,
                store=store or "",
                priority=priority or "",
                group_name=group_name or "",
                is_recurring=is_recurring,
                remind_days=remind_days,
                expires_at=exp,
                stage=stage,
                task_id=task_id or "",
                works_id=works_id or "",
                user_notion_id=user_notion_id or "",
            ).returning(nexus_lists)
        )
        row = result.fetchone()
    return _row_to_list_item(row)


def _nl_get_list_sync(
    list_type: Optional[str],
    status: str,
    user_notion_id: str,
    page_size: int = 100,
) -> List[ListItem]:
    q = select(nexus_lists).where(nexus_lists.c.status == _pg_status(status))
    if list_type:
        q = q.where(nexus_lists.c.list_type == _pg_type(list_type))
    if user_notion_id:
        q = q.where(nexus_lists.c.user_notion_id == user_notion_id)
    q = q.order_by(nexus_lists.c.created_at.desc()).limit(page_size)
    with _get_engine().connect() as conn:
        rows = conn.execute(q).fetchall()
    return [_row_to_list_item(r) for r in rows]


def _nl_search_sync(
    query: str,
    list_type: Optional[str],
    status: Optional[str],
    user_notion_id: str,
    page_size: int = 20,
) -> List[ListItem]:
    q = select(nexus_lists)
    if query:
        q = q.where(nexus_lists.c.name.ilike(f"%{query}%"))
    if list_type:
        q = q.where(nexus_lists.c.list_type == _pg_type(list_type))
    if status:
        q = q.where(nexus_lists.c.status == _pg_status(status))
    else:
        q = q.where(nexus_lists.c.status != "archived")
    if user_notion_id:
        q = q.where(nexus_lists.c.user_notion_id == user_notion_id)
    q = q.order_by(nexus_lists.c.created_at.desc()).limit(page_size)
    with _get_engine().connect() as conn:
        rows = conn.execute(q).fetchall()
    return [_row_to_list_item(r) for r in rows]


def _nl_get_by_id_sync(item_id: str) -> Optional[ListItem]:
    try:
        iid = int(item_id)
    except (ValueError, TypeError):
        return None
    with _get_engine().connect() as conn:
        row = conn.execute(
            select(nexus_lists).where(nexus_lists.c.id == iid)
        ).fetchone()
    return _row_to_list_item(row) if row else None


def _nl_update_sync(item_id: str, **fields) -> bool:
    try:
        iid = int(item_id)
    except (ValueError, TypeError):
        return False
    if not fields:
        return False
    fields["updated_at"] = text("now()")
    with _get_engine().begin() as conn:
        result = conn.execute(
            nexus_lists.update().where(nexus_lists.c.id == iid).values(**fields)
        )
    return result.rowcount > 0


def _nl_get_recurring_sync() -> List[ListItem]:
    q = (
        select(nexus_lists)
        .where(nexus_lists.c.status == "done")
        .where(nexus_lists.c.is_recurring == True)  # noqa: E712
        .limit(200)
    )
    with _get_engine().connect() as conn:
        rows = conn.execute(q).fetchall()
    return [_row_to_list_item(r) for r in rows]


def _nl_get_expiry_due_sync(today: date) -> List[ListItem]:
    q = (
        select(nexus_lists)
        .where(nexus_lists.c.list_type == "инвентарь")
        .where(nexus_lists.c.status != "archived")
        .where(nexus_lists.c.expires_at.isnot(None))
        .limit(200)
    )
    with _get_engine().connect() as conn:
        rows = conn.execute(q).fetchall()
    return [_row_to_list_item(r) for r in rows]


def _nl_get_summary_sync(
    user_notion_id: str,
    list_type: Optional[str] = None,
    group: Optional[str] = None,
    category: Optional[str] = None,
) -> List[ListItem]:
    q = select(nexus_lists).where(nexus_lists.c.status != "archived")
    if list_type:
        q = q.where(nexus_lists.c.list_type == _pg_type(list_type))
    if category:
        q = q.where(nexus_lists.c.category == category)
    if user_notion_id:
        q = q.where(nexus_lists.c.user_notion_id == user_notion_id)
    q = q.order_by(nexus_lists.c.created_at.desc()).limit(500)
    with _get_engine().connect() as conn:
        rows = conn.execute(q).fetchall()
    items = [_row_to_list_item(r) for r in rows]
    if group:
        g_lower = group.strip().lower()
        items = [it for it in items if it.group_name.strip().lower() == g_lower]
    return items


def _nl_get_group_remaining_sync(group_name: str, list_type: str) -> int:
    q = (
        select(nexus_lists.c.id)
        .where(nexus_lists.c.group_name == group_name)
        .where(nexus_lists.c.list_type == _pg_type(list_type))
        .where(nexus_lists.c.status.notin_(["done", "archived"]))
        .limit(1)
    )
    with _get_engine().connect() as conn:
        row = conn.execute(q).fetchone()
    return 0 if row is None else 1


# ── arcana_inventory: sync helpers ────────────────────────────────────────────

def _ai_add_sync(
    name: str,
    list_type: str,
    status: str,
    category: str,
    quantity: Optional[float],
    note: str,
    group_name: str,
    is_recurring: bool,
    remind_days: Optional[int],
    expires_at: Optional[str],
    works_id: str,
    user_notion_id: str,
    notion_id: Optional[str] = None,
) -> InventoryItem:
    exp = None
    if expires_at:
        try:
            exp = date.fromisoformat(expires_at[:10])
        except ValueError:
            pass
    with _get_engine().begin() as conn:
        result = conn.execute(
            arcana_inventory.insert().values(
                notion_id=notion_id,
                name=name,
                list_type=list_type,
                status=status,
                category=category or "",
                quantity=quantity,
                note=note or "",
                group_name=group_name or "",
                is_recurring=is_recurring,
                remind_days=remind_days,
                expires_at=exp,
                works_id=works_id or "",
                user_notion_id=user_notion_id or "",
            ).returning(arcana_inventory)
        )
        row = result.fetchone()
    return _row_to_inventory_item(row)


def _ai_search_sync(
    query: str,
    status: Optional[str],
    user_notion_id: str,
    page_size: int = 20,
) -> List[InventoryItem]:
    q = select(arcana_inventory)
    if query:
        q = q.where(arcana_inventory.c.name.ilike(f"%{query}%"))
    if status:
        q = q.where(arcana_inventory.c.status == _pg_status(status))
    else:
        q = q.where(arcana_inventory.c.status != "archived")
    if user_notion_id:
        q = q.where(arcana_inventory.c.user_notion_id == user_notion_id)
    q = q.order_by(arcana_inventory.c.created_at.desc()).limit(page_size)
    with _get_engine().connect() as conn:
        rows = conn.execute(q).fetchall()
    return [_row_to_inventory_item(r) for r in rows]


def _ai_get_by_id_sync(item_id: str) -> Optional[InventoryItem]:
    try:
        iid = int(item_id)
    except (ValueError, TypeError):
        return None
    with _get_engine().connect() as conn:
        row = conn.execute(
            select(arcana_inventory).where(arcana_inventory.c.id == iid)
        ).fetchone()
    return _row_to_inventory_item(row) if row else None


def _ai_get_list_sync(
    category: Optional[str],
    status: Optional[str],
    user_notion_id: str,
    page_size: int = 100,
) -> List[InventoryItem]:
    q = select(arcana_inventory)
    if category:
        q = q.where(arcana_inventory.c.category == category)
    if status:
        q = q.where(arcana_inventory.c.status == _pg_status(status))
    else:
        q = q.where(arcana_inventory.c.status != "archived")
    if user_notion_id:
        q = q.where(arcana_inventory.c.user_notion_id == user_notion_id)
    q = q.order_by(arcana_inventory.c.created_at.desc()).limit(page_size)
    with _get_engine().connect() as conn:
        rows = conn.execute(q).fetchall()
    return [_row_to_inventory_item(r) for r in rows]


def _ai_update_sync(item_id: str, **fields) -> bool:
    try:
        iid = int(item_id)
    except (ValueError, TypeError):
        return False
    if not fields:
        return False
    fields["updated_at"] = text("now()")
    with _get_engine().begin() as conn:
        result = conn.execute(
            arcana_inventory.update().where(arcana_inventory.c.id == iid).values(**fields)
        )
    return result.rowcount > 0


def _ai_get_recurring_sync() -> List[InventoryItem]:
    q = (
        select(arcana_inventory)
        .where(arcana_inventory.c.status == "done")
        .where(arcana_inventory.c.is_recurring == True)  # noqa: E712
        .limit(200)
    )
    with _get_engine().connect() as conn:
        rows = conn.execute(q).fetchall()
    return [_row_to_inventory_item(r) for r in rows]


def _ai_get_expiry_due_sync(today: date) -> List[InventoryItem]:
    q = (
        select(arcana_inventory)
        .where(arcana_inventory.c.list_type == "инвентарь")
        .where(arcana_inventory.c.status != "archived")
        .where(arcana_inventory.c.expires_at.isnot(None))
        .limit(200)
    )
    with _get_engine().connect() as conn:
        rows = conn.execute(q).fetchall()
    return [_row_to_inventory_item(r) for r in rows]


# ── Public async repos ─────────────────────────────────────────────────────────

class PgNexusListsRepo:
    """Async repo for nexus_lists table."""

    async def add_item(
        self,
        name: str,
        list_type: str,
        status: str = "Not started",
        category: str = "",
        quantity: Optional[float] = None,
        note: str = "",
        price_actual: Optional[float] = None,
        price_plan: Optional[float] = None,
        store: str = "",
        priority: str = "",
        group_name: str = "",
        is_recurring: bool = False,
        remind_days: Optional[int] = None,
        expires_at: Optional[str] = None,
        stage: Optional[int] = None,
        task_id: str = "",
        works_id: str = "",
        user_notion_id: str = "",
        notion_id: Optional[str] = None,
    ) -> ListItem:
        return await asyncio.to_thread(
            _nl_add_sync,
            name, _pg_type(list_type), _pg_status(status),
            category, quantity, note, price_actual, price_plan,
            store, _pg_priority(priority), group_name,
            is_recurring, remind_days, expires_at, stage,
            task_id, works_id, user_notion_id, notion_id,
        )

    async def get_list(
        self,
        list_type: Optional[str],
        status: str = "Not started",
        user_notion_id: str = "",
        page_size: int = 100,
    ) -> List[ListItem]:
        return await asyncio.to_thread(
            _nl_get_list_sync, list_type, status, user_notion_id, page_size
        )

    async def search(
        self,
        query: str,
        list_type: Optional[str] = None,
        status: Optional[str] = None,
        user_notion_id: str = "",
        page_size: int = 20,
    ) -> List[ListItem]:
        return await asyncio.to_thread(
            _nl_search_sync, query, list_type, status, user_notion_id, page_size
        )

    async def get_by_id(self, item_id: str) -> Optional[ListItem]:
        return await asyncio.to_thread(_nl_get_by_id_sync, item_id)

    async def update(self, item_id: str, **fields) -> bool:
        return await asyncio.to_thread(_nl_update_sync, item_id, **fields)

    async def update_status(self, item_id: str, status: str) -> bool:
        return await self.update(item_id, status=_pg_status(status))

    async def get_recurring(self) -> List[ListItem]:
        return await asyncio.to_thread(_nl_get_recurring_sync)

    async def get_expiry_due(self, today: date) -> List[ListItem]:
        return await asyncio.to_thread(_nl_get_expiry_due_sync, today)

    async def get_summary_items(
        self,
        user_notion_id: str,
        list_type: Optional[str] = None,
        group: Optional[str] = None,
        category: Optional[str] = None,
    ) -> List[ListItem]:
        return await asyncio.to_thread(
            _nl_get_summary_sync, user_notion_id, list_type, group, category
        )

    async def get_group_remaining(self, group_name: str, list_type: str) -> int:
        return await asyncio.to_thread(_nl_get_group_remaining_sync, group_name, list_type)


class PgArcanaInventoryRepo:
    """Async repo for arcana_inventory table.

    GUARD: category BARTER_CATEGORY ('🔄 Бартер') must only appear here.
    barter_prompt.py routes to this store, NOT nexus_lists.
    """

    async def add_item(
        self,
        name: str,
        list_type: str = "📦 Инвентарь",
        status: str = "Not started",
        category: str = "",
        quantity: Optional[float] = None,
        note: str = "",
        group_name: str = "",
        is_recurring: bool = False,
        remind_days: Optional[int] = None,
        expires_at: Optional[str] = None,
        works_id: str = "",
        user_notion_id: str = "",
        notion_id: Optional[str] = None,
    ) -> InventoryItem:
        return await asyncio.to_thread(
            _ai_add_sync,
            name, _pg_type(list_type), _pg_status(status),
            category, quantity, note, group_name,
            is_recurring, remind_days, expires_at,
            works_id, user_notion_id, notion_id,
        )

    async def search(
        self,
        query: str,
        status: Optional[str] = None,
        user_notion_id: str = "",
        page_size: int = 20,
    ) -> List[InventoryItem]:
        return await asyncio.to_thread(
            _ai_search_sync, query, status, user_notion_id, page_size
        )

    async def get_list(
        self,
        category: Optional[str] = None,
        status: Optional[str] = None,
        user_notion_id: str = "",
        page_size: int = 100,
    ) -> List[InventoryItem]:
        return await asyncio.to_thread(
            _ai_get_list_sync, category, status, user_notion_id, page_size
        )

    async def get_by_id(self, item_id: str) -> Optional[InventoryItem]:
        return await asyncio.to_thread(_ai_get_by_id_sync, item_id)

    async def update(self, item_id: str, **fields) -> bool:
        return await asyncio.to_thread(_ai_update_sync, item_id, **fields)

    async def update_status(self, item_id: str, status: str) -> bool:
        return await self.update(item_id, status=_pg_status(status))

    async def get_recurring(self) -> List[InventoryItem]:
        return await asyncio.to_thread(_ai_get_recurring_sync)

    async def get_expiry_due(self, today: date) -> List[InventoryItem]:
        return await asyncio.to_thread(_ai_get_expiry_due_sync, today)
