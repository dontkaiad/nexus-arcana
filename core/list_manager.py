"""core/list_manager.py — бизнес-логика для 🗒️ Списки (PG, split by Бот).

Storage: nexus_lists (☀️ Nexus) + arcana_inventory (🌒 Arcana).
GUARD: 🔄 Бартер category → ONLY arcana_inventory; never nexus_lists.
finance_add → Notion 💰 Финансы (Finance DB stays on Notion).
find_task_by_name → Notion ✅ Задачи (Tasks DB stays on Notion).
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

from core.config import config
from core.notion_client import finance_add, query_pages

from core.repos.pg_nexus_lists_repo import (
    PgNexusListsRepo, PgArcanaInventoryRepo,
    ListItem, InventoryItem,
    _pg_type, _pg_status, _notion_type, _notion_status, _notion_priority,
    BARTER_CATEGORY,
)

logger = logging.getLogger("nexus.list_manager")

# ── Константы ─────────────────────────────────────────────────────────────────

# Kept for backward-compat import from lists_repo.py and handlers.
WORK_REL_PROP = "🔮 Работы "

CATEGORY_TO_FINANCE = {
    "🐾 Коты": "🐾 Коты",
    "🍜 Продукты": "🍜 Продукты",
    "🏠 Ж***": "🏠 Ж***",
    "🏥 Здоровье": "🏥 Здоровье",
    "💅 Бьюти": "💅 Бьюти",
    "👗 Гардероб": "👗 Гардероб",
    "💻 Подписки": "💻 Подписки",
    "📚 Хобби/Учеба": "📚 Хобби/Учеба",
    "🚬 Привычки": "🚬 Привычки",
    "🕯️ Расходники": "🕯️ Расходники",
    "🌿 Травы/Масла": "🕯️ Расходники",
    "🃏 Карты/Колоды": "🕯️ Расходники",
    "💳 Прочее": "💳 Прочее",
}

REMIND_DEFAULTS = {
    "🐾 Коты": 7,
    "🍜 Продукты": 3,
    "🏥 Здоровье": 30,
    "🌿 Травы/Масла": 90,
}

LIST_CATEGORIES = [
    "🐾 Коты", "🍜 Продукты", "🏠 Ж***", "🏥 Здоровье", "💅 Бьюти",
    "👗 Гардероб", "💻 Подписки", "💻 Техника", "📚 Хобби/Учеба", "🚬 Привычки",
    "💳 Прочее", "🕯️ Расходники", "🌿 Травы/Масла", "🃏 Карты/Колоды",
    "🔄 Бартер",
]

LIST_TYPES = ["🛒 Покупки", "📋 Чеклист", "📦 Инвентарь"]


# ── Pending state (SQLite) ────────────────────────────────────────────────────

_PENDING_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../pending_lists.db")
_PENDING_TTL = 1800  # 30 min


def _ldb() -> sqlite3.Connection:
    con = sqlite3.connect(_PENDING_DB)
    con.execute(
        "CREATE TABLE IF NOT EXISTS pending_lists "
        "(uid INTEGER PRIMARY KEY, data TEXT, ts REAL)"
    )
    con.commit()
    return con


def pending_set(uid: int, data: dict) -> None:
    with _ldb() as con:
        con.execute(
            "INSERT OR REPLACE INTO pending_lists (uid, data, ts) VALUES (?,?,?)",
            (uid, json.dumps(data, ensure_ascii=False), time.time()),
        )


def pending_get(uid: int) -> Optional[dict]:
    with _ldb() as con:
        row = con.execute(
            "SELECT data, ts FROM pending_lists WHERE uid=?", (uid,)
        ).fetchone()
    if not row:
        return None
    if time.time() - row[1] > _PENDING_TTL:
        pending_del(uid)
        return None
    return json.loads(row[0])


def pending_del(uid: int) -> None:
    with _ldb() as con:
        con.execute("DELETE FROM pending_lists WHERE uid=?", (uid,))


def pending_pop(uid: int) -> Optional[dict]:
    data = pending_get(uid)
    if data is not None:
        pending_del(uid)
    return data


# ── PG repos ──────────────────────────────────────────────────────────────────

_nexus_repo = PgNexusListsRepo()
_arcana_repo = PgArcanaInventoryRepo()


def _get_repo(bot_name: str):
    if bot_name == "☀️ Nexus":
        return _nexus_repo
    return _arcana_repo


# ── Domain → dict converters (stable API for handlers) ────────────────────────

def _item_to_dict(item: ListItem) -> dict:
    """ListItem → dict with same keys as old _extract_page_data."""
    return {
        "id": str(item.id),
        "name": item.name,
        "type": _notion_type(item.list_type),
        "status": _notion_status(item.status),
        "category": item.category,
        "quantity": item.quantity,
        "note": item.note,
        "price": item.price_actual,
        "price_plan": item.price_plan,
        "source": item.store,
        "stage": item.stage,
        "expiry": item.expires_at or "",
        "remind_days": item.remind_days,
        "priority": _notion_priority(item.priority),
        "recurring": item.is_recurring,
        "group": item.group_name,
        "task_rel": item.task_id,
        "work_rel": item.works_id,
    }


def _inv_to_dict(item: InventoryItem) -> dict:
    """InventoryItem → dict with same keys as old _extract_page_data."""
    return {
        "id": str(item.id),
        "name": item.name,
        "type": _notion_type(item.list_type),
        "status": _notion_status(item.status),
        "category": item.category,
        "quantity": item.quantity,
        "note": item.note,
        "price": None,
        "price_plan": None,
        "source": "",
        "stage": None,
        "expiry": item.expires_at or "",
        "remind_days": item.remind_days,
        "priority": "",
        "recurring": item.is_recurring,
        "group": item.group_name,
        "task_rel": "",
        "work_rel": item.works_id,
    }


def _to_dict(item) -> dict:
    if isinstance(item, ListItem):
        return _item_to_dict(item)
    return _inv_to_dict(item)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _today_iso() -> str:
    return datetime.now(timezone(timedelta(hours=3))).strftime("%Y-%m-%d")


_PREF_KEYWORDS = re.compile(
    r"(бренд|марк[аи]|магазин|размер|объ[её]м|вкус|литр|мл|гр|упаковк|пачк|штук|покупа[ейю]|бер[уёе]|предпочита)",
    re.IGNORECASE,
)


async def _search_memory_for_prefs(item_name: str) -> str:
    """Ищет предпочтения в 🧠 Память (PG) по названию айтема."""
    from core.repos.memory_repo import _repo as _mem_repo
    if not item_name.strip():
        return ""
    try:
        mems = await _mem_repo.search([item_name.lower().strip()], page_size=3)
        texts = []
        for m in mems:
            fact = m.fact or ""
            if not fact or len(fact) < 5:
                continue
            if _PREF_KEYWORDS.search(fact):
                texts.append(fact)
        return "; ".join(texts) if texts else ""
    except Exception as e:
        logger.warning("_search_memory_for_prefs(%s): %s", item_name, e)
        return ""


async def search_memory_categories(item_names: list) -> dict:
    """Ищет в 🧠 Память (PG) маппинги категорий для списка айтемов."""
    from core.repos.memory_repo import _repo as _mem_repo
    if not item_names:
        return {}
    result: dict = {}
    for name in item_names:
        name_clean = name.lower().strip()
        if not name_clean or len(name_clean) < 2:
            continue
        try:
            mems = await _mem_repo.search([name_clean], page_size=3)
            for m in mems:
                fact_lower = (m.fact or "").lower()
                for cat in LIST_CATEGORIES:
                    cat_name = cat.split(" ", 1)[-1].lower() if " " in cat else cat.lower()
                    if cat_name in fact_lower:
                        result[name] = cat
                        break
                if name in result:
                    break
        except Exception as e:
            logger.warning("search_memory_categories(%s): %s", name, e)
    return result


async def find_task_by_name(
    query: str, user_page_id: str, db_id: str = "", title_prop: str = "Задача",
) -> list:
    """Поиск задачи/работы в Notion ✅ Задачи по названию.

    Stays on Notion — Tasks DB not yet migrated to PG.
    """
    if not db_id:
        db_id = os.environ.get("NOTION_DB_TASKS") or config.nexus.db_tasks
    if not db_id:
        return []
    conditions: list = [
        {"property": title_prop, "title": {"contains": query}},
        {"property": "Статус", "status": {"does_not_equal": "Done"}},
        {"property": "Статус", "status": {"does_not_equal": "Archived"}},
    ]
    if user_page_id:
        conditions.append({"property": "🪪 Пользователи", "relation": {"contains": user_page_id}})
    try:
        pages = await query_pages(db_id, filters={"and": conditions}, page_size=10)
    except Exception as e:
        logger.error("find_task_by_name(%s): %s", query, e)
        return []
    results = []
    for p in pages:
        props = p.get("properties", {})
        title_parts = props.get(title_prop, {}).get("title", [])
        name = title_parts[0]["plain_text"] if title_parts else "—"
        status = (props.get("Статус", {}).get("status") or {}).get("name", "")
        results.append({"id": p["id"], "name": name, "status": status})
    return results


# ── CRUD ──────────────────────────────────────────────────────────────────────

async def add_items(
    items: list,
    list_type: str,
    bot_name: str,
    user_page_id: str,
) -> list:
    """Создать айтемы в PG (nexus_lists или arcana_inventory по bot_name).

    GUARD: BARTER_CATEGORY ('🔄 Бартер') заблокирован для nexus_lists.
    """
    is_nexus = bot_name == "☀️ Nexus"
    created = []

    for item in items:
        name = item.get("name", "").strip()
        if not name:
            continue

        category = item.get("category", "💳 Прочее")
        note = item.get("note", "")

        if is_nexus and category == BARTER_CATEGORY:
            logger.error("add_items: barter blocked for nexus_lists — sanitized to Прочее")
            category = "💳 Прочее"

        if list_type == "📋 Чеклист" and not item.get("category"):
            category = ""

        if list_type == "🛒 Покупки" and is_nexus:
            pref = await _search_memory_for_prefs(name)
            if pref:
                note = "%s; %s" % (note, pref) if note else pref

        remind_days = item.get("remind_days")
        if list_type == "📦 Инвентарь" and not remind_days:
            remind_days = REMIND_DEFAULTS.get(category)

        qty_val = item.get("qty") if item.get("qty") is not None else item.get("quantity")
        expiry_val = item.get("expiry") or item.get("expires")

        try:
            if is_nexus:
                new_item = await _nexus_repo.add_item(
                    name=name,
                    list_type=list_type,
                    category=category,
                    quantity=float(qty_val) if qty_val is not None else None,
                    note=note or "",
                    price_plan=float(item["price_plan"]) if item.get("price_plan") else None,
                    store=item.get("source") or "",
                    priority=item.get("priority") or "",
                    group_name=item.get("group") or "",
                    is_recurring=bool(item.get("recurring")),
                    remind_days=int(remind_days) if remind_days else None,
                    expires_at=str(expiry_val)[:10] if expiry_val else None,
                    stage=int(item["stage"]) if item.get("stage") else None,
                    task_id=item.get("task_rel") or "",
                    works_id=item.get("work_rel") or "",
                    user_notion_id=user_page_id or "",
                )
            else:
                new_item = await _arcana_repo.add_item(
                    name=name,
                    list_type=list_type,
                    category=category,
                    quantity=float(qty_val) if qty_val is not None else None,
                    note=note or "",
                    group_name=item.get("group") or "",
                    is_recurring=bool(item.get("recurring")),
                    remind_days=int(remind_days) if remind_days else None,
                    expires_at=str(expiry_val)[:10] if expiry_val else None,
                    works_id=item.get("work_rel") or "",
                    user_notion_id=user_page_id or "",
                )

            if new_item:
                created.append({
                    "id": str(new_item.id),
                    "name": name,
                    "type": list_type,
                    "category": category,
                })
                logger.info("add_items: created %s '%s' cat=%s", list_type, name, category)
        except Exception as e:
            logger.error("add_items: failed '%s': %s", name, e)

    return created


async def get_list(
    list_type: Optional[str],
    bot_name: str,
    user_page_id: str,
    status: str = "Not started",
) -> list:
    """Получить айтемы. list_type=None → все типы. Фильтр: Бот + Статус + user."""
    if bot_name == "☀️ Nexus":
        items = await _nexus_repo.get_list(list_type, status, user_page_id)
        return [_item_to_dict(it) for it in items]
    else:
        items = await _arcana_repo.get_list(status=status, user_notion_id=user_page_id)
        return [_inv_to_dict(it) for it in items]


async def check_items(
    items: list,
    bot_name: str,
    user_page_id: str,
) -> dict:
    """Чек покупок. items: [{name?, category?, price}].

    1. Найти айтем в Not started по name
    2. Статус → Done, записать Цену (nexus_lists only)
    3. Запись в Финансы через finance_add()
    """
    is_nexus = bot_name == "☀️ Nexus"
    checked = []
    finance_results = []

    for item in items:
        name = item.get("name", "")
        price = item.get("price") or 0
        category = item.get("category")

        found = None
        found_id = None

        if is_nexus:
            results = await _nexus_repo.search(
                query=name,
                list_type="🛒 Покупки",
                status="Not started",
                user_notion_id=user_page_id,
            )
            if results:
                found = _item_to_dict(results[0])
                found_id = str(results[0].id)
        else:
            results = await _arcana_repo.search(
                query=name,
                status="Not started",
                user_notion_id=user_page_id,
            )
            if results:
                found = _inv_to_dict(results[0])
                found_id = str(results[0].id)

        if found and found_id:
            item_category = category or found.get("category", "💳 Прочее")
            if is_nexus:
                update_fields: dict = {"status": _pg_status("Done")}
                if price:
                    update_fields["price_actual"] = float(price)
                await _nexus_repo.update(found_id, **update_fields)
            else:
                await _arcana_repo.update_status(found_id, "Done")

            checked.append({
                "id": found_id,
                "name": found["name"],
                "price": price,
                "category": item_category,
            })

            if price:
                finance_cat = CATEGORY_TO_FINANCE.get(item_category, "💳 Прочее")
                fin_id = await finance_add(
                    date=_today_iso(),
                    amount=float(price),
                    category=finance_cat,
                    type_="💸 Расход",
                    source="💳 Карта",
                    description=found["name"],
                    bot_label=bot_name,
                    user_notion_id=user_page_id,
                )
                finance_results.append({"page_id": fin_id, "amount": price, "category": finance_cat})
        else:
            if price:
                finance_cat = CATEGORY_TO_FINANCE.get(category or "💳 Прочее", "💳 Прочее")
                fin_id = await finance_add(
                    date=_today_iso(),
                    amount=float(price),
                    category=finance_cat,
                    type_="💸 Расход",
                    source="💳 Карта",
                    description=name or "покупка",
                    bot_label=bot_name,
                    user_notion_id=user_page_id,
                )
                finance_results.append({"page_id": fin_id, "amount": price, "category": finance_cat})
            checked.append({"id": None, "name": name, "price": price, "category": category, "not_found": True})

    return {"checked": checked, "finance_results": finance_results}


async def check_items_bulk(
    total: int,
    breakdown: list,
    bot_name: str,
    user_page_id: str,
) -> dict:
    """Пакетный чек. breakdown: [{category, amount}]."""
    is_nexus = bot_name == "☀️ Nexus"
    checked = []
    finance_results = []

    for entry in breakdown:
        raw_cat = entry.get("category", "")
        amount = entry.get("amount") or 0
        if not amount:
            continue

        finance_cat = None
        for lc, fc in CATEGORY_TO_FINANCE.items():
            clean = lc.split(" ", 1)[-1].lower() if " " in lc else lc.lower()
            if clean in raw_cat.lower() or raw_cat.lower() in clean:
                finance_cat = fc
                break
        if not finance_cat:
            finance_cat = "💳 Прочее"

        if is_nexus:
            all_items = await _nexus_repo.get_list("🛒 Покупки", "Not started", user_page_id, page_size=100)
            for it in all_items:
                page_cat = it.category.split(" ", 1)[-1].lower() if it.category else ""
                if page_cat and (page_cat in raw_cat.lower() or raw_cat.lower() in page_cat):
                    await _nexus_repo.update_status(str(it.id), "Done")
                    checked.append({"id": str(it.id), "name": it.name})
        else:
            all_items = await _arcana_repo.get_list(status="Not started", user_notion_id=user_page_id)
            for it in all_items:
                page_cat = it.category.split(" ", 1)[-1].lower() if it.category else ""
                if page_cat and (page_cat in raw_cat.lower() or raw_cat.lower() in page_cat):
                    await _arcana_repo.update_status(str(it.id), "Done")
                    checked.append({"id": str(it.id), "name": it.name})

        fin_id = await finance_add(
            date=_today_iso(),
            amount=float(amount),
            category=finance_cat,
            type_="💸 Расход",
            source="💳 Карта",
            description="покупки (%s)" % raw_cat,
            bot_label=bot_name,
            user_notion_id=user_page_id,
        )
        finance_results.append({"page_id": fin_id, "amount": amount, "category": finance_cat})

    return {"checked": checked, "finance_results": finance_results, "total": total}


async def checklist_toggle(
    item_name: str,
    bot_name: str,
    user_page_id: str,
) -> dict:
    """Чек пункта чеклиста. После чека — проверить автозавершение группы."""
    if bot_name == "☀️ Nexus":
        results = await _nexus_repo.search(
            item_name, list_type="📋 Чеклист", status="Not started", user_notion_id=user_page_id
        )
        if not results:
            return {"error": "not_found", "name": item_name}
        it = results[0]
        await _nexus_repo.update_status(str(it.id), "Done")
        group = it.group_name
        group_complete = False
        if group:
            remaining = await _nexus_repo.get_group_remaining(group, "📋 Чеклист")
            group_complete = remaining == 0
        return {"checked": it.name, "group": group, "group_complete": group_complete}
    else:
        results = await _arcana_repo.search(
            item_name, status="Not started", user_notion_id=user_page_id
        )
        if not results:
            return {"error": "not_found", "name": item_name}
        it = results[0]
        await _arcana_repo.update_status(str(it.id), "Done")
        group = it.group_name
        group_complete = False
        if group:
            remaining = await _arcana_repo.get_group_remaining(group, "📋 Чеклист")
            group_complete = remaining == 0
        return {"checked": it.name, "group": group, "group_complete": group_complete}


async def checklist_toggle_by_id(page_id: str, bot_name: str) -> dict:
    """Toggle чеклист-айтема по id."""
    if bot_name == "☀️ Nexus":
        it = await _nexus_repo.get_by_id(page_id)
        if not it:
            return {"error": "not_found"}
        await _nexus_repo.update_status(page_id, "Done")
        group = it.group_name
        group_complete = False
        if group:
            remaining = await _nexus_repo.get_group_remaining(group, "📋 Чеклист")
            group_complete = remaining == 0
        return {"name": it.name, "group": group, "group_complete": group_complete}
    else:
        it = await _arcana_repo.get_by_id(page_id)
        if not it:
            return {"error": "not_found"}
        await _arcana_repo.update_status(page_id, "Done")
        group = it.group_name
        group_complete = False
        if group:
            remaining = await _arcana_repo.get_group_remaining(group, "📋 Чеклист")
            group_complete = remaining == 0
        return {"name": it.name, "group": group, "group_complete": group_complete}


async def buy_mark_done_by_id(page_id: str, price: float, bot_name: str, user_page_id: str) -> dict:
    """Отметить покупку Done по id, записать цену и в Финансы."""
    is_nexus = bot_name == "☀️ Nexus"
    it = None

    if is_nexus:
        it = await _nexus_repo.get_by_id(page_id)
    else:
        it = await _arcana_repo.get_by_id(page_id)

    if not it:
        return {"error": "not_found"}

    if is_nexus and isinstance(it, ListItem):
        update_fields: dict = {"status": _pg_status("Done")}
        if price:
            update_fields["price_actual"] = float(price)
        await _nexus_repo.update(page_id, **update_fields)
    else:
        await _arcana_repo.update_status(page_id, "Done")

    finance_result = None
    if price:
        item_category = it.category
        finance_cat = CATEGORY_TO_FINANCE.get(item_category, "💳 Прочее")
        fin_id = await finance_add(
            date=_today_iso(),
            amount=price,
            category=finance_cat,
            type_="💸 Расход",
            source="💳 Карта",
            description=it.name,
            bot_label=bot_name,
            user_notion_id=user_page_id,
        )
        finance_result = {"page_id": fin_id, "amount": price, "category": finance_cat}

    return {"name": it.name, "category": it.category, "finance": finance_result}


async def archive_items(page_ids: list) -> int:
    """Архивировать айтемы по списку id. Пробует nexus, потом arcana."""
    archived = 0
    for pid in page_ids:
        ok = await _nexus_repo.update_status(pid, "Archived")
        if not ok:
            ok = await _arcana_repo.update_status(pid, "Archived")
        if ok:
            archived += 1
    return archived


async def mark_items_done(page_ids: list) -> int:
    """Отметить айтемы Done по списку id. Пробует nexus, потом arcana."""
    done = 0
    for pid in page_ids:
        ok = await _nexus_repo.update_status(pid, "Done")
        if not ok:
            ok = await _arcana_repo.update_status(pid, "Done")
        if ok:
            done += 1
    return done


async def find_matching_items(
    description: str,
    category: str,
    bot_name: str,
    user_page_id: str,
) -> list:
    """Ищет в списке покупок Not started айтемы совпадающие с описанием расхода."""
    if not description:
        return []

    if bot_name == "☀️ Nexus":
        all_items = await _nexus_repo.get_list("🛒 Покупки", "Not started", user_page_id, page_size=100)
    else:
        all_items = await _arcana_repo.get_list(status="Not started", user_notion_id=user_page_id)

    desc_lower = description.lower().strip()
    matches = []
    for it in all_items:
        item_name = it.name.lower().strip()
        if not item_name:
            continue
        if item_name in desc_lower or desc_lower in item_name:
            matches.append(_to_dict(it))
    return matches


async def inventory_search(
    query: str,
    bot_name: str,
    user_page_id: str,
) -> list:
    """Поиск в инвентаре."""
    if bot_name == "☀️ Nexus":
        items = await _nexus_repo.search(
            query, list_type="📦 Инвентарь", user_notion_id=user_page_id, page_size=20
        )
        return [_item_to_dict(it) for it in items]
    else:
        items = await _arcana_repo.search(query, user_notion_id=user_page_id, page_size=20)
        return [_inv_to_dict(it) for it in items]


async def inventory_update(
    item_name: str,
    quantity: int,
    bot_name: str,
    user_page_id: str,
) -> dict:
    """Обновить количество. Если 0 → Archived, предложить в покупки."""
    if bot_name == "☀️ Nexus":
        results = await _nexus_repo.search(
            item_name, list_type="📦 Инвентарь", user_notion_id=user_page_id, page_size=5
        )
        if not results:
            return {"error": "not_found", "name": item_name}
        it = results[0]
        iid = str(it.id)
        if quantity <= 0:
            await _nexus_repo.update(iid, quantity=0.0, status=_pg_status("Archived"))
        else:
            await _nexus_repo.update(iid, quantity=float(quantity))
    else:
        results = await _arcana_repo.search(item_name, user_notion_id=user_page_id, page_size=5)
        if not results:
            return {"error": "not_found", "name": item_name}
        it = results[0]
        iid = str(it.id)
        if quantity <= 0:
            await _arcana_repo.update(iid, quantity=0.0, status=_pg_status("Archived"))
        else:
            await _arcana_repo.update(iid, quantity=float(quantity))

    return {
        "updated": it.name,
        "quantity": quantity,
        "archived": quantity <= 0,
        "suggest_buy": quantity <= 0,
        "category": it.category,
    }


async def clone_recurring() -> int:
    """Cron: найти Done + is_recurring → клонировать со статусом Not started."""
    cloned = 0

    nl_items = await _nexus_repo.get_recurring()
    for it in nl_items:
        try:
            new = await _nexus_repo.add_item(
                name=it.name,
                list_type=_notion_type(it.list_type),
                category=it.category,
                priority=_notion_priority(it.priority),
                group_name=it.group_name,
                is_recurring=True,
                user_notion_id=it.user_notion_id,
            )
            if new:
                await _nexus_repo.update_status(str(it.id), "Archived")
                cloned += 1
                logger.info("clone_recurring(nexus): cloned '%s' → %s", it.name, new.id)
        except Exception as e:
            logger.error("clone_recurring(nexus): %s", e)

    ai_items = await _arcana_repo.get_recurring()
    for it in ai_items:
        try:
            new = await _arcana_repo.add_item(
                name=it.name,
                list_type=_notion_type(it.list_type),
                category=it.category,
                group_name=it.group_name,
                is_recurring=True,
                user_notion_id=it.user_notion_id,
            )
            if new:
                await _arcana_repo.update_status(str(it.id), "Archived")
                cloned += 1
                logger.info("clone_recurring(arcana): cloned '%s' → %s", it.name, new.id)
        except Exception as e:
            logger.error("clone_recurring(arcana): %s", e)

    return cloned


async def get_list_summary(
    user_notion_id: str,
    bot_name: str,
    type_: Optional[str] = None,
    group: Optional[str] = None,
    category: Optional[str] = None,
) -> dict:
    """Агрегации по 🗒️ Списки."""
    if bot_name == "☀️ Nexus":
        items_raw = await _nexus_repo.get_summary_items(user_notion_id, type_, group, category)
        items = [_item_to_dict(it) for it in items_raw]
    else:
        items_raw = await _arcana_repo.get_list(category=category, user_notion_id=user_notion_id)
        items = [_inv_to_dict(it) for it in items_raw]
        if group:
            g_target = group.strip().lower()
            items = [it for it in items if (it.get("group") or "").strip().lower() == g_target]

    plan_total = 0.0
    actual_total = 0.0
    count_open = 0
    count_done = 0
    for it in items:
        plan_total += float(it.get("price_plan") or 0)
        if it.get("status") == "Done":
            actual_total += float(it.get("price") or 0)
            count_done += 1
        else:
            count_open += 1

    return {
        "plan_total": plan_total,
        "actual_total": actual_total,
        "count_total": len(items),
        "count_open": count_open,
        "count_done": count_done,
        "items": items,
    }


async def check_expiry(bot, user_tz_offset: int = 3) -> int:
    """Cron: найти инвентарь где срок годности подходит → уведомить."""
    from datetime import date as date_type

    today = datetime.now(timezone(timedelta(hours=user_tz_offset))).date()
    sent = 0

    async def _notify_items(items_raw, to_dict_fn):
        nonlocal sent
        for it in items_raw:
            expiry_str = it.expires_at or ""
            if not expiry_str:
                continue
            try:
                expiry_date = date_type.fromisoformat(expiry_str[:10])
            except ValueError:
                continue

            remind_days = int(it.remind_days or 0) or 7
            remind_date = expiry_date - timedelta(days=remind_days)

            if remind_date <= today <= expiry_date:
                days_left = (expiry_date - today).days
                for tg_id in config.allowed_ids:
                    try:
                        emoji = "⚠️" if days_left <= 3 else "📦"
                        await bot.send_message(
                            tg_id,
                            "%s <b>Срок годности:</b> %s\nОсталось %d дн. (до %s)\nКоличество: %s" % (
                                emoji, it.name, days_left, expiry_str[:10], it.quantity or "?"
                            ),
                            parse_mode="HTML",
                        )
                        sent += 1
                    except Exception as e:
                        logger.error("check_expiry: send error '%s': %s", it.name, e)

    nl_items = await _nexus_repo.get_expiry_due(today)
    await _notify_items(nl_items, _item_to_dict)

    ai_items = await _arcana_repo.get_expiry_due(today)
    await _notify_items(ai_items, _inv_to_dict)

    return sent
