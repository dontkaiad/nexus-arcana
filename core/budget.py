"""core/budget.py — парсинг бюджета из Памяти.

Общий слой для Nexus-хендлера /budget и Mini App /api/finance.
Держит в одном месте: regex-парсеры, константы, публичные функции загрузки.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Dict, List

logger = logging.getLogger(__name__)


# ── Key prefix ↔ Notion category ─────────────────────────────────────────────

BUDGET_KEY_TO_CATEGORY: Dict[str, str] = {
    "income_": "📥 Доход",
    "обязательно_": "🔒 Обязательные",
    "лимит_": "💰 Лимит",
    "цель_": "🎯 Цели",
    "долг_": "📋 Долги",
}
BUDGET_ALL_CATEGORIES: List[str] = list(BUDGET_KEY_TO_CATEGORY.values())


# ── Display mapping для лимитов ──────────────────────────────────────────────

LIMIT_DISPLAY: Dict[str, str] = {
    "привычки": "🚬 Привычки",
    "продукты": "🍜 Продукты",
    "кафе": "🍱 Кафе/Доставка",
    "транспорт": "🚕 Транспорт",
    "бьюти": "💅 Бьюти",
    "гардероб": "👗 Гардероб",
    "здоровье": "🏥 Здоровье",
    "хобби": "📚 Хобби/Учеба",
    "импульсивные": "🎲 Импульсивные",
    "импульсивный": "🎲 Импульсивные",
    "подушка": "🛡️ Подушка",
    "расходники": "🕯️ Расходники",
}


# ── Regex ────────────────────────────────────────────────────────────────────

LIMIT_AMOUNT_RE = re.compile(r'(\d[\d\s]*(?:[.,]\d+)?)\s*[₽р]')
LIMIT_FACT_RE = re.compile(
    r'лимит[:\s]+([^—\-\d]+?)\s*[—\-]\s*(\d[\d\s]*(?:[.,]\d+)?)\s*[₽р]',
    re.IGNORECASE | re.UNICODE,
)
INCOME_RE = re.compile(
    r'доход:\s*(.+?)\s*[—\-]\s*(\d[\d\s]*(?:[.,]\d+)?)\s*[₽р]',
    re.IGNORECASE,
)
OBLIGATORY_RE = re.compile(
    r'обязательно:\s*(.+?)\s*[—\-]\s*(\d[\d\s]*(?:[.,]\d+)?)\s*[₽р]',
    re.IGNORECASE,
)
GOAL_RE = re.compile(
    r'цель:\s*(.+?)\s*[—\-]\s*(\d[\d\s]*(?:[.,]\d+)?)\s*[₽р]'
    r'(?:.*?откладываю\s*(\d[\d\s]*(?:[.,]\d+)?)\s*[₽р])?',
    re.IGNORECASE,
)
DEBT_RE = re.compile(
    r'долг:\s*(.+?)\s*[—\-]\s*(\d[\d\s]*(?:[.,]\d+)?)\s*[₽р]'
    r'(?:.*?дедлайн:\s*([^·]+))?'
    r'(?:.*?стратегия:\s*([^·]+))?'
    r'(?:.*?платёж:\s*(\d[\d\s]*(?:[.,]\d+)?))?'
    r'\s*$',
    re.IGNORECASE,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def parse_amount(s: str) -> float:
    """'15 000' / '15,5' / '15.5' → float. Без ₽ и пробелов."""
    return float(s.replace(' ', '').replace(',', '.'))


def cat_link(cat: str) -> str:
    """'🚬 Привычки' → 'привычки', '🍱 Кафе/Доставка' → 'кафе'."""
    name = re.sub(r'^[^\w\u0400-\u04FF]+', '', cat, flags=re.UNICODE).strip()
    return name.split('/')[0].strip().lower()


def display_limit_name(raw_name: str) -> str:
    """'привычки' / 'лимит_привычки' → '🚬 Привычки'. Fallback — raw_name."""
    key = raw_name.lower().replace("лимит_", "").strip()
    return LIMIT_DISPLAY.get(key, raw_name)


# ── Public API ───────────────────────────────────────────────────────────────

async def get_limits(mem_db: str = "") -> Dict[str, float]:
    """Все лимиты из Памяти. Возвращает {cat_link: amount}.

    Стратегия: фильтр по Категория="💰 Лимит", если упал или пусто —
    забираем все записи и ищем те, у которых текст начинается с "лимит:".
    """
    from core.notion_client import db_query
    from core.config import config
    db = mem_db or config.nexus.db_memory
    if not db:
        logger.warning("get_limits: no memory db configured")
        return {}
    limits: Dict[str, float] = {}
    pages: list = []
    try:
        pages = await db_query(db, filter_obj={
            "property": "Категория", "select": {"equals": "💰 Лимит"}
        }, page_size=100)
        logger.info("get_limits: category filter → %d pages", len(pages))
    except Exception as e:
        logger.warning("get_limits: category filter failed (%s), trying text search", e)

    if not pages:
        try:
            all_pages = await db_query(db, page_size=200)
            pages = [
                p for p in all_pages
                if (p["properties"].get("Текст", {}).get("title") or [{}])[0]
                   .get("plain_text", "").lower().startswith("лимит")
            ]
            logger.info("get_limits: text fallback → %d limit pages from %d total",
                        len(pages), len(all_pages))
        except Exception as e2:
            logger.error("get_limits: fallback failed: %s", e2, exc_info=True)
            return {}

    for p in pages:
        props = p["properties"]
        fact_parts = props.get("Текст", {}).get("title", [])
        fact = fact_parts[0]["plain_text"] if fact_parts else ""

        связь_parts = props.get("Связь", {}).get("rich_text", [])
        связь = связь_parts[0]["plain_text"].strip().lower() if связь_parts else ""

        fact_match = LIMIT_FACT_RE.search(fact)
        if fact_match and not связь:
            связь = cat_link(fact_match.group(1).strip())

        amount_match = LIMIT_AMOUNT_RE.search(fact)
        logger.info("get_limits: fact=%r связь=%r amount=%r",
                    fact, связь, amount_match.group(0) if amount_match else None)

        if связь and amount_match:
            limits[связь] = float(amount_match.group(1).replace(' ', '').replace(',', '.'))
        else:
            logger.warning("get_limits: skip — связь=%r fact=%r", связь, fact)

    logger.info("get_limits: result=%s", limits)
    return limits


async def load_budget_data(user_notion_id: str = "") -> Dict[str, list]:
    """Все бюджетные записи Памяти (💰 Лимит / 🎯 Цели / 📋 Долги / 🔒 Обязательные / 📥 Доход).

    Возвращает {"доходы": [...], "обязательные": [...], "цели": [...],
                "долги": [...], "лимиты": [...]}.
    Ключи фильтруются по prefix (income_, обязательно_, лимит_, цель_, долг_),
    Актуально == true, опционально — по owner user_notion_id.
    """
    from core.notion_client import db_query
    from core.config import config

    mem_db = os.environ.get("NOTION_DB_MEMORY") or config.nexus.db_memory
    empty = {"доходы": [], "обязательные": [], "цели": [], "долги": [], "лимиты": []}
    if not mem_db:
        return empty

    key_filter = {"or": [
        {"property": "Ключ", "rich_text": {"starts_with": "income_"}},
        {"property": "Ключ", "rich_text": {"starts_with": "обязательно_"}},
        {"property": "Ключ", "rich_text": {"starts_with": "лимит_"}},
        {"property": "Ключ", "rich_text": {"starts_with": "цель_"}},
        {"property": "Ключ", "rich_text": {"starts_with": "долг_"}},
    ]}
    conditions = [key_filter, {"property": "Актуально", "checkbox": {"equals": True}}]
    if user_notion_id:
        conditions.append({"property": "🪪 Пользователи",
                           "relation": {"contains": user_notion_id}})
    filt = {"and": conditions}
    try:
        pages = await db_query(mem_db, filter_obj=filt, page_size=200)
    except Exception as e:
        logger.error("load_budget_data: %s", e)
        return empty

    result: Dict[str, list] = {"доходы": [], "обязательные": [], "цели": [],
                               "долги": [], "лимиты": []}
    for p in pages:
        props = p["properties"]
        fact_parts = props.get("Текст", {}).get("title", [])
        fact = fact_parts[0]["plain_text"] if fact_parts else ""
        key_parts = props.get("Ключ", {}).get("rich_text", [])
        key = key_parts[0]["plain_text"].strip().lower() if key_parts else ""
        active = props.get("Актуально", {}).get("checkbox", True)
        if not active:
            continue

        if key.startswith("income_"):
            m = INCOME_RE.search(fact)
            if m:
                amt = parse_amount(m.group(2))
                if amt > 0:
                    result["доходы"].append({"name": m.group(1).strip(), "amount": amt})
        elif key.startswith("обязательно_"):
            m = OBLIGATORY_RE.search(fact)
            if m:
                amt = parse_amount(m.group(2))
                if amt > 0:
                    result["обязательные"].append({"name": m.group(1).strip(), "amount": amt})
        elif key.startswith("цель_"):
            m = GOAL_RE.search(fact)
            if m:
                saving = parse_amount(m.group(3)) if m.group(3) else 0
                result["цели"].append({
                    "name": m.group(1).strip(),
                    "target": parse_amount(m.group(2)),
                    "saving": saving,
                    "key": key,
                    "fact": fact,
                })
        elif key.startswith("долг_"):
            m = DEBT_RE.search(fact)
            if m:
                strategy = (m.group(4) or "").strip()
                mp_raw = (m.group(5) or "").strip()
                monthly_payment = parse_amount(mp_raw) if mp_raw else 0
                result["долги"].append({
                    "name": m.group(1).strip(),
                    "amount": parse_amount(m.group(2)),
                    "deadline": (m.group(3) or "").strip(),
                    "strategy": strategy,
                    "monthly_payment": monthly_payment,
                    "fact": fact,
                    "key": key,
                })
        elif key.startswith("лимит_"):
            amount_m = LIMIT_AMOUNT_RE.search(fact)
            if amount_m:
                связь_parts = props.get("Связь", {}).get("rich_text", [])
                связь = связь_parts[0]["plain_text"].strip() if связь_parts else ""
                result["лимиты"].append({
                    "name": связь or key,
                    "amount": parse_amount(amount_m.group(1)),
                })

    # Дедупликация лимитов по display-имени
    seen_limit_names: dict = {}
    for lim in result["лимиты"]:
        display = display_limit_name(lim["name"])
        if display not in seen_limit_names:
            seen_limit_names[display] = lim
        elif lim["amount"] > seen_limit_names[display]["amount"]:
            seen_limit_names[display] = lim
    result["лимиты"] = list(seen_limit_names.values())

    return result
