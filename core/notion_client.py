"""core/notion_client.py — обёртка Notion API + высокоуровневые функции"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List

from notion_client import AsyncClient

logger = logging.getLogger(__name__)
MOSCOW_TZ = timezone(timedelta(hours=3))


# ─── Низкоуровневый клиент ────────────────────────────────────────────────────

class NotionClient:
    def __init__(self, token: str) -> None:
        self._client = AsyncClient(auth=token)

    async def create_page(self, database_id: str, properties: dict) -> str:
        resp = await self._client.pages.create(
            parent={"database_id": database_id},
            properties=properties,
        )
        page_id: str = resp["id"]
        logger.info("notion.create_page db=%s → %s", database_id[:8], page_id[:8])
        return page_id

    async def update_page(self, page_id: str, properties: dict) -> None:
        await self._client.pages.update(page_id=page_id, properties=properties)
        logger.info("notion.update_page %s", page_id[:8])

    async def query_database(
        self,
        database_id: str,
        filters: Optional[dict] = None,
        sorts: Optional[list] = None,
        page_size: int = 20,
    ) -> List[dict]:
        kwargs: dict = {"database_id": database_id, "page_size": page_size}
        if filters:
            kwargs["filter"] = filters
        if sorts:
            kwargs["sorts"] = sorts
        resp = await self._client.databases.query(**kwargs)
        return resp.get("results", [])


# ─── Синглтон ─────────────────────────────────────────────────────────────────

_instance: Optional[NotionClient] = None


def _notion() -> NotionClient:
    global _instance
    if _instance is None:
        from core.config import config
        _instance = NotionClient(config.notion_token)
    return _instance


def get_notion() -> AsyncClient:
    """Возвращает raw AsyncClient для прямых вызовов (архив страниц и т.д.)."""
    return _notion()._client


# ─── Prop helpers ─────────────────────────────────────────────────────────────

def _title(text: str) -> dict:
    return {"title": [{"text": {"content": text or ""}}]}

def _text(text: str) -> dict:
    return {"rich_text": [{"text": {"content": text or ""}}]}

def _number(value: float) -> dict:
    return {"number": value}

def _select(name: str) -> dict:
    return {"select": {"name": name}}

def _status(name: str) -> dict:
    """Для полей типа Status (не Select)."""
    return {"status": {"name": name}}

def _multi_select(names: List[str]) -> dict:
    return {"multi_select": [{"name": n} for n in names]}

def _date(iso: str) -> dict:
    return {"date": {"start": iso}}

def _relation(page_id: str) -> dict:
    return {"relation": [{"id": page_id}]}


# ─── Extract helpers ──────────────────────────────────────────────────────────

def _extract_text(prop: dict) -> str:
    items = prop.get("rich_text") or prop.get("title") or []
    return items[0]["text"]["content"] if items else ""

def _extract_number(prop: dict) -> float:
    return prop.get("number") or 0.0

def _extract_select(prop: dict) -> str:
    sel = prop.get("select")
    return sel["name"] if sel else ""


# ─── Generic ──────────────────────────────────────────────────────────────────

async def page_create(db_id: str, props: dict) -> Optional[str]:
    try:
        return await _notion().create_page(db_id, props)
    except Exception as e:
        logger.error("page_create error: %s", e)
        return None

async def update_page(page_id: str, props: dict) -> None:
    await _notion().update_page(page_id, props)

async def query_pages(
    db_id: str,
    filters: Optional[dict] = None,
    sorts: Optional[list] = None,
    page_size: int = 20,
) -> List[dict]:
    try:
        return await _notion().query_database(db_id, filters, sorts, page_size)
    except Exception as e:
        logger.error("query_pages error: %s", e)
        return []

async def db_query(
    db_id: str,
    filter_obj: Optional[dict] = None,
    sorts: Optional[list] = None,
    page_size: int = 20,
) -> List[dict]:
    """Алиас query_pages с сигнатурой под filter_obj (для deleter.py и tasks.py)."""
    return await query_pages(db_id, filters=filter_obj, sorts=sorts, page_size=page_size)

_db_options_cache: dict = {}  # {db_id:prop_name: [options]}
_db_options_cache: dict = {}  # {db_id:prop_name: [options]}
_db_options_cache: dict = {}

async def get_db_options(db_id: str, prop_name: str) -> List[str]:
    """Возвращает опции с кешем."""
    cache_key = f"{db_id}:{prop_name}"
    if cache_key in _db_options_cache:
        return _db_options_cache[cache_key]
    
    try:
        resp = await _notion()._client.databases.retrieve(database_id=db_id)
        prop = resp.get("properties", {}).get(prop_name, {})
        ptype = prop.get("type", "")
        if ptype in ("select", "multi_select"):
            options = [o["name"] for o in prop.get(ptype, {}).get("options", [])]
            _db_options_cache[cache_key] = options
            return options
    except Exception as e:
        logger.error("get_db_options %s.%s: %s", db_id[:8], prop_name, e)
    return []

import unicodedata


def _remove_emojis(text: str) -> str:
    """Удалить эмодзи и спецсимволы из текста, оставить только слова."""
    return ''.join(
        c for c in text 
        if unicodedata.category(c)[0] != 'S'  # S = Symbol (эмодзи, стрелки и т.д.)
    ).strip()


async def match_select(db_id: str, prop_name: str, value: str) -> str:
    """Матчит value к реальной опции Notion, игнорируя эмодзи.

    "Расходники" → "🕯️ Расходники"
    "расходники" → "🕯️ Расходники"
    "🕯️ Расходники" → "🕯️ Расходники"
    Если не нашёл — возвращает value как есть.
    """
    options = await get_db_options(db_id, prop_name)
    if not options:
        return value
    
    # Нормализуем поиск: удаляем эмодзи из введённого значения
    val_clean = _remove_emojis(value).lower()
    
    if not val_clean:  # Если после удаления эмодзи ничего не осталось
        return value
    
    # 1. Точное совпадение (без эмодзи)
    for opt in options:
        opt_clean = _remove_emojis(opt).lower()
        if opt_clean == val_clean:
            return opt
    
    # 2. Содержится (без эмодзи)
    for opt in options:
        opt_clean = _remove_emojis(opt).lower()
        if val_clean in opt_clean:
            return opt
    
    # 3. Обратное содержание (редко, но на всякий случай)
    for opt in options:
        opt_clean = _remove_emojis(opt).lower()
        if opt_clean in val_clean:
            return opt
    
    # Не нашли - возвращаем как ввёл пользователь
    return value


# ─── Finance ──────────────────────────────────────────────────────────────────

async def finance_add(
    date: str,
    amount: float,
    category: str,
    type_: str,
    source: str = "💳 Карта",
    bot_label: str = "☀️ Nexus",
    description: str = "",
) -> Optional[str]:
    from core.config import config
    db_id = config.nexus.db_finance
    real_category = await match_select(db_id, "Категория", category)
    real_source   = await match_select(db_id, "Источник", source)
    real_type     = await match_select(db_id, "Тип", type_)
    return await page_create(db_id, {
        "Описание":  _title(description),
        "Дата":      _date(date),
        "Сумма":     _number(amount),
        "Категория": _select(real_category),
        "Тип":       _select(real_type),
        "Источник":  _select(real_source),
        "Бот":       _select(bot_label),
    })

async def finance_month(month: str) -> List[dict]:
    """Возвращает все записи за месяц (YYYY-MM)."""
    from core.config import config
    start = f"{month}-01"
    y, m = int(month[:4]), int(month[5:7])
    if m == 12:
        end = f"{y+1}-01-01"
    else:
        end = f"{y}-{m+1:02d}-01"
    filters = {
        "and": [
            {"property": "Дата", "date": {"on_or_after": start}},
            {"property": "Дата", "date": {"before": end}},
        ]
    }
    return await query_pages(config.nexus.db_finance, filters=filters, page_size=100)

async def finance_update(target_type: str, field: str, new_value: str) -> bool:
    """Обновить последнюю финансовую запись (expense или income)."""
    from core.config import config
    db_id = config.nexus.db_finance
    
    # Найти последнюю запись нужного типа (Тип = "💸 Расход" или "💰 Доход")
    type_label = "💸 Расход" if target_type == "expense" else "💰 Доход"
    filters = {"property": "Тип", "select": {"equals": type_label}}
    sorts = [{"property": "Дата", "direction": "descending"}]
    
    pages = await query_pages(db_id, filters=filters, sorts=sorts, page_size=1)
    if not pages:
        return False
    
    page_id = pages[0]["id"]
    
    # Обновить нужное поле
    if field == "source":
        real_source = await match_select(db_id, "Источник", new_value)
        props = {"Источник": _select(real_source)}
    elif field == "category":
        real_category = await match_select(db_id, "Категория", new_value)
        props = {"Категория": _select(real_category)}
    elif field == "amount":
        try:
            amount = float(new_value)
            props = {"Сумма": _number(amount)}
        except ValueError:
            return False
    else:
        return False
    
    await update_page(page_id, props)
    return True


# ─── Tasks ────────────────────────────────────────────────────────────────────

async def task_add(
    title: str,
    category: str = "Другое",
    priority: str = "Средний",
    deadline: Optional[str] = None,
    reminder: str = "",
) -> Optional[str]:
    """Простое добавление задачи без уточнений.
    Для полного флоу с дедлайном/напоминаниями — используй handle_task из tasks.py.
    """
    from core.config import config
    db_id = config.nexus.db_tasks
    real_priority = await match_select(db_id, "Приоритет", priority)
    real_category = await match_select(db_id, "Категория", category)
    props = {
        "Задача":    _title(title),
        "Статус":    _status("Not started"),
        "Приоритет": _select(real_priority),
        "Категория": _select(real_category),
    }
    if deadline:
        props["Дедлайн"] = _date(deadline)
    return await page_create(db_id, props)

async def tasks_active() -> List[dict]:
    """Возвращает все незавершённые задачи."""
    from core.config import config
    return await query_pages(
        config.nexus.db_tasks,
        filters={
            "and": [
                {"property": "Статус", "status": {"does_not_equal": "Done"}},
                {"property": "Статус", "status": {"does_not_equal": "Archived"}},
            ]
        },
        sorts=[{"property": "Приоритет", "direction": "descending"}],
        page_size=50,
    )


async def update_task_status(page_id: str, status: str) -> bool:
    """Обновить статус задачи.
    Статусы: 'Not started', 'In progress', 'Done', 'Archived'
    """
    try:
        await update_page(page_id, {"Статус": _status(status)})
        logger.info("update_task_status: page=%s status=%s", page_id[:8], status)
        return True
    except Exception as e:
        logger.error("update_task_status error: %s", e)
        return False


async def update_task_deadline(page_id: str, new_deadline: str) -> bool:
    """Обновить дедлайн задачи."""
    try:
        await update_page(page_id, {"Дедлайн": _date(new_deadline)})
        logger.info("update_task_deadline: page=%s deadline=%s", page_id[:8], new_deadline)
        return True
    except Exception as e:
        logger.error("update_task_deadline error: %s", e)
        return False


async def update_task_completion_time(page_id: str, completion_time: str) -> bool:
    """Обновить время завершения задачи."""
    try:
        await update_page(page_id, {"Время завершения": _date(completion_time)})
        logger.info("update_task_completion_time: page=%s time=%s", page_id[:8], completion_time)
        return True
    except Exception as e:
        logger.error("update_task_completion_time error: %s", e)
        return False


# ─── Notes ────────────────────────────────────────────────────────────────────

async def note_add(
    text: str,
    tags: Optional[List[str]] = None,
    date: Optional[str] = None,
) -> Optional[str]:
    from core.config import config
    if not date:
        date = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")
    props = {
        "Заголовок": _title(text[:100]),
        "Дата":      _date(date),
    }
    if tags:
        props["Теги"] = _multi_select(tags)
    return await page_create(config.nexus.db_notes, props)

async def notes_search(query: str) -> List[dict]:
    """Поиск заметок по тексту заголовка."""
    from core.config import config
    return await query_pages(
        config.nexus.db_notes,
        filters={"property": "Заголовок", "title": {"contains": query}},
        page_size=10,
    )


# ─── Memory ───────────────────────────────────────────────────────────────────

async def memory_get(key: str) -> Optional[str]:
    """Читает значение из базы Memory по ключу."""
    from core.config import config
    db_id = config.nexus.db_memory
    if not db_id:
        return None
    try:
        results = await query_pages(
            db_id,
            filters={"property": "Ключ", "title": {"equals": key}},
            page_size=1,
        )
        if not results:
            return None
        return _extract_text(results[0].get("properties", {}).get("Значение", {})) or None
    except Exception as e:
        logger.error("memory_get %s: %s", key, e)
        return None

async def memory_set(key: str, value: str, category: str = "") -> None:
    """Записывает или обновляет значение в базе Memory."""
    from core.config import config
    db_id = config.nexus.db_memory
    if not db_id:
        return
    try:
        results = await query_pages(
            db_id,
            filters={"property": "Ключ", "title": {"equals": key}},
            page_size=1,
        )
        props: dict = {
            "Ключ":     _title(key),
            "Значение": _text(value),
        }
        if category:
            props["Категория"] = _select(category)

        if results:
            await _notion().update_page(results[0]["id"], props)
        else:
            await page_create(db_id, props)
    except Exception as e:
        logger.error("memory_set %s=%s: %s", key, value, e)


# ─── Errors ───────────────────────────────────────────────────────────────────

async def log_error(
    message: str,
    error_type: str,
    claude_response: str = "",
    traceback: str = "",
    bot_label: str = "☀️ Nexus",
    error_code: str = "–",
) -> bool:
    from core.config import config
    db_id = config.nexus.db_errors
    if not db_id:
        logger.error("log_error: no db_errors configured. msg=%s type=%s", message[:80], error_type)
        return False
    try:
        await page_create(db_id, {
            "Сообщение":    _title(message[:200]),
            "Дата":         _date(datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")),
            "Тип ошибки":   _select(error_type),
            "Код":          _select(error_code),
            "Ответ Claude": _text(claude_response[:2000]),
            "Трейсбек":     _text(traceback[:2000]),
            "Бот":          _select(bot_label),
        })
        return True
    except Exception as e:
        logger.error("log_error failed: %s", e)
        return False


# ─── Arcana: Clients ──────────────────────────────────────────────────────────

async def client_add(
    name: str,
    contact: str = "",
    request: str = "",
    date: Optional[str] = None,
) -> Optional[str]:
    from core.config import config
    if not date:
        date = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")
    return await page_create(config.arcana.db_clients, {
        "Имя":      _title(name),
        "Контакт":  _text(contact),
        "Запрос":   _text(request),
        "Статус":   _status("🟢 Активный"),
    })

async def client_find(name: str) -> Optional[dict]:
    from core.config import config
    results = await query_pages(
        config.arcana.db_clients,
        filters={"property": "Имя", "title": {"contains": name}},
        page_size=1,
    )
    return results[0] if results else None

async def sessions_by_client(client_id: str) -> List[dict]:
    from core.config import config
    return await query_pages(
        config.arcana.db_sessions,
        filters={"property": "Клиенты", "relation": {"contains": client_id}},
        page_size=50,
    )

async def rituals_by_client(client_id: str) -> List[dict]:
    from core.config import config
    return await query_pages(
        config.arcana.db_rituals,
        filters={"property": "Клиенты", "relation": {"contains": client_id}},
        page_size=50,
    )

async def arcana_all_debts() -> List[dict]:
    """Все сеансы и ритуалы с долгом > 0."""
    from core.config import config
    sessions = await query_pages(config.arcana.db_sessions, page_size=100)
    rituals  = await query_pages(config.arcana.db_rituals, page_size=100)
    result = []
    for item in sessions + rituals:
        props  = item["properties"]
        amount = _extract_number(props.get("Сумма", {}))
        paid   = _extract_number(props.get("Оплачено", {}))
        if amount - paid > 0:
            result.append(item)
    return result


# ─── Arcana: Sessions ─────────────────────────────────────────────────────────

async def session_add(
    date: str,
    spread_type: str = "",
    question: str = "",
    cards: str = "",
    interpretation: str = "",
    amount: float = 0,
    paid: float = 0,
    session_type: str = "Личный",
    client_id: Optional[str] = None,
) -> Optional[str]:
    from core.config import config
    props = {
        "Тема":       _title(question or spread_type or "Сеанс"),
        "Дата":       _date(date[:10]),
        "Тип сеанса": _select("🌟 Личный" if session_type == "Личный" else "🤝 Клиентский"),
        "Сумма":      _number(amount),
        "Оплачено":   _number(paid),
    }
    if cards:
        props["Карты"] = _text(cards)
    if interpretation:
        props["Трактовка"] = _text(interpretation[:2000])
    if client_id:
        props["Клиенты"] = _relation(client_id)
    return await page_create(config.arcana.db_sessions, props)


# ─── Arcana: Rituals ──────────────────────────────────────────────────────────

async def ritual_add(
    name: str,
    date: str,
    ritual_type: str = "Личный",
    consumables: str = "",
    consumables_cost: float = 0,
    duration_min: float = 0,
    offerings: str = "",
    forces: str = "",
    structure: str = "",
    amount: float = 0,
    paid: float = 0,
    client_id: Optional[str] = None,
) -> Optional[str]:
    from core.config import config
    props = {
        "Название":         _title(name),
        "Дата":             _date(date),
        "Тип":              _select("🌟 Личный" if ritual_type == "Личный" else "🤝 Клиентский"),
        "Расходники":       _text(consumables),
        "Сумма подношений": _number(consumables_cost),
        "Время (мин)":      _number(duration_min),
        "Подношения":       _text(offerings),
        "Силы":             _text(forces),
        "Структура":        _text(structure),
        "Цена за ритуал":   _number(amount),
        "Оплачено":         _number(paid),
        "Результат":        _select("⏳ Не проверено"),
    }
    if client_id:
        props["Клиенты"] = _relation(client_id)
    return await page_create(config.arcana.db_rituals, props)

def clear_db_options_cache() -> None:
    """Очистить кеш опций БД (если схема изменилась)."""
    global _db_options_cache
    _db_options_cache.clear()
    logger.info("DB options cache cleared")