"""core/list_manager.py — бизнес-логика для 🗒️ Списки (Покупки / Чеклист / Инвентарь)."""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from core.config import config
from core.notion_client import (
    page_create, update_page, db_query, query_pages,
    _title, _text, _number, _select, _status, _date, _relation,
    _extract_text, _extract_number, _extract_select,
    finance_add,
)

logger = logging.getLogger("nexus.list_manager")

# ── Константы ─────────────────────────────────────────────────────────────────

CATEGORY_TO_FINANCE = {
    "🐾 Коты": "🐾 Коты",
    "🍜 Продукты": "🍜 Продукты",
    "🏠 Жилье": "🏠 Жилье",
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
    "🐾 Коты", "🍜 Продукты", "🏠 Жилье", "🏥 Здоровье", "💅 Бьюти",
    "👗 Гардероб", "💻 Подписки", "📚 Хобби/Учеба", "🚬 Привычки",
    "💳 Прочее", "🕯️ Расходники", "🌿 Травы/Масла", "🃏 Карты/Колоды",
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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _db_id() -> str:
    return os.environ.get("NOTION_DB_LISTS") or config.db_lists


def _today_iso() -> str:
    return datetime.now(timezone(timedelta(hours=3))).strftime("%Y-%m-%d")


def _checkbox(val: bool) -> dict:
    return {"checkbox": val}


async def search_memory_categories(item_names: list[str]) -> dict[str, str]:
    """Ищет в 🧠 Память маппинги категорий для списка айтемов.

    Ищет записи содержащие название айтема + слово "категория" или название категории.
    Возвращает {item_name: "🚬 Привычки", ...} для найденных.
    """
    from core.notion_client import query_pages as qp
    db_mem = os.environ.get("NOTION_DB_MEMORY") or config.nexus.db_memory
    if not db_mem or not item_names:
        return {}

    result: dict[str, str] = {}
    # Один запрос: ищем все записи содержащие любое из имён
    for name in item_names:
        name_clean = name.lower().strip()
        if not name_clean or len(name_clean) < 2:
            continue
        try:
            pages = await qp(
                db_mem,
                filters={"and": [
                    {"property": "Текст", "title": {"contains": name_clean}},
                    {"property": "Актуально", "checkbox": {"equals": True}},
                ]},
                page_size=3,
            )
            for p in pages:
                title_parts = p.get("properties", {}).get("Текст", {}).get("title", [])
                fact = title_parts[0].get("plain_text", "") if title_parts else ""
                fact_lower = fact.lower()
                # Ищем упоминание категории в тексте факта
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


_PREF_KEYWORDS = re.compile(
    r"(бренд|марк[аи]|магазин|размер|объ[её]м|вкус|литр|мл|гр|упаковк|пачк|штук|покупа[ейю]|бер[уёе]|предпочита)",
    re.IGNORECASE,
)


async def _search_memory_for_prefs(item_name: str) -> str:
    """Ищет в 🧠 Память предпочтения по названию айтема (бренд, магазин, размер).

    Возвращает текст ТОЛЬКО если в записи есть полезная инфо (бренд/магазин/размер).
    Записи-маппинги категорий (вида "X = категория") пропускаются.
    """
    from core.notion_client import query_pages as qp
    db_mem = os.environ.get("NOTION_DB_MEMORY") or config.nexus.db_memory
    if not db_mem:
        return ""
    try:
        results = await qp(
            db_mem,
            filters={"and": [
                {"property": "Текст", "title": {"contains": item_name.lower()}},
                {"property": "Актуально", "checkbox": {"equals": True}},
            ]},
            page_size=3,
        )
        if not results:
            return ""
        texts = []
        for r in results:
            title_parts = r.get("properties", {}).get("Текст", {}).get("title", [])
            if not title_parts:
                continue
            fact = title_parts[0].get("plain_text", "")
            # Пропускаем маппинги категорий ("монстр = привычки") и короткие записи
            if not fact or len(fact) < 5:
                continue
            # Только записи с полезной инфой (бренд, магазин, размер, объём)
            if _PREF_KEYWORDS.search(fact):
                texts.append(fact)
        return "; ".join(texts) if texts else ""
    except Exception as e:
        logger.warning("_search_memory_for_prefs(%s): %s", item_name, e)
        return ""


async def find_task_by_name(
    query: str, user_page_id: str, db_id: str = "", title_prop: str = "Задача",
) -> list[dict]:
    """Поиск задачи/работы по названию. Фильтр: Статус != Done, != Archived.

    title_prop: название title-свойства в БД (по умолчанию "Задача", для Работ — "Работа").
    Возвращает [{id, name, status}].
    """
    if not db_id:
        db_id = os.environ.get("NOTION_DB_TASKS") or config.nexus.db_tasks
    if not db_id:
        return []
    conditions: list[dict] = [
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


def _extract_page_data(page: dict) -> dict:
    """Извлечь данные из Notion page для ответа."""
    props = page.get("properties", {})
    return {
        "id": page["id"],
        "name": _extract_text(props.get("Название", {})),
        "type": _extract_select(props.get("Тип", {})),
        "status": (props.get("Статус", {}).get("status") or {}).get("name", ""),
        "category": _extract_select(props.get("Категория", {})),
        "quantity": _extract_number(props.get("Количество", {})),
        "note": _extract_text(props.get("Заметка", {})),
        "price": _extract_number(props.get("Цена", {})),
        "expiry": (props.get("Срок годности", {}).get("date") or {}).get("start", ""),
        "remind_days": _extract_number(props.get("Напомнить за", {})),
        "priority": _extract_select(props.get("Приоритет", {})),
        "recurring": (props.get("Повторяющийся", {}).get("checkbox") or False),
        "group": _extract_text(props.get("Группа", {})),
        "task_rel": (props.get("✅ Задачи", {}).get("relation") or [{}])[0].get("id", ""),
        "work_rel": (props.get("🔮 Работы", {}).get("relation") or [{}])[0].get("id", ""),
    }


# ── CRUD ──────────────────────────────────────────────────────────────────────

async def add_items(
    items: list[dict],
    list_type: str,
    bot_name: str,
    user_page_id: str,
) -> list[dict]:
    """Создать айтемы в Notion.

    Каждый dict: {name, category?, quantity?, note?, priority?, group?,
                  task_rel?, work_rel?, recurring?, expiry?, remind_days?}

    Для покупок — ищем предпочтения в Памяти и дописываем в note.
    Для инвентаря — проставляем remind_days из REMIND_DEFAULTS если не указано.
    """
    db = _db_id()
    if not db:
        logger.error("add_items: NOTION_DB_LISTS not set")
        return []

    created = []
    for item in items:
        name = item.get("name", "").strip()
        if not name:
            continue

        category = item.get("category", "💳 Прочее")
        note = item.get("note", "")

        # Для чеклистов — категория не нужна
        if list_type == "📋 Чеклист":
            category = ""

        # Для покупок — поиск предпочтений в Памяти
        if list_type == "🛒 Покупки":
            pref = await _search_memory_for_prefs(name)
            if pref:
                note = f"{note}; {pref}".strip("; ") if note else pref

        # Для инвентаря — дефолтные напоминания
        remind_days = item.get("remind_days")
        if list_type == "📦 Инвентарь" and not remind_days:
            remind_days = REMIND_DEFAULTS.get(category)

        props: dict = {
            "Название": _title(name),
            "Тип": _select(list_type),
            "Статус": _status("Not started"),
            "Бот": _select(bot_name),
        }
        if category:
            props["Категория"] = _select(category)

        if item.get("quantity"):
            props["Количество"] = _number(float(item["quantity"]))
        if note:
            props["Заметка"] = _text(note)
        if item.get("priority"):
            props["Приоритет"] = _select(item["priority"])
        if item.get("group"):
            props["Группа"] = _text(item["group"])
        if item.get("recurring"):
            props["Повторяющийся"] = _checkbox(True)
        if item.get("expiry"):
            props["Срок годности"] = _date(item["expiry"])
        if remind_days:
            props["Напомнить за"] = _number(float(remind_days))
        if user_page_id:
            props["🪪 Пользователи"] = _relation(user_page_id)
        if item.get("task_rel"):
            props["✅ Задачи"] = _relation(item["task_rel"])
        if item.get("work_rel"):
            props["🔮 Работы"] = _relation(item["work_rel"])

        page_id = await page_create(db, props)
        if page_id:
            created.append({"id": page_id, "name": name, "type": list_type, "category": category})
            logger.info("add_items: created %s '%s' cat=%s", list_type, name, category)

    return created


async def get_list(
    list_type: str | None,
    bot_name: str,
    user_page_id: str,
    status: str = "Not started",
) -> list[dict]:
    """Получить айтемы. list_type=None → все типы. Фильтр: Бот + Статус + user."""
    db = _db_id()
    if not db:
        return []

    conditions: list[dict] = [
        {"property": "Бот", "select": {"equals": bot_name}},
        {"property": "Статус", "status": {"equals": status}},
    ]
    if list_type:
        conditions.append({"property": "Тип", "select": {"equals": list_type}})
    if user_page_id:
        conditions.append({"property": "🪪 Пользователи", "relation": {"contains": user_page_id}})

    pages = await db_query(db, filter_obj={"and": conditions}, page_size=100)
    return [_extract_page_data(p) for p in pages]


async def check_items(
    items: list[dict],
    bot_name: str,
    user_page_id: str,
) -> dict:
    """Чек покупок. items: [{name?, category?, price}].

    1. Найти айтемы в Not started по name/category
    2. Статус → Done, записать Цену
    3. Для каждого — запись в Финансы через finance_add()
    4. Вернуть {checked: [...], finance_results: [...]}
    """
    db = _db_id()
    checked = []
    finance_results = []

    for item in items:
        name = item.get("name", "")
        price = item.get("price") or 0
        category = item.get("category")

        # Найти айтем в списке
        conditions: list[dict] = [
            {"property": "Бот", "select": {"equals": bot_name}},
            {"property": "Статус", "status": {"equals": "Not started"}},
            {"property": "Тип", "select": {"equals": "🛒 Покупки"}},
        ]
        if name:
            conditions.append({"property": "Название", "title": {"contains": name}})
        if user_page_id:
            conditions.append({"property": "🪪 Пользователи", "relation": {"contains": user_page_id}})

        pages = await db_query(db, filter_obj={"and": conditions}, page_size=5)

        if pages:
            page = pages[0]
            page_id = page["id"]
            page_data = _extract_page_data(page)
            item_category = category or page_data.get("category", "💳 Прочее")

            # Обновить статус + цену
            update_props: dict = {"Статус": _status("Done")}
            if price:
                update_props["Цена"] = _number(float(price))
            await update_page(page_id, update_props)
            checked.append({"id": page_id, "name": page_data["name"], "price": price, "category": item_category})

            # Запись в Финансы
            if price:
                finance_cat = CATEGORY_TO_FINANCE.get(item_category, "💳 Прочее")
                fin_id = await finance_add(
                    date=_today_iso(),
                    amount=float(price),
                    category=finance_cat,
                    type_="💸 Расход",
                    source="💳 Карта",
                    description=page_data["name"],
                    bot_label=bot_name,
                    user_notion_id=user_page_id,
                )
                finance_results.append({"page_id": fin_id, "amount": price, "category": finance_cat})
        else:
            # Айтем не найден в списке — всё равно записать в финансы если есть цена
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
    breakdown: list[dict],
    bot_name: str,
    user_page_id: str,
) -> dict:
    """Пакетный чек. breakdown: [{category, amount}].
    Чекает все Not started айтемы указанных категорий.
    Пишет отдельные записи в Финансы по каждой категории.
    """
    db = _db_id()
    checked = []
    finance_results = []

    for entry in breakdown:
        raw_cat = entry.get("category", "")
        amount = entry.get("amount") or 0
        if not amount:
            continue

        # Маппим категорию
        finance_cat = None
        for lc, fc in CATEGORY_TO_FINANCE.items():
            clean = lc.split(" ", 1)[-1].lower() if " " in lc else lc.lower()
            if clean in raw_cat.lower() or raw_cat.lower() in clean:
                finance_cat = fc
                break
        if not finance_cat:
            finance_cat = "💳 Прочее"

        # Чекаем все Not started айтемы этой категории
        conditions: list[dict] = [
            {"property": "Бот", "select": {"equals": bot_name}},
            {"property": "Статус", "status": {"equals": "Not started"}},
            {"property": "Тип", "select": {"equals": "🛒 Покупки"}},
        ]
        if user_page_id:
            conditions.append({"property": "🪪 Пользователи", "relation": {"contains": user_page_id}})

        pages = await db_query(db, filter_obj={"and": conditions}, page_size=50)
        for page in pages:
            page_data = _extract_page_data(page)
            page_cat = page_data.get("category", "").split(" ", 1)[-1].lower() if page_data.get("category") else ""
            if page_cat and page_cat in raw_cat.lower() or raw_cat.lower() in page_cat:
                await update_page(page["id"], {"Статус": _status("Done")})
                checked.append({"id": page["id"], "name": page_data["name"]})

        # Записать в Финансы
        fin_id = await finance_add(
            date=_today_iso(),
            amount=float(amount),
            category=finance_cat,
            type_="💸 Расход",
            source="💳 Карта",
            description=f"покупки ({raw_cat})",
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
    db = _db_id()
    if not db:
        return {"error": "NOTION_DB_LISTS not set"}

    conditions: list[dict] = [
        {"property": "Бот", "select": {"equals": bot_name}},
        {"property": "Статус", "status": {"equals": "Not started"}},
        {"property": "Тип", "select": {"equals": "📋 Чеклист"}},
        {"property": "Название", "title": {"contains": item_name}},
    ]
    if user_page_id:
        conditions.append({"property": "🪪 Пользователи", "relation": {"contains": user_page_id}})

    pages = await db_query(db, filter_obj={"and": conditions}, page_size=5)
    if not pages:
        return {"error": "not_found", "name": item_name}

    page = pages[0]
    page_data = _extract_page_data(page)
    await update_page(page["id"], {"Статус": _status("Done")})

    # Проверяем автозавершение группы
    group = page_data.get("group", "")
    group_complete = False
    if group:
        remaining = await db_query(db, filter_obj={"and": [
            {"property": "Бот", "select": {"equals": bot_name}},
            {"property": "Группа", "rich_text": {"equals": group}},
            {"property": "Тип", "select": {"equals": "📋 Чеклист"}},
            {"property": "Статус", "status": {"does_not_equal": "Done"}},
            {"property": "Статус", "status": {"does_not_equal": "Archived"}},
        ]}, page_size=1)
        group_complete = len(remaining) == 0

    return {
        "checked": page_data["name"],
        "group": group,
        "group_complete": group_complete,
    }


async def checklist_toggle_by_id(page_id: str, bot_name: str) -> dict:
    """Toggle чеклист-айтема по page_id. Возвращает {name, group, group_complete}."""
    db = _db_id()
    if not db:
        return {"error": "db_not_set"}

    from core.notion_client import get_notion
    try:
        raw = await get_notion().pages.retrieve(page_id)
    except Exception as e:
        logger.error("checklist_toggle_by_id retrieve %s: %s", page_id, e)
        return {"error": "not_found"}

    page_data = _extract_page_data(raw)
    await update_page(page_id, {"Статус": _status("Done")})

    group = page_data.get("group", "")
    group_complete = False
    if group:
        remaining = await db_query(db, filter_obj={"and": [
            {"property": "Бот", "select": {"equals": bot_name}},
            {"property": "Группа", "rich_text": {"equals": group}},
            {"property": "Тип", "select": {"equals": "📋 Чеклист"}},
            {"property": "Статус", "status": {"does_not_equal": "Done"}},
            {"property": "Статус", "status": {"does_not_equal": "Archived"}},
        ]}, page_size=1)
        group_complete = len(remaining) == 0

    return {"name": page_data["name"], "group": group, "group_complete": group_complete}


async def buy_mark_done_by_id(page_id: str, price: float, bot_name: str, user_page_id: str) -> dict:
    """Отметить покупку Done по page_id, записать цену и в Финансы."""
    from core.notion_client import get_notion
    try:
        raw = await get_notion().pages.retrieve(page_id)
    except Exception as e:
        logger.error("buy_mark_done_by_id retrieve %s: %s", page_id, e)
        return {"error": "not_found"}

    page_data = _extract_page_data(raw)
    update_props: dict = {"Статус": _status("Done")}
    if price:
        update_props["Цена"] = _number(price)
    await update_page(page_id, update_props)

    finance_result = None
    if price:
        item_category = page_data.get("category", "💳 Прочее")
        finance_cat = CATEGORY_TO_FINANCE.get(item_category, "💳 Прочее")
        fin_id = await finance_add(
            date=_today_iso(),
            amount=price,
            category=finance_cat,
            type_="💸 Расход",
            source="💳 Карта",
            description=page_data["name"],
            bot_label=bot_name,
            user_notion_id=user_page_id,
        )
        finance_result = {"page_id": fin_id, "amount": price, "category": finance_cat}

    return {"name": page_data["name"], "category": page_data.get("category", ""), "finance": finance_result}


async def archive_items(page_ids: list[str]) -> int:
    """Архивировать айтемы по списку page_id. Возвращает количество архивированных."""
    archived = 0
    for pid in page_ids:
        try:
            await update_page(pid, {"Статус": _status("Archived")})
            archived += 1
        except Exception as e:
            logger.error("archive_items %s: %s", pid, e)
    return archived


async def mark_items_done(page_ids: list[str]) -> int:
    """Отметить айтемы Done по списку page_id."""
    done = 0
    for pid in page_ids:
        try:
            await update_page(pid, {"Статус": _status("Done")})
            done += 1
        except Exception as e:
            logger.error("mark_items_done %s: %s", pid, e)
    return done


async def find_matching_items(
    description: str,
    category: str,
    bot_name: str,
    user_page_id: str,
) -> list[dict]:
    """Ищет в списке покупок Not started айтемы, совпадающие с описанием расхода.

    Матчинг: item_name.lower() in description.lower()
             OR description.lower() in item_name.lower()
    + категория совпадает (если указана).
    """
    db = _db_id()
    if not db or not description:
        return []

    conditions: list[dict] = [
        {"property": "Бот", "select": {"equals": bot_name}},
        {"property": "Тип", "select": {"equals": "🛒 Покупки"}},
        {"property": "Статус", "status": {"equals": "Not started"}},
    ]
    if user_page_id:
        conditions.append({"property": "🪪 Пользователи", "relation": {"contains": user_page_id}})

    pages = await db_query(db, filter_obj={"and": conditions}, page_size=50)
    desc_lower = description.lower().strip()
    matches = []
    for p in pages:
        data = _extract_page_data(p)
        item_name = (data.get("name") or "").lower().strip()
        if not item_name:
            continue
        # Нестрогий матч: "молоко" ↔ "молоко", но НЕ "молочко"
        if item_name in desc_lower or desc_lower in item_name:
            matches.append(data)
    return matches


async def inventory_search(
    query: str,
    bot_name: str,
    user_page_id: str,
) -> list[dict]:
    """Поиск в инвентаре. Возвращает [{name, category, quantity, note, expiry}]."""
    db = _db_id()
    if not db:
        return []

    conditions: list[dict] = [
        {"property": "Бот", "select": {"equals": bot_name}},
        {"property": "Тип", "select": {"equals": "📦 Инвентарь"}},
        {"property": "Статус", "status": {"does_not_equal": "Archived"}},
        {"property": "Название", "title": {"contains": query}},
    ]
    if user_page_id:
        conditions.append({"property": "🪪 Пользователи", "relation": {"contains": user_page_id}})

    pages = await db_query(db, filter_obj={"and": conditions}, page_size=20)
    return [_extract_page_data(p) for p in pages]


async def inventory_update(
    item_name: str,
    quantity: int,
    bot_name: str,
    user_page_id: str,
) -> dict:
    """Обновить количество. Если 0 → Archived, предложить в покупки."""
    db = _db_id()
    if not db:
        return {"error": "NOTION_DB_LISTS not set"}

    conditions: list[dict] = [
        {"property": "Бот", "select": {"equals": bot_name}},
        {"property": "Тип", "select": {"equals": "📦 Инвентарь"}},
        {"property": "Статус", "status": {"does_not_equal": "Archived"}},
        {"property": "Название", "title": {"contains": item_name}},
    ]
    if user_page_id:
        conditions.append({"property": "🪪 Пользователи", "relation": {"contains": user_page_id}})

    pages = await db_query(db, filter_obj={"and": conditions}, page_size=5)
    if not pages:
        return {"error": "not_found", "name": item_name}

    page = pages[0]
    page_data = _extract_page_data(page)
    update_props: dict = {"Количество": _number(float(quantity))}

    suggest_buy = False
    if quantity <= 0:
        update_props["Статус"] = _status("Archived")
        suggest_buy = True

    await update_page(page["id"], update_props)

    return {
        "updated": page_data["name"],
        "quantity": quantity,
        "archived": quantity <= 0,
        "suggest_buy": suggest_buy,
        "category": page_data.get("category", ""),
    }


async def clone_recurring() -> int:
    """Cron: найти Done + Повторяющийся=true → создать клон со статусом Not started.
    Возвращает количество клонированных айтемов.
    """
    db = _db_id()
    if not db:
        return 0

    pages = await db_query(db, filter_obj={"and": [
        {"property": "Статус", "status": {"equals": "Done"}},
        {"property": "Повторяющийся", "checkbox": {"equals": True}},
    ]}, page_size=50)

    cloned = 0
    for page in pages:
        data = _extract_page_data(page)
        props_raw = page.get("properties", {})

        # Создаём клон
        new_props: dict = {
            "Название": _title(data["name"]),
            "Тип": _select(data["type"]),
            "Статус": _status("Not started"),
            "Повторяющийся": _checkbox(True),
        }
        if data["category"]:
            new_props["Категория"] = _select(data["category"])
        if data["priority"]:
            new_props["Приоритет"] = _select(data["priority"])
        if data["group"]:
            new_props["Группа"] = _text(data["group"])

        # Копируем Бот
        bot = _extract_select(props_raw.get("Бот", {}))
        if bot:
            new_props["Бот"] = _select(bot)

        # Копируем user relation
        user_rel = props_raw.get("🪪 Пользователи", {}).get("relation", [])
        if user_rel:
            new_props["🪪 Пользователи"] = {"relation": user_rel}

        page_id = await page_create(db, new_props)
        if page_id:
            # Архивируем старый
            await update_page(page["id"], {"Статус": _status("Archived")})
            cloned += 1
            logger.info("clone_recurring: cloned '%s' → %s", data["name"], page_id)

    return cloned


async def check_expiry(bot, user_tz_offset: int = 3) -> int:
    """Cron: найти инвентарь где Срок годности - Напомнить_за <= today → уведомить.
    Возвращает количество отправленных уведомлений.
    """
    db = _db_id()
    if not db:
        return 0

    today = datetime.now(timezone(timedelta(hours=user_tz_offset))).date()

    # Берём весь активный инвентарь с датой срока годности
    pages = await db_query(db, filter_obj={"and": [
        {"property": "Тип", "select": {"equals": "📦 Инвентарь"}},
        {"property": "Статус", "status": {"does_not_equal": "Archived"}},
        {"property": "Срок годности", "date": {"is_not_empty": True}},
    ]}, page_size=100)

    sent = 0
    for page in pages:
        data = _extract_page_data(page)
        expiry_str = data.get("expiry", "")
        if not expiry_str:
            continue

        try:
            expiry_date = datetime.strptime(expiry_str[:10], "%Y-%m-%d").date()
        except ValueError:
            continue

        remind_days = int(data.get("remind_days") or 0) or 7
        remind_date = expiry_date - timedelta(days=remind_days)

        if remind_date <= today <= expiry_date:
            days_left = (expiry_date - today).days
            # Отправляем уведомление через бот
            from core.config import config
            for tg_id in config.allowed_ids:
                try:
                    emoji = "⚠️" if days_left <= 3 else "📦"
                    await bot.send_message(
                        tg_id,
                        f"{emoji} <b>Срок годности:</b> {data['name']}\n"
                        f"Осталось {days_left} дн. (до {expiry_str[:10]})\n"
                        f"Количество: {data.get('quantity', '?')}",
                        parse_mode="HTML",
                    )
                    sent += 1
                except Exception as e:
                    logger.error("check_expiry: send error for '%s': %s", data["name"], e)

    return sent
