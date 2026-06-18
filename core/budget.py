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

    Возвращает {"доходы": [...], "обязательные": [...], "цели": [...],
                "долги": [...], "лимиты": [...]}.
    """
    from core.repos.memory_repo import _repo as _mem_repo

    empty = {"доходы": [], "обязательные": [], "цели": [], "долги": [], "лимиты": []}
    try:
        mems = await _mem_repo.find_by_key_prefixes(
            ["income_", "обязательно_", "лимит_", "цель_"],
            user_notion_id=user_notion_id,
        )
    except Exception as e:
        logger.error("load_budget_data: %s", e)
        return empty

    result: Dict[str, list] = {"доходы": [], "обязательные": [], "цели": [],
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

    free = доход − обязательные − лимиты − цели.saving − долги.monthly_payment
    day_limit = max(0, free // дни_до_пэйдея)
    Возвращает 0 если план не задан или доход отсутствует.
    """
    try:
        budget = await load_budget_data(user_notion_id)
        total_income = sum(d["amount"] for d in budget["доходы"])
        if total_income <= 0:
            return 0
        total_obligatory = sum(d["amount"] for d in budget["обязательные"])
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
