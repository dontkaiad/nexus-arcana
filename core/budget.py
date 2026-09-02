"""core/budget.py — парсинг бюджета из Памяти.

Общий слой для Nexus-хендлера /budget и Mini App /api/finance.
Держит в одном месте: regex-парсеры, константы, публичные функции загрузки.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone as _tz
from typing import Dict, List

logger = logging.getLogger(__name__)


# ── Key prefix ↔ Notion category ─────────────────────────────────────────────

BUDGET_KEY_TO_CATEGORY: Dict[str, str] = {
    "income_": "📥 Доход",
    "постоянно_": "🔒 Постоянные",
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
PERMANENT_RE = re.compile(
    r'постоянно:\s*(.+?)\s*[—\-]\s*(\d[\d\s]*(?:[.,]\d+)?)\s*[₽р]',
    re.IGNORECASE,
)
GOAL_RE = re.compile(
    r'цель:\s*(.+?)\s*[—\-]\s*(\d[\d\s]*(?:[.,]\d+)?)\s*[₽р]'
    r'(?:.*?откладываю\s*(\d[\d\s]*(?:[.,]\d+)?)\s*[₽р])?',
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
    """Все лимиты из Памяти (PG). Возвращает {cat_link: amount}."""
    from core.repos.memory_repo import _repo as _mem_repo
    limits: Dict[str, float] = {}
    try:
        mems = await _mem_repo.find_by_category("💰 Лимит", is_current=True, page_size=100)
        logger.info("get_limits: found %d limit memories", len(mems))
    except Exception as e:
        logger.error("get_limits: %s", e)
        return {}

    for m in mems:
        fact = m.fact or ""
        связь = (m.related_to or "").strip().lower()

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
    """Все бюджетные записи Памяти (PG).

    Возвращает {"доходы": [...], "постоянные": [...], "цели": [...],
                "долги": [...], "лимиты": [...]}.
    """
    from core.repos.memory_repo import _repo as _mem_repo

    empty = {"доходы": [], "постоянные": [], "цели": [], "долги": [], "лимиты": []}
    try:
        mems = await _mem_repo.find_by_key_prefixes(
            ["income_", "постоянно_", "лимит_", "цель_"],
            user_notion_id=user_notion_id,
        )
    except Exception as e:
        logger.error("load_budget_data: %s", e)
        return empty

    result: Dict[str, list] = {"доходы": [], "постоянные": [], "цели": [],
                               "долги": [], "лимиты": []}
    for m in mems:
        fact = m.fact or ""
        key = (m.key or "").strip().lower()
        if not m.is_current:
            continue

        if key.startswith("income_"):
            m = INCOME_RE.search(fact)
            if m:
                amt = parse_amount(m.group(2))
                if amt > 0:
                    result["доходы"].append({"name": m.group(1).strip(), "amount": amt})
        elif key.startswith("постоянно_"):
            m = PERMANENT_RE.search(fact)
            if m:
                amt = parse_amount(m.group(2))
                if amt > 0:
                    result["постоянные"].append({"name": m.group(1).strip(), "amount": amt})
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
        elif key.startswith("лимит_"):
            amount_m = LIMIT_AMOUNT_RE.search(fact)
            if amount_m:
                связь = (m.related_to or "").strip()
                result["лимиты"].append({
                    "name": связь or key,
                    "amount": parse_amount(amount_m.group(1)),
                })

    # Долги — читаем из таблицы debts (not Memory)
    try:
        from core.repos.pg_debts_repo import _repo as _debt_repo
        active_debts = await _debt_repo.list_active(user_notion_id, kind="i_owe")
        for d in active_debts:
            result["долги"].append({
                "name": d.name,
                "amount": d.amount,
                "deadline": d.deadline,
                "strategy": d.strategy,
                "monthly_payment": d.monthly_payment,
                "fact": "",
                "key": "",
            })
    except Exception as e:
        logger.error("load_budget_data debts: %s", e)

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


# ── Детерминированный расчёт лимитов ─────────────────────────────────────────
#
# Вся арифметика распределения переменных лимитов живёт здесь, а не в
# LLM-инструкциях. Sonnet больше не считает ни одной цифры лимита — только
# текстовые пояснения вокруг готовых чисел из compute_limits().

CAT_TRANSPORT = "🚕 Транспорт"
CAT_IMPULSE = "🎲 Импульсивные"
CAT_PRODUCTS = "🍜 Продукты"
CAT_HABITS = "🚬 Привычки"

# Железные категории — всегда, никогда не режутся (пока discretionary их покрывает).
IRON_TRANSPORT = 1500
IRON_IMPULSE = 1000
IRON_TOTAL = IRON_TRANSPORT + IRON_IMPULSE  # 2500

PRODUCTS_TARGET = 10000
HABITS_CEILING = 13000
_PRIORITY_FLOOR = PRODUCTS_TARGET + HABITS_CEILING  # 23000

# Приоритет распределения остатка: каждая категория забирает 50% того, что
# осталось после предыдущей; последняя в списке — весь остаток (иначе из-за
# округления виснет копейка и сумма не сходится с discretionary).
PRIORITY_CHAIN = [
    "🍱 Кафе/Доставка",
    "💅 Бьюти",
    "🏥 Здоровье",
    "👗 Гардероб",
    "📚 Хобби/Учеба",
]

# Все категории, которые compute_limits() всегда возвращает (0, если не профинансированы).
LIMIT_CATEGORIES = [CAT_TRANSPORT, CAT_IMPULSE, CAT_PRODUCTS, CAT_HABITS] + PRIORITY_CHAIN


def compute_limits(distributable_pool: float, total_debt_payment: float) -> Dict[str, int]:
    """Детерминированное распределение переменных лимитов. Без LLM.

    discretionary = distributable_pool − total_debt_payment.

    Возвращает {категория: рубли(int)} по всем LIMIT_CATEGORIES. Сумма всех
    значений ТОЧНО равна discretionary (округление round() до рубля, последняя
    категория цепочки приоритета забирает остаток без деления пополам —
    поэтому копейки от округления не теряются и не создаются).

    Ветки:
      • discretionary ≤ 0        → все категории 0 (в минус не уходим).
      • discretionary < 2500     → железные транспорт/импульсивные урезаны
                                    пропорционально их долям в IRON_TOTAL,
                                    остальное 0 (крайний случай).
      • pool_after_iron ≥ 23000  → продукты=10000, привычки≤13000, остаток
                                    по PRIORITY_CHAIN делением пополам.
      • иначе                    → продукты/привычки делят pool_after_iron
                                    пополам, цепочка приоритета = 0.
    """
    limits: Dict[str, int] = {cat: 0 for cat in LIMIT_CATEGORIES}

    discretionary = round(distributable_pool - total_debt_payment)
    if discretionary <= 0:
        return limits

    if discretionary < IRON_TOTAL:
        # Транспорт+импульсивные не покрываются — режем их пропорционально,
        # остаток цепочки/продуктов/привычек = 0. Сумма = discretionary ровно
        # (транспорт берёт свою долю, импульсивные — весь остаток).
        transport = round(discretionary * IRON_TRANSPORT / IRON_TOTAL)
        limits[CAT_TRANSPORT] = transport
        limits[CAT_IMPULSE] = discretionary - transport
        return limits

    limits[CAT_TRANSPORT] = IRON_TRANSPORT
    limits[CAT_IMPULSE] = IRON_IMPULSE
    pool_after_iron = discretionary - IRON_TOTAL

    if pool_after_iron >= _PRIORITY_FLOOR:
        limits[CAT_PRODUCTS] = PRODUCTS_TARGET
        limits[CAT_HABITS] = min(HABITS_CEILING, pool_after_iron - PRODUCTS_TARGET)
        remaining = pool_after_iron - limits[CAT_PRODUCTS] - limits[CAT_HABITS]
        last_idx = len(PRIORITY_CHAIN) - 1
        for i, cat in enumerate(PRIORITY_CHAIN):
            if i == last_idx:
                limits[cat] = remaining  # весь остаток, без деления — сумма сходится
            else:
                amount = round(remaining / 2)
                limits[cat] = amount
                remaining -= amount
    else:
        products = round(pool_after_iron / 2)
        limits[CAT_PRODUCTS] = products
        limits[CAT_HABITS] = pool_after_iron - products
        # цепочка приоритета остаётся 0

    return limits


_MOSCOW_TZ = _tz(timedelta(hours=3))


async def _budget_payday() -> int:
    """День пэйдея из Памяти (PG). Default 1."""
    try:
        from core.repos.pg_memory_repo import PgMemoryRepo
        mems = await PgMemoryRepo().find_by_exact_key("budget_payday")
        stored = mems[0].fact if mems else None
        if stored:
            m = re.search(r"(\d+)", stored)
            if m:
                return int(m.group(1))
    except Exception:
        pass
    return 1


def _period_days_remaining(payday: int) -> int:
    """Дней до конца бюджетного периода (не считая сегодня) → делитель."""
    now = datetime.now(_MOSCOW_TZ)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if now.day >= payday:
        next_month = now.month + 1 if now.month < 12 else 1
        next_year = now.year if now.month < 12 else now.year + 1
        period_end = datetime(next_year, next_month, payday, tzinfo=_MOSCOW_TZ) - timedelta(days=1)
    else:
        period_end = now.replace(day=payday, hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    return max(1, (period_end - today_start).days)


async def budget_day_limit_from_plan(user_notion_id: str) -> int:
    """Дневной лимит из сохранённого плана в Памяти.

    free = доход − постоянные − лимиты − цели.saving − долги.monthly_payment
    day_limit = max(0, free // дни_до_пэйдея)
    Возвращает 0 если план не задан или доход отсутствует.
    """
    try:
        budget = await load_budget_data(user_notion_id)
        total_income = sum(d["amount"] for d in budget["доходы"])
        if total_income <= 0:
            return 0
        total_obligatory = sum(d["amount"] for d in budget["постоянные"])
        total_limits = sum(d["amount"] for d in budget["лимиты"])
        total_goals_saving = sum(d.get("saving", 0) for d in budget["цели"])
        total_debt_monthly = sum(
            d.get("monthly_payment") or 0 for d in budget["долги"]
            if (d.get("monthly_payment") or 0) > 0
        )
        free = (total_income - total_obligatory - total_limits
                - total_goals_saving - total_debt_monthly)
        payday = await _budget_payday()
        days = _period_days_remaining(payday)
        return max(0, int(free / days))
    except Exception:
        logger.error("budget_day_limit_from_plan: unexpected error", exc_info=True)
        return 0
