"""core/repos/lists_repo.py — repository seam for 🗒️ Списки.

Seals all Notion API calls for the Lists domain so handlers receive
domain operations without raw notion_client props leaking out.
Callers continue to receive plain dicts from list_manager functions;
class methods add domain operations for writes that handlers were
previously doing directly against notion_client.
"""
from __future__ import annotations

from typing import Optional

from core import list_manager as _lm
from core import notion_client as _notion
from core.repos.finance_repo import _repo as _fin_repo

# ── Re-exports so handlers don't import list_manager or notion_client ─────────

WORK_REL_PROP: str = _lm.WORK_REL_PROP
CATEGORY_TO_FINANCE: dict = _lm.CATEGORY_TO_FINANCE
LIST_CATEGORIES: list = _lm.LIST_CATEGORIES
LIST_TYPES: list = _lm.LIST_TYPES
REMIND_DEFAULTS: dict = _lm.REMIND_DEFAULTS

# Pending state pass-throughs (SQLite, not Notion — still domain-owned here)
pending_get = _lm.pending_get
pending_set = _lm.pending_set
pending_del = _lm.pending_del
pending_pop = _lm.pending_pop


class ListsRepo:
    # ── list_manager delegation ───────────────────────────────────────────────

    async def add(
        self, items: list, list_type: str, bot_name: str, user_page_id: str
    ) -> list:
        return await _lm.add_items(items, list_type, bot_name, user_page_id)

    async def get(
        self,
        list_type: Optional[str],
        bot_name: str,
        user_page_id: str,
        status: str = "Not started",
    ) -> list:
        return await _lm.get_list(list_type, bot_name, user_page_id, status)

    async def check(self, items: list, bot_name: str, user_page_id: str) -> dict:
        return await _lm.check_items(items, bot_name, user_page_id)

    async def check_bulk(
        self, total: int, breakdown: list, bot_name: str, user_page_id: str
    ) -> dict:
        return await _lm.check_items_bulk(total, breakdown, bot_name, user_page_id)

    async def checklist_toggle(
        self, item_name: str, bot_name: str, user_page_id: str
    ) -> dict:
        return await _lm.checklist_toggle(item_name, bot_name, user_page_id)

    async def checklist_toggle_by_id(self, page_id: str, bot_name: str) -> dict:
        return await _lm.checklist_toggle_by_id(page_id, bot_name)

    async def buy_mark_done_by_id(
        self, page_id: str, price: float, bot_name: str, user_page_id: str
    ) -> dict:
        return await _lm.buy_mark_done_by_id(page_id, price, bot_name, user_page_id)

    async def inventory_search(
        self, query: str, bot_name: str, user_page_id: str
    ) -> list:
        return await _lm.inventory_search(query, bot_name, user_page_id)

    async def inventory_update(
        self, item_name: str, quantity: int, bot_name: str, user_page_id: str
    ) -> dict:
        return await _lm.inventory_update(item_name, quantity, bot_name, user_page_id)

    async def archive(self, page_ids: list) -> int:
        return await _lm.archive_items(page_ids)

    async def mark_done(self, page_ids: list) -> int:
        return await _lm.mark_items_done(page_ids)

    async def find_matching(
        self, description: str, category: str, bot_name: str, user_page_id: str
    ) -> list:
        return await _lm.find_matching_items(description, category, bot_name, user_page_id)

    async def find_task(
        self,
        query: str,
        user_page_id: str,
        db_id: str = "",
        title_prop: str = "Задача",
    ) -> list:
        return await _lm.find_task_by_name(
            query, user_page_id, db_id=db_id, title_prop=title_prop
        )

    async def search_memory_categories(self, item_names: list) -> dict:
        return await _lm.search_memory_categories(item_names)

    async def get_summary(
        self,
        user_notion_id: str,
        bot_name: str,
        type_: Optional[str] = None,
        group: Optional[str] = None,
        category: Optional[str] = None,
    ) -> dict:
        return await _lm.get_list_summary(
            user_notion_id, bot_name, type_=type_, group=group, category=category
        )

    # ── PG writes — sealed from handlers ────────────────────────────────────

    async def set_expiry(self, item_id: str, date_iso: str) -> None:
        """Set expires_at on a list item (PG). Tries nexus_lists first, then arcana_inventory."""
        from datetime import date as _date_type
        try:
            exp_date = _date_type.fromisoformat(date_iso[:10])
        except ValueError:
            return
        ok = await _lm._nexus_repo.update(item_id, expires_at=exp_date)
        if not ok:
            await _lm._arcana_repo.update(item_id, expires_at=exp_date)

    async def mark_item_done(self, item_id: str) -> None:
        """Set status=Done on a list item (PG) without writing to Финансы."""
        ok = await _lm._nexus_repo.update_status(item_id, "Done")
        if not ok:
            await _lm._arcana_repo.update_status(item_id, "Done")

    async def add_checklist_task(
        self, title: str, user_notion_id: str
    ) -> Optional[str]:
        """Create a parent ✅ Задача for a checklist group. Returns page_id or None."""
        return await _notion.task_add(
            title=title,
            category="💳 Прочее",
            priority="Важно",
            user_notion_id=user_notion_id,
        )

    async def record_purchase(
        self,
        amount: float,
        list_category: str,
        source: str,
        description: str,
        bot_label: str,
        user_notion_id: str,
    ) -> tuple:
        """Write one 💸 Расход to 💰 Финансы.

        Maps list_category → finance_category via CATEGORY_TO_FINANCE.
        Returns (page_id, finance_category).
        """
        finance_cat = CATEGORY_TO_FINANCE.get(list_category, "💳 Прочее")
        fin_id = await _fin_repo.add(
            date=_lm._today_iso(),
            amount=float(amount),
            category=finance_cat,
            type_="💸 Расход",
            source=source,
            description=description,
            bot_label=bot_label,
            user_notion_id=user_notion_id,
        )
        return fin_id, finance_cat

    async def mark_task_done(self, task_id: str) -> bool:
        """Set Статус=Done on a ✅ Задача page."""
        return await _notion.update_task_status(task_id, "Done")

    async def create_reminder_task(
        self,
        item_name: str,
        category: str,
        deadline_iso: str,
        reminder_date_prop: dict,
        db_tasks: str,
        user_page_id: str,
    ) -> Optional[str]:
        """Create a 'Купить X' reminder task in ✅ Задачи.

        reminder_date_prop: pre-built Notion date property dict
        (output of _date or _date_with_tz from nexus/handlers/tasks.py).
        """
        props: dict = {
            "Задача": _notion._title(f"Купить {item_name}"),
            "Статус": _notion._status("Not started"),
            "Дедлайн": _notion._date(deadline_iso),
            "Напоминание": reminder_date_prop,
            "Приоритет": _notion._select("Важно"),
        }
        if category:
            props["Категория"] = _notion._select(category)
        if user_page_id:
            props["🪪 Пользователи"] = _notion._relation(user_page_id)
        from nexus.repos.tasks_repo import _repo as _tasks_repo
        return await _tasks_repo.create(db_tasks, props)


_repo = ListsRepo()
