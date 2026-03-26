"""nexus/handlers/finance.py"""
from __future__ import annotations

import base64
import calendar
import json
import logging
import os
import random
import re
import sqlite3 as _sqlite3
import time as _time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Set, Tuple

from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram import Router, F
from core.claude_client import ask_claude, ask_claude_vision
from nexus.handlers.utils import react
from core.notion_client import finance_month, log_error, page_create, update_page, create_report_page, _title, _number, _select, _date, _text

logger = logging.getLogger("nexus.finance")
MOSCOW_TZ = timezone(timedelta(hours=3))

router = Router()

# Маппинг ключ → категория Notion для бюджетных записей
_BUDGET_KEY_TO_CATEGORY = {
    "income_": "📥 Доход",
    "обязательно_": "🔒 Обязательные",
    "лимит_": "💰 Лимит",
    "цель_": "🎯 Цели",
    "долг_": "📋 Долги",
}
_BUDGET_ALL_CATEGORIES = list(_BUDGET_KEY_TO_CATEGORY.values())

_LIMIT_AMOUNT_RE = re.compile(r'(\d[\d\s]*(?:[.,]\d+)?)\s*[₽р]')
# Парсит "лимит: 🍱 Кафе/Доставка — 9000₽/мес" → group(1)=категория, group(2)=сумма
_LIMIT_FACT_RE = re.compile(
    r'лимит[:\s]+([^—\-\d]+?)\s*[—\-]\s*(\d[\d\s]*(?:[.,]\d+)?)\s*[₽р]',
    re.IGNORECASE | re.UNICODE,
)
_INCOME_MARKERS_RE = re.compile(
    r'\b(получила|получил|заработала|заработал|зарплата|доход|перевели|перевёл|перевел'
    r'|вернули|вернул|пришло|пришла|поступил[аио]?|аванс)\b',
    re.IGNORECASE,
)
_BARTER_MARKERS_RE = re.compile(r'\b(бартер|обмен|в\s+обмен)\b', re.IGNORECASE)

# ── Бюджет: предупреждения по привычкам ──────────────────────────────────────
HABIT_WARNINGS = [
    "💡 17 500₽/мес на привычки = 210 000₽/год. Это Samsung Flip за полгода.",
    "💡 Пачка сигарет в день = 8 500₽/мес. За год — ноутбук.",
    "💡 Монстр каждый день = 6 600₽/мес = 79 000₽/год.",
    "💡 Кола + монстр = 11 000₽/мес. Это больше чем коты.",
    "💡 Если сократить привычки на 30% — через полгода будет подушка.",
    "💡 210к/год на привычки — за 3 года это первый взнос на квартиру.",
    "💡 Одна пачка в два дня вместо одной = 4 250₽ экономии/мес.",
]

# ── Бюджет: regex для парсинга записей из памяти ─────────────────────────────
_INCOME_RE = re.compile(
    r'доход:\s*(.+?)\s*[—\-]\s*(\d[\d\s]*(?:[.,]\d+)?)\s*[₽р]',
    re.IGNORECASE,
)
_OBLIGATORY_RE = re.compile(
    r'обязательно:\s*(.+?)\s*[—\-]\s*(\d[\d\s]*(?:[.,]\d+)?)\s*[₽р]',
    re.IGNORECASE,
)
_GOAL_RE = re.compile(
    r'цель:\s*(.+?)\s*[—\-]\s*(\d[\d\s]*(?:[.,]\d+)?)\s*[₽р]'
    r'(?:.*?откладываю\s*(\d[\d\s]*(?:[.,]\d+)?)\s*[₽р])?',
    re.IGNORECASE,
)
_DEBT_RE = re.compile(
    r'долг:\s*(.+?)\s*[—\-]\s*(\d[\d\s]*(?:[.,]\d+)?)\s*[₽р]'
    r'(?:.*?дедлайн:\s*([^·]+))?'
    r'(?:.*?стратегия:\s*([^·]+))?'
    r'(?:.*?платёж:\s*(\d[\d\s]*(?:[.,]\d+)?))?'
    r'\s*$',
    re.IGNORECASE,
)

# Трекинг: не предлагать лимит повторно в одной сессии
_limit_suggested: Set[Tuple[int, str]] = set()

_RU_MONTHS = {
    1: "январь", 2: "февраль", 3: "март", 4: "апрель",
    5: "май", 6: "июнь", 7: "июль", 8: "август",
    9: "сентябрь", 10: "октябрь", 11: "ноябрь", 12: "декабрь",
}


def _cat_link(cat: str) -> str:
    """'🚬 Привычки' → 'привычки', '🍱 Кафе/Доставка' → 'кафе'"""
    name = re.sub(r'^[^\w\u0400-\u04FF]+', '', cat, flags=re.UNICODE).strip()
    return name.split('/')[0].strip().lower()


async def _get_limits(mem_db: str) -> Dict[str, float]:
    """Загрузить все лимиты из памяти. Возвращает {cat_link: amount}.

    Стратегия: сначала фильтр по Категория="💰 Лимит", если упал —
    забираем все записи и ищем те, у которых текст начинается с "лимит:".
    Ключ берётся из поля Связь или парсится из факт-текста через _LIMIT_FACT_RE.
    """
    from core.notion_client import db_query
    from core.config import config
    db = mem_db or config.nexus.db_memory
    if not db:
        logger.warning("_get_limits: no memory db configured")
        return {}
    limits: Dict[str, float] = {}
    pages: list = []
    try:
        pages = await db_query(db, filter_obj={
            "property": "Категория", "select": {"equals": "💰 Лимит"}
        }, page_size=100)
        logger.info("_get_limits: category filter → %d pages", len(pages))
    except Exception as e:
        logger.warning("_get_limits: category filter failed (%s), trying text search", e)

    # Если фильтр вернул 0 или упал — берём все страницы и фильтруем сами
    if not pages:
        try:
            all_pages = await db_query(db, page_size=200)
            pages = [
                p for p in all_pages
                if (p["properties"].get("Текст", {}).get("title") or [{}])[0]
                   .get("plain_text", "").lower().startswith("лимит")
            ]
            logger.info("_get_limits: text fallback → %d limit pages from %d total", len(pages), len(all_pages))
        except Exception as e2:
            logger.error("_get_limits: fallback failed: %s", e2, exc_info=True)
            return {}

    for p in pages:
        props = p["properties"]
        fact_parts = props.get("Текст", {}).get("title", [])
        fact = fact_parts[0]["plain_text"] if fact_parts else ""

        # Стратегия 1: поле Связь
        связь_parts = props.get("Связь", {}).get("rich_text", [])
        связь = связь_parts[0]["plain_text"].strip().lower() if связь_parts else ""

        # Стратегия 2: парсим категорию из текста "лимит: 🍱 Кафе/Доставка — 9000₽/мес"
        fact_match = _LIMIT_FACT_RE.search(fact)
        if fact_match and not связь:
            связь = _cat_link(fact_match.group(1).strip())

        # Сумма из текста
        amount_match = _LIMIT_AMOUNT_RE.search(fact)
        logger.info("_get_limits: fact=%r связь=%r amount=%r",
                    fact, связь, amount_match.group(0) if amount_match else None)

        if связь and amount_match:
            limits[связь] = float(amount_match.group(1).replace(' ', '').replace(',', '.'))
        else:
            logger.warning("_get_limits: skip — связь=%r fact=%r", связь, fact)

    logger.info("_get_limits: result=%s", limits)
    return limits


def _parse_user_amount(text: str) -> Optional[int]:
    """Парсит сумму из пользовательского ввода.

    Поддерживает: '20к', '31000', '15 000₽', '20к съем', '15-20к' (среднее), '500р'.
    """
    # Диапазон: "15-20к" → среднее
    m = re.search(r'(\d+)\s*[-–]\s*(\d+)\s*к\b', text, re.IGNORECASE)
    if m:
        try:
            return int((int(m.group(1)) + int(m.group(2))) / 2 * 1000)
        except ValueError:
            pass
    # Число + "к": "20к", "5к"
    m = re.search(r'(\d[\d\s]*(?:[.,]\d+)?)\s*к\b', text, re.IGNORECASE)
    if m:
        raw = m.group(1).replace(" ", "").replace(",", ".")
        try:
            return int(float(raw) * 1000)
        except ValueError:
            return None
    # Обычное число: "31000", "15 000₽", "500р"
    m = re.search(r'(\d[\d\s]*(?:[.,]\d+)?)\s*[₽р]?\b', text)
    if m:
        raw = m.group(1).replace(" ", "").replace(",", ".")
        try:
            return int(float(raw))
        except ValueError:
            return None
    return None


def _parse_amount(s: str) -> float:
    """Парсит строку суммы: убирает пробелы, заменяет запятую."""
    return float(s.replace(' ', '').replace(',', '.'))


async def _load_budget_data(user_notion_id: str = "") -> Dict[str, list]:
    """Загрузить все бюджетные записи из Памяти (💰 Лимит).

    Возвращает {"обязательные": [...], "цели": [...], "долги": [...], "лимиты": [...]}.
    Каждый элемент — dict с name, amount, (saving, deadline и т.д.).
    """
    from core.notion_client import db_query
    mem_db = os.environ.get("NOTION_DB_MEMORY")
    if not mem_db:
        return {"обязательные": [], "цели": [], "долги": [], "лимиты": []}

    # Ищем по ключу (prefix-based) — не зависит от существования категорий в Notion
    key_filter = {"or": [
        {"property": "Ключ", "rich_text": {"starts_with": "income_"}},
        {"property": "Ключ", "rich_text": {"starts_with": "обязательно_"}},
        {"property": "Ключ", "rich_text": {"starts_with": "лимит_"}},
        {"property": "Ключ", "rich_text": {"starts_with": "цель_"}},
        {"property": "Ключ", "rich_text": {"starts_with": "долг_"}},
    ]}
    conditions = [key_filter, {"property": "Актуально", "checkbox": {"equals": True}}]
    if user_notion_id:
        conditions.append({"property": "🪪 Пользователи", "relation": {"contains": user_notion_id}})
    filt = {"and": conditions}
    try:
        pages = await db_query(mem_db, filter_obj=filt, page_size=200)
    except Exception as e:
        logger.error("_load_budget_data: %s", e)
        return {"обязательные": [], "цели": [], "долги": [], "лимиты": []}

    result: Dict[str, list] = {"доходы": [], "обязательные": [], "цели": [], "долги": [], "лимиты": []}
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
            m = _INCOME_RE.search(fact)
            if m:
                amt = _parse_amount(m.group(2))
                if amt > 0:
                    result["доходы"].append({"name": m.group(1).strip(), "amount": amt})
        elif key.startswith("обязательно_"):
            m = _OBLIGATORY_RE.search(fact)
            if m:
                amt = _parse_amount(m.group(2))
                if amt > 0:  # 0₽ = деактивировано
                    result["обязательные"].append({"name": m.group(1).strip(), "amount": amt})
        elif key.startswith("цель_"):
            m = _GOAL_RE.search(fact)
            if m:
                saving = _parse_amount(m.group(3)) if m.group(3) else 0
                result["цели"].append({"name": m.group(1).strip(), "target": _parse_amount(m.group(2)), "saving": saving})
        elif key.startswith("долг_"):
            m = _DEBT_RE.search(fact)
            if m:
                strategy = (m.group(4) or "").strip()
                mp_raw = (m.group(5) or "").strip()
                monthly_payment = _parse_amount(mp_raw) if mp_raw else 0
                result["долги"].append({
                    "name": m.group(1).strip(),
                    "amount": _parse_amount(m.group(2)),
                    "deadline": (m.group(3) or "").strip(),
                    "strategy": strategy,
                    "monthly_payment": monthly_payment,
                    "fact": fact,
                })
        elif key.startswith("лимит_"):
            amount_m = _LIMIT_AMOUNT_RE.search(fact)
            if amount_m:
                связь_parts = props.get("Связь", {}).get("rich_text", [])
                связь = связь_parts[0]["plain_text"].strip() if связь_parts else ""
                result["лимиты"].append({"name": связь or key, "amount": _parse_amount(amount_m.group(1))})

    return result


async def _calc_free_remaining(user_notion_id: str = "") -> Optional[Tuple[float, int]]:
    """Возвращает (остаток_свободных, дней_до_конца_месяца) или None."""
    from core.config import config
    from core.notion_client import db_query

    mem_db = os.environ.get("NOTION_DB_MEMORY")
    if not mem_db:
        return None

    budget = await _load_budget_data(user_notion_id)
    obligatory_total = sum(o["amount"] for o in budget["обязательные"])
    savings_total = sum(g["saving"] for g in budget["цели"])

    now = datetime.now(MOSCOW_TZ)
    month_str = now.strftime("%Y-%m")
    month_start = f"{month_str}-01"
    today_str = now.strftime("%Y-%m-%d")
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    days_remaining = days_in_month - now.day

    # Доходы за месяц
    try:
        income_records = await db_query(config.nexus.db_finance, filter_obj={"and": [
            {"property": "Тип", "select": {"equals": "💰 Доход"}},
            {"property": "Дата", "date": {"on_or_after": month_start}},
            {"property": "Дата", "date": {"on_or_before": today_str}},
        ]}, page_size=200)
        total_income = sum((p["properties"].get("Сумма", {}).get("number") or 0) for p in income_records)
    except Exception:
        total_income = 0

    if total_income == 0:
        return None  # нет дохода — нечего считать

    # Расходы за месяц
    try:
        expense_records = await db_query(config.nexus.db_finance, filter_obj={"and": [
            {"property": "Тип", "select": {"equals": "💸 Расход"}},
            {"property": "Дата", "date": {"on_or_after": month_start}},
            {"property": "Дата", "date": {"on_or_before": today_str}},
        ]}, page_size=500)
        total_expenses = sum((p["properties"].get("Сумма", {}).get("number") or 0) for p in expense_records)
    except Exception:
        total_expenses = 0

    # Свободные = доход - все расходы - накопления
    # obligatory_total НЕ вычитаем отдельно — обязательные платежи
    # уже записаны в расходы когда оплачены
    free_left = total_income - total_expenses - savings_total
    return (free_left, days_remaining)


_LIMIT_DISPLAY = {
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


def _display_limit_name(raw_name: str) -> str:
    """'привычки' -> '🚬 Привычки', 'лимит_привычки' -> '🚬 Привычки'."""
    key = raw_name.lower().replace("лимит_", "").strip()
    return _LIMIT_DISPLAY.get(key, raw_name)


async def build_budget_message(user_notion_id: str = "") -> Optional[str]:
    """Формирует полное сообщение /budget из сохранённых данных. НЕ вызывает Sonnet."""
    from core.config import config
    from core.notion_client import db_query

    budget = await _load_budget_data(user_notion_id)
    has_data = budget.get("обязательные") or budget.get("лимиты")
    if not has_data:
        return None

    now = datetime.now(MOSCOW_TZ)
    payday = await _get_payday()
    period_start, period_end = _period_bounds(payday)
    today_str = now.strftime("%Y-%m-%d")
    try:
        start_dt = datetime.strptime(period_start, "%Y-%m-%d")
        end_dt = datetime.strptime(period_end, "%Y-%m-%d")
        days_in_period = max(1, (end_dt - start_dt).days)
        day_of_period = max(1, (now.replace(hour=0, minute=0, second=0, microsecond=0) - start_dt).days + 1)
        days_remaining = max(0, (end_dt - now.replace(hour=0, minute=0, second=0, microsecond=0)).days)
    except Exception:
        days_in_period = 30
        day_of_period = now.day
        days_remaining = 30 - now.day
    ru_month = _RU_MONTHS.get(now.month, "")

    # Расходы за период по категориям
    by_expense_cat: Dict[str, float] = {}
    total_expenses = 0
    try:
        expense_records = await db_query(config.nexus.db_finance, filter_obj={"and": [
            {"property": "Тип", "select": {"equals": "💸 Расход"}},
            {"property": "Дата", "date": {"on_or_after": period_start}},
            {"property": "Дата", "date": {"on_or_before": today_str}},
        ]}, page_size=500)
        for r in expense_records:
            cat = (r["properties"].get("Категория", {}).get("select") or {}).get("name", "💳 Прочее")
            amt = r["properties"].get("Сумма", {}).get("number") or 0
            by_expense_cat[cat] = by_expense_cat.get(cat, 0) + amt
            total_expenses += amt
    except Exception:
        pass

    # ── Формируем сообщение ──
    lines = ["<b>💰 Бюджет на {} (день {}/{})</b>".format(ru_month, day_of_period, days_in_period)]

    # Доходы — с источниками
    income_items = budget.get("доходы", [])
    income_total = sum(d.get("amount", 0) for d in income_items)
    if income_items:
        lines.append("\n<b>📥 Доход ({:,}₽):</b>".format(int(income_total)))
        for d in income_items:
            lines.append("  {} — {:,}₽".format(d["name"], int(d["amount"])))

    # Обязательные — сгруппированы по категории
    obligatory_items = budget.get("обязательные", [])
    obligatory_total = sum(o["amount"] for o in obligatory_items)
    if obligatory_items:
        lines.append("\n<b>🔒 Обязательные ({:,}₽):</b>".format(int(obligatory_total)))
        for ob in obligatory_items:
            lines.append("  {} — {:,}₽".format(ob["name"], int(ob["amount"])))

    distributable = income_total - obligatory_total
    if distributable > 0:
        lines.append("\n💳 Распределяемые: <b>{:,}₽</b>".format(int(distributable)))

    # Долги
    debts = budget.get("долги", [])
    total_debt_payments = 0
    total_debt_amount = sum(d.get("amount", 0) for d in debts)
    if debts:
        lines.append("\n<b>📋 Долги ({:,}₽):</b>".format(int(total_debt_amount)))
        for d in debts:
            mp = d.get("monthly_payment", 0)
            total_debt_payments += mp
            strategy = d.get("strategy", "").strip()
            if mp > 0:
                lines.append("  {} — {:,}₽ · {:,}₽/мес".format(
                    d["name"], int(d["amount"]), int(mp)))
                if strategy:
                    lines.append("    💬 {}".format(strategy))
            else:
                strat_display = strategy if strategy else "отложен"
                lines.append("  {} — {:,}₽ · {}".format(
                    d["name"], int(d["amount"]), strat_display))
        lines.append("\n💳 Платежей: <b>{:,}₽/мес</b>".format(int(total_debt_payments)))

    # Лимиты с прогрессом
    limits = budget.get("лимиты", [])
    if limits:
        limits_total = sum(l["amount"] for l in limits)
        lines.append("\n<b>📊 Лимиты · {}:</b>".format(ru_month))
        spent_in_limits = 0
        for l in limits:
            display_name = _display_limit_name(l["name"])
            limit_amt = l["amount"]
            spent = 0.0
            for cat_key, cat_spent in by_expense_cat.items():
                cat_link_key = _cat_link(cat_key)
                name_key = l["name"].lower().replace("лимит_", "")
                if name_key in cat_link_key or cat_link_key in name_key or name_key in cat_key.lower():
                    spent += cat_spent
            spent_in_limits += spent
            pct = int(spent / limit_amt * 100) if limit_amt else 0
            indicator = "🟢" if pct < 70 else ("🟡" if pct < 90 else "🔴")
            if day_of_period > 1:
                lines.append("  {} — {:,} / {:,}₽ ({}%) {}".format(
                    display_name, int(spent), int(limit_amt), pct, indicator))
            else:
                lines.append("  {} — {:,}₽".format(display_name, int(limit_amt)))

        if day_of_period > 1:
            lines.append("\n💳 Потрачено: {:,} / {:,}₽".format(int(spent_in_limits), int(limits_total)))
            free_in_limits = limits_total - spent_in_limits
            daily_left = free_in_limits / max(days_remaining, 1)
            lines.append("💳 Свободных: <b>{:,}₽</b> · {:,}₽/день".format(
                int(max(0, free_in_limits)), int(max(0, daily_left))))
        else:
            lines.append("\n💳 Итого: {:,}₽".format(int(limits_total)))

    # Цели
    goals = budget.get("цели", [])
    if goals:
        has_active_goals = any(g.get("saving", 0) > 0 for g in goals)
        if has_active_goals:
            lines.append("\n<b>🎯 Цели:</b>")
            for i, g in enumerate(goals, 1):
                saving = g.get("saving", 0)
                target = g.get("target", 0)
                if saving > 0:
                    months_to = int(target / saving) if saving else 0
                    lines.append("  {}. {} — {:,}₽ · {:,}₽/мес → ~{} мес".format(
                        i, g["name"], int(target), int(saving), months_to))
                else:
                    lines.append("  {}. {} — {:,}₽ · после долгов".format(
                        i, g["name"], int(target)))
        else:
            lines.append("\n<b>🎯 Цели (после закрытия долгов):</b>")
            for i, g in enumerate(goals, 1):
                lines.append("  {}. {} — {:,}₽".format(i, g["name"], int(g.get("target", 0))))

    return "\n".join(lines)


async def _check_budget_limit(category: str, message: Message, user_notion_id: str = "", amount: float = 0) -> None:
    """После записи расхода — проверить бюджетный лимит по категории (period-aware)."""
    logger.info("_check_budget_limit called: category=%s amount=%.0f", category, amount)
    mem_db = os.environ.get("NOTION_DB_MEMORY")
    if not mem_db:
        logger.info("_check_budget_limit: NOTION_DB_MEMORY not set, skip")
        return
    link = _cat_link(category)
    limits = await _get_limits(mem_db)
    logger.info("_check_budget_limit: limits=%s link=%r", limits, link)
    limit_amount: Optional[float] = None
    for key, val in limits.items():
        if key in link or link in key:
            limit_amount = val
            break
    if not limit_amount:
        logger.info("_check_budget_limit: no limit for category=%r, skip", category)
        # Показать остаток свободных даже без лимита
        try:
            result = await _calc_free_remaining(user_notion_id)
            if result:
                free_left, days_rem = result
                daily = free_left / max(days_rem, 1)
                await message.answer(
                    f"💳 Свободных: {free_left:,.0f}₽ · {daily:,.0f}₽/день",
                    parse_mode="HTML",
                )
        except Exception:
            pass
        # Проверить: если категория — обязательный расход, НЕ предлагать лимит
        is_obligatory = False
        try:
            budget = await _load_budget_data(user_notion_id)
            for ob in budget.get("обязательные", []):
                ob_link = _cat_link(ob.get("name", ""))
                if ob_link in link or link in ob_link:
                    is_obligatory = True
                    break
        except Exception:
            pass

        if is_obligatory:
            logger.info("_check_budget_limit: %r is obligatory, skip limit suggestion", category)
            return

        # Предложить установить лимит (1 раз за сессию)
        uid = getattr(message, "from_user", None)
        uid_id = uid.id if uid else 0
        if uid_id and (uid_id, link) not in _limit_suggested:
            _limit_suggested.add((uid_id, link))
            try:
                await message.answer(
                    f"💡 Хочешь поставить лимит на <b>{category}</b>?",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text="5 000₽", callback_data=f"setlim_{link}_5000"),
                        InlineKeyboardButton(text="10 000₽", callback_data=f"setlim_{link}_10000"),
                        InlineKeyboardButton(text="15 000₽", callback_data=f"setlim_{link}_15000"),
                    ], [
                        InlineKeyboardButton(text="Другая сумма", callback_data=f"setlim_{link}_custom"),
                        InlineKeyboardButton(text="Не надо", callback_data="setlim_skip"),
                    ]]),
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.debug("limit suggest error: %s", e)
        return

    from core.config import config
    from core.notion_client import db_query
    now = datetime.now(MOSCOW_TZ)
    payday = await _get_payday()
    period_start, period_end = _period_bounds(payday)
    today_str = now.strftime("%Y-%m-%d")
    try:
        records = await db_query(config.nexus.db_finance, filter_obj={"and": [
            {"property": "Тип",       "select": {"equals": "💸 Расход"}},
            {"property": "Категория", "select": {"equals": category}},
            {"property": "Дата",      "date":   {"on_or_after": period_start}},
            {"property": "Дата",      "date":   {"on_or_before": today_str}},
        ]}, page_size=200)
        period_total = sum((p["properties"].get("Сумма", {}).get("number") or 0) for p in records)
        logger.info("_check_budget_limit: period_total=%.0f limit=%.0f category=%s",
                    period_total, limit_amount, category)
    except Exception as e:
        logger.error("_check_budget_limit db_query: %s", e, exc_info=True)
        return

    pct = period_total / limit_amount * 100 if limit_amount else 0

    # Indicator by percentage
    if pct < 60:
        indicator = "🟢"
    elif pct < 85:
        indicator = "🟡"
    else:
        indicator = "🔴"

    # Собираем всё в ОДНО сообщение
    parts = []

    if pct >= 100:
        over = period_total - limit_amount
        parts.append(f"🚨 {category}: <b>{period_total:,.0f} / {limit_amount:,.0f}₽</b> ({pct:.0f}%!) +{over:,.0f}₽ overflow")
        # Impulse overflow
        try:
            impulse_limit, impulse_used = await _calc_impulse_status(period_start, user_notion_id)
            if impulse_limit > 0:
                impulse_left = impulse_limit - impulse_used
                imp_pct = impulse_used / impulse_limit * 100
                imp_ind = "🟢" if imp_pct < 60 else ("🟡" if imp_pct < 85 else "🔴")
                parts.append(f"  → overflow {over:,.0f}₽ → импульсивный")
                parts.append(f"🎲 Импульсивный: {impulse_used:,.0f} / {impulse_limit:,.0f}₽ ({imp_pct:.0f}%) {imp_ind}")
                if impulse_left <= 0:
                    parts.append("🚨 Импульсивный бюджет исчерпан!")
                # Auto-create impulse expense for overflow
                try:
                    await _handle_impulse_overflow(category, over, message, user_notion_id, period_start)
                except Exception as _oe:
                    logger.debug("impulse overflow create: %s", _oe)
            else:
                parts.append(f"  → overflow {over:,.0f}₽ (нет импульсивного резерва)")
        except Exception as _e:
            logger.debug("impulse calc: %s", _e)
    elif pct >= 85:
        parts.append(f"⚠️ {category}: {period_total:,.0f} / {limit_amount:,.0f}₽ ({pct:.0f}%) {indicator}")
    else:
        parts.append(f"📊 {category}: {period_total:,.0f} / {limit_amount:,.0f}₽ ({pct:.0f}%) {indicator}")

    # Debt remaining
    try:
        budget_data = await _load_budget_data(user_notion_id)
        if budget_data.get("долги"):
            debt_total = sum(d.get("amount", 0) for d in budget_data["долги"])
            parts.append(f"📋 Долги: {debt_total:,.0f}₽")
    except Exception:
        pass

    # Free/day
    try:
        result = await _calc_free_remaining(user_notion_id)
        if result:
            free_left, days_rem = result
            daily = free_left / max(days_rem, 1)
            parts.append(f"💳 Свободных: {free_left:,.0f}₽ · {daily:,.0f}₽/день")
    except Exception:
        pass

    # Предупреждения по привычкам
    if "Привычки" in category or "привычки" in category.lower():
        show_warning = random.random() < 0.2
        if pct >= 80:
            show_warning = True
        if show_warning:
            parts.append(random.choice(HABIT_WARNINGS))

    await message.answer("\n".join(parts), parse_mode="HTML")


async def _show_free_remaining(message: Message, user_notion_id: str = "") -> None:
    """Показать остаток свободных денег после расхода."""
    try:
        result = await _calc_free_remaining(user_notion_id)
        if result:
            free_left, days_rem = result
            daily = free_left / max(days_rem, 1)
            await message.answer(f"💳 Свободных: {free_left:,.0f}₽ · {daily:,.0f}₽/день")
    except Exception as e:
        logger.debug("free remaining skip: %s", e)


async def get_finance_period(start_date: str, end_date: str, label: str,
                             user_notion_id: str = "", show_daily_avg: bool = False) -> str:
    """Сводка за произвольный период. start_date/end_date = 'YYYY-MM-DD'."""
    from core.notion_client import query_pages
    from core.config import config

    db_id = os.environ.get("NOTION_DB_FINANCE") or config.nexus.db_finance
    conditions = [
        {"property": "Дата", "date": {"on_or_after": start_date}},
        {"property": "Дата", "date": {"on_or_before": end_date}},
    ]
    records = await query_pages(db_id, filters={"and": conditions}, page_size=200)

    total_expense = 0.0
    total_income = 0.0
    by_cat: Dict[str, float] = {}

    for r in records:
        props = r["properties"]
        amount = props.get("Сумма", {}).get("number") or 0
        type_name = (props.get("Тип", {}).get("select") or {}).get("name", "")
        cat = (props.get("Категория", {}).get("select") or {}).get("name", "")
        if "Доход" in type_name:
            total_income += amount
        elif "Расход" in type_name:
            total_expense += amount
            if cat:
                by_cat[cat] = by_cat.get(cat, 0) + amount

    if not by_cat and total_income == 0:
        return f"💰 {label}\n\nРасходов нет 🎉"

    lines = [f"💰 <b>{label}</b>\n"]
    for cat in sorted(by_cat, key=lambda c: -by_cat[c]):
        lines.append(f"  {cat} — {by_cat[cat]:,.0f}₽")

    lines.append(f"\n💸 Итого расходы: <b>{total_expense:,.0f}₽</b>")
    if total_income > 0:
        lines.append(f"💰 Доходы: <b>{total_income:,.0f}₽</b>")

    if show_daily_avg and total_expense > 0:
        try:
            d1 = datetime.strptime(start_date, "%Y-%m-%d").date()
            d2 = datetime.strptime(end_date, "%Y-%m-%d").date()
            days = max((d2 - d1).days + 1, 1)
            avg = total_expense / days
            lines.append(f"📊 В среднем {avg:,.0f}₽/день")
        except Exception:
            pass

    return "\n".join(lines)


async def get_finance_stats(month: str, user_notion_id: str = "", compare_prev: bool = False) -> str:
    """Сводка за месяц с лимитами. month = 'YYYY-MM'."""
    from core.praise import get_praise
    mem_db = os.environ.get("NOTION_DB_MEMORY")
    try:
        records = await finance_month(month, user_notion_id=user_notion_id)
    except Exception as e:
        logger.error("get_finance_stats: %s", e)
        return "⚠️ Ошибка получения данных"

    total_expense = 0.0
    total_income = 0.0
    by_cat: Dict[str, float] = {}

    for r in records:
        props = r["properties"]
        amount = props.get("Сумма", {}).get("number") or 0
        type_name = (props.get("Тип", {}).get("select") or {}).get("name", "")
        cat = (props.get("Категория", {}).get("select") or {}).get("name", "")
        if "Доход" in type_name:
            total_income += amount
        elif "Расход" in type_name:
            total_expense += amount
            if cat:
                by_cat[cat] = by_cat.get(cat, 0) + amount

    # Сравнение с предыдущим месяцем — отдельный чистый вид
    if compare_prev:
        try:
            prev_month = _month_offset(1)
            prev_records = await finance_month(prev_month, user_notion_id=user_notion_id)
            prev_by_cat: Dict[str, float] = {}
            prev_expense_total = 0.0
            for r in prev_records:
                props = r["properties"]
                amount = props.get("Сумма", {}).get("number") or 0
                type_name = (props.get("Тип", {}).get("select") or {}).get("name", "")
                cat = (props.get("Категория", {}).get("select") or {}).get("name", "")
                if "Расход" in type_name:
                    prev_expense_total += amount
                    if cat:
                        prev_by_cat[cat] = prev_by_cat.get(cat, 0) + amount

            m_num = int(month[5:7])
            prev_m_num = int(prev_month[5:7])
            cur_label = _RU_MONTHS.get(m_num, month)
            prev_label = _RU_MONTHS.get(prev_m_num, prev_month)

            cmp_lines = [f"📊 <b>Сравнение: {cur_label} vs {prev_label}</b>", ""]
            all_cats = set(list(by_cat.keys()) + list(prev_by_cat.keys()))
            cat_deltas: List[tuple] = []  # (cat, cur, prev, delta)
            for cat in sorted(all_cats, key=lambda c: -by_cat.get(c, 0.0)):
                cur = by_cat.get(cat, 0.0)
                prev = prev_by_cat.get(cat, 0.0)
                delta = cur - prev
                cat_deltas.append((cat, cur, prev, delta))
                if delta > 50:
                    pct_str = f"+{delta / prev * 100:.0f}%" if prev else ""
                    arrow = f"↑ +{delta:,.0f}₽" + (f" / {pct_str}" if pct_str else "")
                elif delta < -50:
                    pct_str = f"{delta / prev * 100:.0f}%" if prev else ""
                    arrow = f"↓ {delta:,.0f}₽" + (f" / {pct_str}" if pct_str else "")
                else:
                    arrow = "→ без изм."
                prev_str = f" ← {prev:,.0f}₽" if prev else ""
                cmp_lines.append(f"<b>{cat}</b>: {cur:,.0f}₽{prev_str}  <i>({arrow})</i>")

            cmp_lines.append("")
            exp_delta = total_expense - prev_expense_total
            if exp_delta > 50:
                exp_arrow = f"↑ +{exp_delta:,.0f}₽"
            elif exp_delta < -50:
                exp_arrow = f"↓ {exp_delta:,.0f}₽"
            else:
                exp_arrow = "→ без изм."
            cmp_lines.append(f"<b>Итого расходы: {total_expense:,.0f}₽</b>  <i>({exp_arrow})</i>")

            # Краткое ревью на основе дельт
            improved = sorted(
                [(cat, d) for cat, cur, prev, d in cat_deltas if d < -100],
                key=lambda x: x[1]
            )[:3]
            worsened = sorted(
                [(cat, d) for cat, cur, prev, d in cat_deltas if d > 100],
                key=lambda x: -x[1]
            )[:3]

            review: List[str] = []
            if improved:
                parts = ", ".join(f"<b>{cat}</b> (<i>{d:,.0f}₽</i>)" for cat, d in improved)
                review.append(f"✅ Сократил: {parts}")
            if worsened:
                parts = ", ".join(f"<b>{cat}</b> (<i>+{d:,.0f}₽</i>)" for cat, d in worsened)
                review.append(f"⚠️ Выросло: {parts}")
            if not improved and not worsened:
                review.append("→ Расходы стабильны, существенных изменений нет")
            elif exp_delta < -200:
                review.append(f"💚 Отличный результат — в целом на <b>{abs(exp_delta):,.0f}₽</b> меньше")
            elif exp_delta > 200:
                review.append(f"💡 Общий перерасход <b>+{exp_delta:,.0f}₽</b> — есть над чем поработать")

            if review:
                cmp_lines.append("")
                cmp_lines.extend(review)

            advice = await _get_finance_advice("\n".join(cmp_lines))
            if advice:
                cmp_lines.append(advice)

            report_title = f"Сравнение: {cur_label} vs {prev_label}"
            return await _stats_publish(report_title, cmp_lines)
        except Exception as e:
            logger.error("compare_prev: %s", e, exc_info=True)
            # fallback — продолжить обычный вывод

    limits: Dict[str, float] = {}
    if mem_db:
        limits = await _get_limits(mem_db)
    logger.info("get_finance_stats: mem_db=%r limits=%s by_cat=%s", mem_db, limits, by_cat)

    y, m = int(month[:4]), int(month[5:7])
    month_label = f"{_RU_MONTHS.get(m, month)} {y}"

    lines = [f"📊 <b>Финансы за {month_label}:</b>",
             f"Расходы: <b>{total_expense:,.0f}₽</b>",
             f"Доходы: <b>{total_income:,.0f}₽</b>",
             "", "<b>По категориям:</b>"]

    # cat → (spent, limit_val) для ревью
    cat_review: list[tuple[str, float, float]] = []

    for cat, amount in sorted(by_cat.items(), key=lambda x: -x[1]):
        link = _cat_link(cat)
        limit_val: Optional[float] = None
        for key, val in limits.items():
            logger.info("get_finance_stats: matching cat=%r link=%r key=%r → %s",
                        cat, link, key, key in link or link in key)
            if key in link or link in key:
                limit_val = val
                break
        if limit_val:
            pct = amount / limit_val * 100
            if pct > 100:
                status = f"🔴 <i>(+{amount - limit_val:,.0f}₽)</i>"
            elif pct >= 80:
                status = f"🟡 <i>({pct:.0f}%)</i>"
            else:
                status = f"🟢 <i>({pct:.0f}%)</i>"
            lines.append(f"<b>{cat}</b>: {amount:,.0f}₽ / лимит {limit_val:,.0f}₽ {status}")
            cat_review.append((cat, amount, limit_val))
        else:
            lines.append(f"<b>{cat}</b>: {amount:,.0f}₽")

    balance = total_income - total_expense
    sign = "+" if balance >= 0 else ""
    lines.append(f"\n💰 <b>Баланс: {sign}{balance:,.0f}₽</b>")

    # Ревью по лимитам — только если есть хотя бы один лимит
    if cat_review:
        review_lines: List[str] = []
        for cat, spent, lim in cat_review:
            pct = spent / lim * 100
            if pct > 100:
                over = spent - lim
                review_lines.append(
                    f"😬 <b>{cat}</b>: лимит превышен на {over:,.0f}₽. "
                    f"В следующем месяце попробуй уложиться в {lim:,.0f}₽"
                )
            elif pct >= 80:
                review_lines.append(
                    f"⚠️ <b>{cat}</b>: почти весь лимит — {spent:,.0f}₽ из {lim:,.0f}₽"
                )
            elif pct < 50:
                praise = get_praise("finance_under_limit")
                review_lines.append(
                    f"🎉 <b>{cat}</b>: отличный результат! Потратил {spent:,.0f}₽ из {lim:,.0f}₽\n"
                    + praise
                )
            # 50-80% — без комментария
        if review_lines:
            lines.append("\n<b>Ревью по лимитам:</b>")
            lines.extend(review_lines)

    # Прогноз до конца месяца
    import calendar as _cal
    now_fc = datetime.now(MOSCOW_TZ)
    day = now_fc.day
    if day >= 5 and day < 25 and cat_review:
        days_in_month = _cal.monthrange(now_fc.year, now_fc.month)[1]
        forecast_lines: List[str] = []
        for cat, spent, lim in cat_review:
            cat_forecast = spent / day * days_in_month
            if cat_forecast > lim * 1.1:
                forecast_lines.append(
                    f"📈 Прогноз <b>{cat}</b> до конца месяца: ~{cat_forecast:,.0f}₽ "
                    f"(лимит {lim:,.0f}₽) — темп высоковат"
                )
        if forecast_lines:
            lines.append("")
            lines.extend(forecast_lines)

    if total_expense > 0:
        advice = await _get_finance_advice("\n".join(lines))
        if advice:
            lines.append(advice)

    return "\n".join(lines)

CATEGORIES = [
    "🐾 Коты", "🏠 Жилье", "🚬 Привычки", "🍜 Продукты",
    "🍱 Кафе/Доставка", "🚕 Транспорт", "💅 Бьюти", "👗 Гардероб",
    "💻 Подписки", "🏥 Здоровье", "🕯️ Расходники", "📚 Хобби/Учеба",
    "💰 Зарплата", "🔮 Практика", "💳 Прочее",
]

SOURCES = ["💳 Карта", "💵 Наличные", "🔄 Бартер"]

STATS_SYSTEM = f"""Определи, запрашивает ли пользователь статистику по конкретной категории или конкретному имени/объекту.
Ответь ТОЛЬКО JSON без markdown:
{{
  "category": "одна из: {', '.join(CATEGORIES)} или null если запрос общей сводки",
  "type_": "expense если спрашивает о расходах, income если о доходах, null если оба",
  "description_search": "ключевое слово/имя для фильтра описания или null. Извлекай имена людей, магазины, организации после слов 'на/у/за/для/от'. Пример: 'у вадима' → 'вадим', 'на клинику' → 'клиника', 'за маму' → 'мама'",
  "months": 1,
  "compare": false
}}

Правила:
- Если в запросе есть имя/магазин/организация/объект рядом со словами 'на/у/за/для/от/по' → description_search = это слово
- Для доходов: 'получила X', 'пришло X', 'заработала X', 'доход по X', 'аренда', 'аренды' → description_search = X, type_=income
- Категория и description_search могут быть вместе: 'расходы на транспорт для вадима' → category=Транспорт, description_search=вадим
- Если категория явно указана (коты, транспорт, продукты...) → category; если имя/человек/объект → description_search
- months: сколько месяцев захватить. "за 3 месяца" → 3, "за полгода" → 6, "за год" → 12. По умолчанию 1
- compare=true если просят сравнить месяцы: "сравни", "сравнение", "как изменились расходы"

Примеры:
"сколько потратила на коты" → {{"category": "🐾 Коты", "type_": "expense", "description_search": null, "months": 1, "compare": false}}
"расходы на транспорт" → {{"category": "🚕 Транспорт", "type_": "expense", "description_search": null, "months": 1, "compare": false}}
"кола" → {{"category": "🚬 Привычки", "type_": "expense", "description_search": null, "months": 1, "compare": false}}
"заработала на практике" → {{"category": "🔮 Практика", "type_": "income", "description_search": null, "months": 1, "compare": false}}
"сколько перевела вадиму" → {{"category": null, "type_": "expense", "description_search": "вадим", "months": 1, "compare": false}}
"расходы на клинику" → {{"category": "🏥 Здоровье", "type_": "expense", "description_search": "клиника", "months": 1, "compare": false}}
"у мамы" → {{"category": null, "type_": null, "description_search": "мама", "months": 1, "compare": false}}
"сколько получила аренды" → {{"category": null, "type_": "income", "description_search": "аренда", "months": 1, "compare": false}}
"сколько пришло от вадима" → {{"category": null, "type_": "income", "description_search": "вадим", "months": 1, "compare": false}}
"доход по аренде" → {{"category": null, "type_": "income", "description_search": "аренда", "months": 1, "compare": false}}
"все доходы" → {{"category": null, "type_": "income", "description_search": null, "months": 1, "compare": false}}
"сводка за месяц" → {{"category": null, "type_": null, "description_search": null, "months": 1, "compare": false}}
"статистика" → {{"category": null, "type_": null, "description_search": null, "months": 1, "compare": false}}
"сколько потратила на еду за 3 месяца" → {{"category": "🍜 Продукты", "type_": "expense", "description_search": null, "months": 3, "compare": false}}
"расходы на кафе за 2 месяца" → {{"category": "🍱 Кафе/Доставка", "type_": "expense", "description_search": null, "months": 2, "compare": false}}
"статистика за полгода" → {{"category": null, "type_": null, "description_search": null, "months": 6, "compare": false}}
"сравни месяцы" → {{"category": null, "type_": null, "description_search": null, "months": 2, "compare": true}}
"сравнение расходов" → {{"category": null, "type_": null, "description_search": null, "months": 2, "compare": true}}
"сравни расходы на кафе" → {{"category": "🍱 Кафе/Доставка", "type_": "expense", "description_search": null, "months": 2, "compare": true}}"""

ADVICE_SYSTEM = """Ты смотришь на финансовые данные пользователя и даёшь ОДИН конкретный инсайт.

Правила:
- 2–3 предложения максимум
- Называй конкретные суммы, категории и цифры из данных
- ЗАПРЕЩЕНО: "откладывай X% дохода", "создай подушку безопасности" без расчёта, "веди учёт расходов", "избегай импульсивных покупок", любые советы-клише не из данных
- Ищи: перекос между категориями, конкретную возможность с расчётом, паттерн роста/снижения, реальное достижение с объяснением почему оно значимо
- Если всё ровно — найди что-то интересное в соотношениях, не выдумывай проблемы
- Тон: умный друг, без нотаций, без "следует", "рекомендую"
- Язык: русский, разговорный
- Только текст инсайта, никаких заголовков и префиксов"""

PARSE_SYSTEM = f"""Извлеки финансовую запись. Исправляй опечатки. Ответь ТОЛЬКО JSON без markdown:
{{
  "amount": число (всегда положительное),
  "type_": "💰 Доход" или "💸 Расход",
  "category": "одна из: {', '.join(CATEGORIES)}",
  "source": "одна из: {', '.join(SOURCES)}",
  "description": "краткое описание на русском: кто/что/куда (исправь опечатки)",
  "is_update": false,
  "update_field": null,
  "update_value": null,
  "confidence": "high" или "low",
  "question": "уточняющий вопрос если confidence=low, иначе null"
}}

Если это запрос на ИЗМЕНЕНИЕ последней записи (слова: измени, поменяй, исправь, обнови):
  "is_update": true,
  "update_field": "source" | "category" | "amount" | "description",
  "update_value": "новое значение в точном формате из списка",
  "amount": 0

Правила source: нал/наличные/кэш→"💵 Наличные", бартер→"🔄 Бартер", иначе→"💳 Карта"

Правила confidence:
- confidence=HIGH (не спрашивай): если type_ понятен ИЛИ сумма+категория понятны
- confidence=LOW (спроси): ТОЛЬКО если ВООБЩЕ непонятно — это доход или расход? (например "500р от вадима")
- НЕ ставь low только потому что не знаешь описание — description="?" и high достаточно

Правила type_:
- "💸 Расход" если: имя товара/услуги (такси, продукты, кофе, энергетик), NEGATIVE сумма,
  слова купил/купила/потратила/заплатила/оплатила/потрачено/расход
- "💰 Доход" если: зарплата/аванс/перевод/пришло/получила/поступление/доход/аренда
- По умолчанию если неясно → "💸 Расход"

Категории (выбирай ближайшую, НЕ используй "💳 Прочее" если хоть что-то подходит):
- 🐾 Коты: корм, лоток, ветеринар, игрушки для кота/кошки
- 🏠 Жилье: аренда, ЖКХ, ремонт, мебель, квартира, дом
- 🚬 Привычки: сигареты, табак, алкоголь, энергетик, Monster, Red Bull, Burn, пиво, вино
- 🍜 Продукты: еда, продукты, супермаркет, магазин, снеки, чипсы, орехи, сок, вода, молоко, хлеб
- 🍱 Кафе/Доставка: кафе, ресторан, кофейня, доставка, Самокат, Яндекс.Еда, бургер, пицца, роллы, суши
- 🚕 Транспорт: такси, Яндекс.Такси, метро, автобус, бензин, каршеринг
- 💅 Бьюти: маникюр, педикюр, стрижка, косметика, уходовая, салон красоты, депиляция
- 👗 Гардероб: одежда, обувь, аксессуары, Wildberries, OZON (вещи)
- 💻 Подписки: Netflix, Spotify, iCloud, VPN, ChatGPT, подписка, сервис
- 🏥 Здоровье: аптека, лекарства, врач, анализы, клиника, больница
- 🕯️ Расходники: свечи, масла, травы, ароматизатор, уборка, хозтовары
- 📚 Хобби/Учеба: книги, курсы, кино, театр, концерт, игры, спорт
- 💰 Зарплата: зарплата, аванс (только доход)
- 🔮 Практика: таро, ритуал, сеанс, гадание (доход от практики)
- 💳 Прочее: ТОЛЬКО если ни одна категория выше не подходит вообще

Примеры:
"350 вадим" → description="Вадим", type_="💸 Расход", category="💳 Прочее", confidence="low", question="350р Вадиму — за что?"
"450р такси" → description="Такси", type_="💸 Расход", category="🚕 Транспорт", confidence="high"
"монстр 120" → description="Monster Energy", type_="💸 Расход", category="🚬 Привычки", confidence="high"
"кофе 180р" → description="Кофе", type_="💸 Расход", category="🍱 Кафе/Доставка", confidence="high"
"снеки 300" → description="Снеки", type_="💸 Расход", category="🍜 Продукты", confidence="high"
"пришла зарплата 80к" → description="Зарплата", type_="💰 Доход", category="💰 Зарплата", confidence="high"
"500р от вадима" → description="От Вадима", type_="💸 Расход", category="💳 Прочее", confidence="low", question="500р от Вадима — это доход или расход?"
"измени карту на бартер" → is_update=true, update_field="source", update_value="🔄 Бартер", amount=0
"поменяй категорию на продукты" → is_update=true, update_field="category", update_value="🍜 Продукты", amount=0"""

# Pending-записи до уточнения: {{user_id: data}}
_pending_finance: dict = {}
# Последняя записанная страница: {{user_id: page_id}}
_last_page_id: dict = {}


def _today() -> str:
    return datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")


def _month() -> str:
    return datetime.now(MOSCOW_TZ).strftime("%Y-%m")


def _format_record(data: dict) -> str:
    icon = "💰" if "Доход" in data.get("type_", "") else "💸"
    return (
        f"✅ Записано\n"
        f"{icon} <b>{data['amount']}₽</b>\n"
        f"📝 {data.get('description', '—')}\n"
        f"🏷 {data.get('category', '—')}\n"
        f"💳 {data.get('source', '—')}"
    )


async def _save_finance(data: dict, db_id: str, bot_label: str = "☀️ Nexus",
                        user_notion_id: str = "", uid: int = 0) -> str:
    """Создаёт запись в Notion. Возвращает page_id или None."""
    from core.notion_client import _relation
    props = {
        "Описание": _title(data.get("description") or ""),
        "Дата":     _date(_today()),
        "Сумма":    _number(float(data["amount"])),
        "Категория": _select(data.get("category", "💳 Прочее")),
        "Тип":      _select(data.get("type_", "💸 Расход")),
        "Источник": _select(data.get("source", "💳 Карта")),
        "Бот":      _select(bot_label),
    }
    if user_notion_id:
        props["🪪 Пользователи"] = _relation(user_notion_id)
    page_id = await page_create(db_id, props)
    if page_id and uid:
        from nexus.handlers.tasks import last_record_set
        last_record_set(uid, "finance", page_id)
    return page_id


async def _update_last_finance(uid: int, field: str, value: str) -> bool:
    """Обновляет поле последней записанной страницы."""
    page_id = _last_page_id.get(uid)
    if not page_id:
        return False

    field_map = {
        "source":      ("Источник", _select(value)),
        "category":    ("Категория", _select(value)),
        "description": ("Описание", _title(value)),
        "amount":      ("Сумма", _number(float(value))),
    }
    if field not in field_map:
        return False

    notion_key, notion_val = field_map[field]
    try:
        await update_page(page_id, {notion_key: notion_val})
        return True
    except Exception as e:
        logger.error("_update_last_finance error: %s", e)
        return False


async def handle_finance_text(message: Message, text: str, bot_label: str = "☀️ Nexus",
                              user_notion_id: str = "") -> None:
    from core.config import config

    raw = await ask_claude(text, system=PARSE_SYSTEM, max_tokens=400)
    try:
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(raw)
    except Exception:
        await log_error(text, "parse_error", raw)
        await message.answer("⚠️ Не смог разобрать. Попробуй: «450р такси»")
        return

    uid = message.from_user.id

    # ── Post-processing: форсируем Расход если явные признаки ───────────────
    _EXPENSE_VERBS = re.compile(
        r"\b(купил[аи]?|потратил[аи]?|заплатил[аи]?|оплатил[аи]?|"
        r"потрачено|расход|заплачено|сняла|сняли?)\b",
        re.IGNORECASE,
    )
    raw_amount = data.get("amount", 0)
    # Отрицательная сумма → всегда расход
    if isinstance(raw_amount, (int, float)) and raw_amount < 0:
        data["amount"] = abs(raw_amount)
        data["type_"] = "💸 Расход"
        data["confidence"] = "high"
    # Явный глагол расхода → форсируем тип и убираем вопрос о типе
    elif _EXPENSE_VERBS.search(text):
        data["type_"] = "💸 Расход"
        data["confidence"] = "high"
    # ── Конец post-processing ────────────────────────────────────────────────

    # Запрос на изменение последней записи
    if data.get("is_update"):
        field = data.get("update_field", "")
        value = data.get("update_value", "")
        ok = await _update_last_finance(uid, field, value)
        if ok:
            labels = {"source": "Источник", "category": "Категория",
                      "description": "Описание", "amount": "Сумма"}
            await message.answer(f"✏️ Обновлено: {labels.get(field, field)} → <b>{value}</b>")
        else:
            await message.answer("⚠️ Нет последней записи для обновления.")
        return

    if not data.get("amount"):
        await message.answer("⚠️ Не нашёл сумму.")
        return

    # Низкая уверенность — уточняем только если есть маркеры дохода/бартера
    if data.get("confidence") == "low" and data.get("question"):
        has_income = bool(_INCOME_MARKERS_RE.search(text))
        has_barter = bool(_BARTER_MARKERS_RE.search(text))
        if not has_income and not has_barter:
            # Нет маркеров дохода/бартера → автоматически расход
            logger.info("finance: low confidence but no income/barter markers → auto-expense")
            data["type_"] = "💸 Расход"
            data["confidence"] = "high"
        else:
            _pending_finance[uid] = (data, user_notion_id)
            amount = data.get("amount", 0)
            description = data.get("description", "?")
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="💸 Расход", callback_data="fin_expense"),
                    InlineKeyboardButton(text="💰 Доход", callback_data="fin_income"),
                    InlineKeyboardButton(text="🔄 Бартер", callback_data="fin_barter"),
                ]
            ])
            await message.answer(
                f"❓ <b>{amount:,.0f}₽ — {description}</b>\n\n"
                f"Это доход, расход или бартер?",
                reply_markup=kb,
            )
            return

    # Высокая уверенность — пишем сразу
    page_id = await _save_finance(data, config.nexus.db_finance, bot_label, user_notion_id, uid=uid)
    if not page_id:
        await message.answer("⚠️ Ошибка записи в Notion.")
        return

    _last_page_id[uid] = page_id
    await react(message, "👌" if "Расход" in data.get("type_", "") else "🏆")
    await message.answer(_format_record(data))

    # Smart recall: ищем в памяти по описанию покупки
    try:
        desc = (data.get("description") or "").strip()
        if desc and "Расход" in data.get("type_", ""):
            from core.memory import recall_from_memory
            _fact = await recall_from_memory(desc)
            if _fact:
                await message.answer(f"💡 <i>{_fact} — как обычно?</i>")
    except Exception as e:
        logger.debug("finance recall skip: %s", e)

    if "Расход" in data.get("type_", ""):
        logger.info("finance saved: category=%s — calling budget check", data.get("category", ""))
        try:
            await _check_budget_limit(data.get("category", ""), message, user_notion_id)
        except Exception as e:
            logger.error("budget check error: %s", e, exc_info=True)
        # Предложить вычеркнуть из списка покупок
        try:
            from core.list_manager import find_matching_items
            desc = (data.get("description") or "").strip()
            cat = data.get("category") or ""
            if desc:
                matches = await find_matching_items(desc, cat, bot_label, user_notion_id)
                if matches:
                    buttons = []
                    item_names = []
                    for m in matches[:3]:
                        cat_e = (m.get("category") or "").split(" ")[0]
                        item_names.append(f"◻️ {m['name']} · {cat_e}")
                        buttons.append([InlineKeyboardButton(
                            text=f"✅ {m['name']}",
                            callback_data=f"list_cross_{m['id'][:28]}",
                        )])
                    buttons.append([InlineKeyboardButton(text="Нет", callback_data="list_cross_no")])
                    await message.answer(
                        f"🛒 Есть в списке:\n" + "\n".join(item_names),
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
                        parse_mode="HTML",
                    )
        except Exception as e:
            logger.debug("list cross-off check: %s", e)

    # Триггер при зарплате: показать краткий бюджет
    if "Доход" in data.get("type_", "") and "Зарплата" in data.get("category", ""):
        try:
            budget_msg = await build_budget_message(user_notion_id)
            if budget_msg:
                await message.answer(f"💰 Зарплата получена! Твой бюджет на месяц:\n\n{budget_msg}", parse_mode="HTML")
        except Exception as e:
            logger.debug("salary budget trigger: %s", e)


@router.message(F.text)
async def handle_finance_clarification(message: Message, user_notion_id: str = "") -> None:
    """Текстовые ответы на уточнение: вместо кнопок или уточнение данных."""
    from core.config import config

    uid = message.from_user.id

    # Обработка ввода кастомного лимита
    cat_link = _pending_limit.get(uid)
    if cat_link:
        text_raw = (message.text or "").strip().replace(" ", "")
        if text_raw.isdigit():
            _pending_limit.pop(uid, None)
            amount = int(text_raw)
            await _save_limit_to_memory(cat_link, amount, user_notion_id)
            await message.answer(f"✅ Лимит на {cat_link}: <b>{amount:,}₽/мес</b>")
            return
        elif text_raw.lower() in ("отмена", "нет", "cancel"):
            _pending_limit.pop(uid, None)
            await message.answer("❌ Отмена.")
            return

    pending_entry = _pending_finance.get(uid)
    if not pending_entry:
        return
    # Support both formats: data dict (old) or (data, user_notion_id) tuple (new)
    if isinstance(pending_entry, tuple):
        pending, stored_uid = pending_entry
    else:
        pending = pending_entry
        stored_uid = user_notion_id

    text_lower = (message.text or "").strip().lower()

    if text_lower in ("отмена", "нет", "cancel", "❌"):
        _pending_finance.pop(uid, None)
        await message.answer("❌ Отменено.")
        return

    if text_lower in ("записать", "да", "ок", "ok", "✅", "записать как есть"):
        _pending_finance.pop(uid, None)
        page_id = await _save_finance(pending, config.nexus.db_finance, user_notion_id=stored_uid, uid=uid)
        if page_id:
            _last_page_id[uid] = page_id
            await react(message, "👌" if "Расход" in pending.get("type_", "") else "🏆")
            await message.answer(_format_record(pending))
            if "Расход" in pending.get("type_", ""):
                try:
                    await _check_budget_limit(pending.get("category", ""), message, stored_uid)
                except Exception as e:
                    logger.debug("budget check skip: %s", e)
        else:
            await message.answer("⚠️ Ошибка записи в Notion.")
        return

    # Уточнение через Claude
    UPDATE_SYSTEM = (
        f"У тебя финансовая запись и уточнение от пользователя. "
        f"Обнови нужные поля. Ответь ТОЛЬКО JSON без markdown:\n"
        f'{{"description":"...","category":"одна из: {", ".join(CATEGORIES)}",'
        f'"type_":"💰 Доход или 💸 Расход","source":"одна из: {", ".join(SOURCES)}"}}\n'
        f"Текущая запись: {json.dumps(pending, ensure_ascii=False)}"
    )
    raw = await ask_claude(message.text.strip(), system=UPDATE_SYSTEM, max_tokens=200)
    try:
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        pending.update(json.loads(raw))
    except Exception:
        pass

    _pending_finance.pop(uid, None)
    page_id = await _save_finance(pending, config.nexus.db_finance, user_notion_id=stored_uid, uid=uid)
    if not page_id:
        await message.answer("⚠️ Ошибка записи в Notion.")
        return

    _last_page_id[uid] = page_id
    await message.answer(_format_record(pending))
    if "Расход" in pending.get("type_", ""):
        try:
            await _check_budget_limit(pending.get("category", ""), message, stored_uid)
        except Exception as e:
            logger.debug("budget check skip: %s", e)


@router.callback_query(F.data == "fin_save_asis")
async def fin_save_asis(call: CallbackQuery) -> None:
    from core.config import config
    uid = call.from_user.id
    pending_entry = _pending_finance.pop(uid, None)
    if not pending_entry:
        await call.answer("Нет данных.")
        return
    if isinstance(pending_entry, tuple):
        pending, stored_uid = pending_entry
    else:
        pending, stored_uid = pending_entry, ""
    page_id = await _save_finance(pending, config.nexus.db_finance, user_notion_id=stored_uid, uid=uid)
    if page_id:
        _last_page_id[uid] = page_id
    await call.message.edit_text(_format_record(pending))
    if "Расход" in pending.get("type_", ""):
        logger.info("finance saved (asis): category=%s — calling budget check", pending.get("category", ""))
        try:
            await _check_budget_limit(pending.get("category", ""), call.message, stored_uid)
        except Exception as e:
            logger.error("budget check error: %s", e, exc_info=True)
    await call.answer()


# ── Pending custom limit: uid → cat_link (ждём число от пользователя) ─────────
_pending_limit: Dict[int, str] = {}


@router.callback_query(F.data == "fin_cancel")
async def fin_cancel(call: CallbackQuery) -> None:
    _pending_finance.pop(call.from_user.id, None)
    await call.message.edit_text("❌ Отмена.")
    await call.answer()


@router.callback_query(F.data == "msg_hide")
async def on_msg_hide(call: CallbackQuery) -> None:
    """Кнопка 🙈 Скрыть — удалить сообщение."""
    try:
        await call.message.delete()
    except Exception:
        await call.message.edit_text("🙈")
    await call.answer()


@router.callback_query(F.data.startswith("setlim_"))
async def on_set_limit(call: CallbackQuery, user_notion_id: str = "") -> None:
    """Обработчик кнопок установки лимита."""
    data = call.data  # setlim_{link}_{amount} or setlim_skip or setlim_{link}_custom
    if data == "setlim_skip":
        await call.message.edit_text("👌 Ок, без лимита.")
        await call.answer()
        return

    parts = data.split("_", 2)  # ['setlim', link, amount/custom]
    if len(parts) < 3:
        await call.answer()
        return
    cat_link = parts[1]
    value = parts[2]

    if value == "custom":
        _pending_limit[call.from_user.id] = cat_link
        await call.message.edit_text(f"💬 Напиши сумму лимита на <b>{cat_link}</b> (число в рублях):")
        await call.answer()
        return

    # Сохранить лимит
    amount = int(value)
    await _save_limit_to_memory(cat_link, amount, user_notion_id)
    await call.message.edit_text(f"✅ Лимит на {cat_link}: <b>{amount:,}₽/мес</b>")
    await call.answer()


async def _save_limit_to_memory(cat_link: str, amount: int, user_notion_id: str = "") -> None:
    """Сохранить лимит в Память."""
    from core.notion_client import db_query, _relation
    mem_db = os.environ.get("NOTION_DB_MEMORY")
    if not mem_db:
        return
    key = f"лимит_{cat_link}"
    fact = f"лимит: {cat_link} — {amount}₽/мес"
    props = {
        "Текст": _title(fact),
        "Ключ": _text(key),
        "Категория": _select("💰 Лимит"),
        "Связь": _text(cat_link),
        "Бот": _select("☀️ Nexus"),
        "Актуально": {"checkbox": True},
    }
    if user_notion_id:
        props["🪪 Пользователи"] = _relation(user_notion_id)
    # Обновить если существует
    try:
        existing = await db_query(mem_db, filter_obj={"and": [
            {"property": "Ключ", "rich_text": {"contains": key}},
            {"property": "Категория", "select": {"equals": "💰 Лимит"}},
        ]}, page_size=1)
        if existing:
            await update_page(existing[0]["id"], props)
        else:
            await page_create(mem_db, props)
    except Exception as e:
        logger.error("_save_limit_to_memory: %s", e)


@router.callback_query(F.data.startswith("fin_expense") | F.data.startswith("fin_income") | F.data.startswith("fin_barter"))
async def handle_finance_clarify(call: CallbackQuery, user_notion_id: str = "") -> None:
    """Обработчик уточнения доход/расход/бартер для неясных операций."""
    from core.config import config
    from core.notion_client import match_select, _relation

    action = call.data.split("_")[1]  # expense, income или barter
    uid = call.from_user.id

    pending_entry = _pending_finance.get(uid)
    if not pending_entry:
        await call.answer("⚠️ Сессия истекла. Отправь операцию ещё раз.")
        await call.message.edit_text("⚠️ Сессия истекла.")
        return

    await call.answer()

    # Support both formats
    if isinstance(pending_entry, tuple):
        pending, stored_uid = pending_entry
    else:
        pending = pending_entry
        stored_uid = user_notion_id

    amount = float(pending.get("amount", 0))
    category = pending.get("category", "💳 Прочее")
    source = pending.get("source", "💳 Карта")
    description = pending.get("description", "")

    db_id = config.nexus.db_finance

    if action == "barter":
        type_label = "💸 Расход"
        source = "🔄 Бартер"
    elif action == "income":
        type_label = "💰 Доход"
    else:
        type_label = "💸 Расход"

    real_category = await match_select(db_id, "Категория", category)
    real_source = await match_select(db_id, "Источник", source)
    real_type = await match_select(db_id, "Тип", type_label)

    props = {
        "Описание": _title(description),
        "Дата": _date(datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")),
        "Сумма": _number(amount),
        "Категория": _select(real_category),
        "Тип": _select(real_type),
        "Источник": _select(real_source),
        "Бот": _select("☀️ Nexus"),
    }
    eff_uid = stored_uid or user_notion_id
    if eff_uid:
        props["🪪 Пользователи"] = _relation(eff_uid)

    result = await page_create(db_id, props)

    if result:
        from nexus.handlers.tasks import last_record_set
        last_record_set(uid, "finance", result)
        sign = "−" if action != "income" else "+"
        icon = "💸" if action != "income" else "💰"
        text = f"{icon} <b>{sign}{amount:,.0f}₽</b> · <b>{description}</b>\n🏷 {real_category} <i>{real_source}</i>"
        await call.message.edit_text(text, parse_mode="HTML")
        _pending_finance.pop(uid, None)
        if action != "income":
            await _check_budget_limit(real_category, call.message)
    else:
        await call.message.edit_text("⚠️ Ошибка записи. Попробуй позже.")


async def handle_bank_screenshot(message: Message, bot_label: str = "☀️ Nexus") -> None:
    from core.config import config
    photo = message.photo[-1]
    file = await message.bot.get_file(photo.file_id)
    bio = await message.bot.download_file(file.file_path)
    image_b64 = base64.standard_b64encode(bio.read()).decode()

    await message.answer("🔍 Читаю скрин...")

    cats = ", ".join(CATEGORIES)
    srcs = ", ".join(SOURCES)
    system = (
        "Ты анализируешь скрин банковского приложения. "
        "Извлеки ВСЕ транзакции. Ответь ТОЛЬКО JSON без markdown:\n"
        f'{{"transactions": [{{"amount": число, "type_": "💰 Доход|💸 Расход", '
        f'"category": "одна из: {cats}", "source": "одна из: {srcs}", "description": "описание"}}]}}'
    )
    raw = await ask_claude_vision("Извлеки транзакции.", image_b64, system=system)
    try:
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(raw)
    except Exception:
        await log_error(message.caption or "bank_screenshot", "parse_error", raw)
        await message.answer("⚠️ Не смог распознать транзакции.")
        return

    uid = message.from_user.id
    saved = []
    for t in data.get("transactions", []):
        amount = float(t.get("amount") or 0)
        if not amount:
            continue
        page_id = await _save_finance(t, config.nexus.db_finance, bot_label)
        if page_id:
            _last_page_id[uid] = page_id
        icon = "💰" if "Доход" in t.get("type_", "") else "💸"
        saved.append(f"{icon} {amount:,.0f}₽ — {t.get('description', '')}")

    if not saved:
        await message.answer("Транзакций не найдено.")
        return
    await message.answer(f"✅ Записано {len(saved)}:\n" + "\n".join(saved[:15]))


_MONTH_MAP = {
    "январ": 1,  "янв": 1,
    "феврал": 2, "фев": 2,
    "март": 3,   "мар": 3,
    "апрел": 4,  "апр": 4,
    "май": 5,    "мая": 5,
    "июн": 6,
    "июл": 7,
    "август": 8, "авг": 8,
    "сентябр": 9,  "сен": 9,
    "октябр": 10,  "окт": 10,
    "ноябр": 11,   "ноя": 11,
    "декабр": 12,  "дек": 12,
}
_MONTH_RE = re.compile(
    r"\b(январ[яеь]?|янв|феврал[яеь]?|фев|март[ае]?|мар|апрел[яеь]?|апр"
    r"|май|мая|июн[яеь]?|июл[яеь]?|август[ае]?|авг"
    r"|сентябр[яеь]?|сен|октябр[яеь]?|окт|ноябр[яеь]?|ноя|декабр[яеь]?|дек)\b",
    re.IGNORECASE,
)


def _parse_month_from_query(text: str) -> str:
    """Вернуть 'YYYY-MM' из текста или текущий месяц."""
    now = datetime.now(MOSCOW_TZ)
    m = _MONTH_RE.search(text.lower())
    if not m:
        return _month()
    word = m.group(1).lower()
    # Найти номер месяца по максимальному совпадению префикса
    month_num = None
    for prefix, num in _MONTH_MAP.items():
        if word.startswith(prefix) or prefix.startswith(word[:3]):
            month_num = num
            break
    if month_num is None:
        return _month()
    # Год: текущий, но если месяц ещё не наступил — тот же год (не будущий)
    year = now.year
    if month_num > now.month:
        year -= 1  # "в декабре" в январе → прошлый декабрь
    return f"{year}-{month_num:02d}"


async def _get_finance_advice(data: str) -> str:
    """Один конкретный финансовый инсайт от Claude на основе данных статистики."""
    try:
        result = await ask_claude(data, system=ADVICE_SYSTEM, max_tokens=220)
        result = result.strip()
        if result:
            return f"\n💡 {result}"
    except Exception as e:
        logger.debug("_get_finance_advice: %s", e)
    return ""


def _month_offset(offset: int) -> str:
    """Вернуть 'YYYY-MM' для текущего месяца минус offset месяцев."""
    now = datetime.now(MOSCOW_TZ)
    month = now.month - offset
    year = now.year
    while month <= 0:
        month += 12
        year -= 1
    return f"{year}-{month:02d}"


async def _handle_multimonth_stats(
    months_count: int,
    category_filter: Optional[str],
    type_filter: Optional[str],
    description_search: Optional[str],
    compare_mode: bool,
    user_notion_id: str,
    uid: int,
) -> str:
    """Статистика за несколько месяцев с разбивкой по каждому."""
    _MONTHS_SHORT = "янв фев мар апр май июн июл авг сен окт ноя дек".split()
    notion_desc_kw = (description_search or "")[:5].strip() if description_search else ""

    month_strings = [_month_offset(i) for i in range(months_count - 1, -1, -1)]
    month_totals: List[tuple] = []  # (month_str, total)

    for ms in month_strings:
        records = await finance_month(
            ms,
            user_notion_id=user_notion_id,
            description_filter=notion_desc_kw,
            type_filter=type_filter or "",
        )
        total = 0.0
        for r in records:
            props = r["properties"]
            amount = props.get("Сумма", {}).get("number") or 0
            cat_name = (props.get("Категория", {}).get("select") or {}).get("name", "")
            type_name = (props.get("Тип", {}).get("select") or {}).get("name", "")
            if category_filter and cat_name != category_filter:
                continue
            if type_filter == "expense" and "Расход" not in type_name:
                continue
            if type_filter == "income" and "Доход" not in type_name:
                continue
            total += amount
        month_totals.append((ms, total))

    icon = "💸" if type_filter == "expense" else ("💰" if type_filter == "income" else "📊")
    label_parts = []
    if category_filter:
        label_parts.append(category_filter)
    if description_search:
        label_parts.append(f"«{description_search}»")
    label = " · ".join(label_parts) if label_parts else "Общая статистика"

    lines = [f"{icon} <b>{label} — {months_count} мес.</b>"]

    grand_total = 0.0
    for ms, total in month_totals:
        try:
            y, m = int(ms[:4]), int(ms[5:7])
            ml = f"{_MONTHS_SHORT[m - 1]} {y}"
        except Exception:
            ml = ms
        lines.append(f"<b>{ml}:</b> {total:,.0f}₽")
        grand_total += total

    avg = grand_total / months_count if months_count else 0
    lines.append(f"\n<b>Итого: {grand_total:,.0f}₽</b>  <i>· среднее: {avg:,.0f}₽/мес</i>")

    # Лимит если есть категория расходов
    if category_filter and type_filter != "income":
        try:
            mem_db = os.environ.get("NOTION_DB_MEMORY")
            if mem_db:
                limits = await _get_limits(mem_db)
                link = _cat_link(category_filter)
                limit_val: Optional[float] = None
                for key, val in limits.items():
                    if key in link or link in key:
                        limit_val = val
                        break
                if limit_val:
                    avg_pct = avg / limit_val * 100
                    if avg_pct > 100:
                        indicator = "🔴"
                    elif avg_pct >= 80:
                        indicator = "🟡"
                    else:
                        indicator = "🟢"
                    lines.append(
                        f"📊 Лимит: {limit_val:,.0f}₽/мес → в среднем {avg_pct:.0f}% {indicator}"
                    )
        except Exception as e:
            logger.debug("multimonth limit: %s", e)

    advice = await _get_finance_advice("\n".join(lines))
    if advice:
        lines.append(advice)

    report_title = f"{label} — {months_count} мес."
    return await _stats_publish(report_title, lines)


async def handle_finance_summary(query: str = "", user_notion_id: str = "", uid: int = 0) -> str:
    """Возвращает строку со статистикой. Вызывающий сам отправляет её пользователю."""
    logger.info("handle_finance_summary: user_notion_id=%r query=%r", user_notion_id, query)
    # Попробовать распарсить категорию и имя из запроса
    category_filter = None
    type_filter = None
    description_search = None
    parsed: dict = {}
    if query:
        raw = await ask_claude(query, system=STATS_SYSTEM, max_tokens=200)
        try:
            raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            parsed = json.loads(raw)
            category_filter = parsed.get("category") or None
            type_filter = parsed.get("type_") or None
            description_search = parsed.get("description_search") or None
            if description_search:
                description_search = description_search.lower().strip()
        except Exception:
            pass

    months_count = max(1, int(parsed.get("months") or 1))
    compare_mode = bool(parsed.get("compare"))

    # Сравнение текущий vs предыдущий — приоритет над мультимесячным режимом
    if compare_mode:
        return await get_finance_stats(_month(), user_notion_id=user_notion_id, compare_prev=True)

    # Мультимесячный режим
    if months_count > 1:
        return await _handle_multimonth_stats(
            months_count=months_count,
            category_filter=category_filter,
            type_filter=type_filter,
            description_search=description_search,
            compare_mode=compare_mode,
            user_notion_id=user_notion_id,
            uid=uid,
        )

    # Месяц: из текста запроса или текущий
    month_str = _parse_month_from_query(query) if query else _month()

    # Первые 4-5 символов для Notion title contains (fuzzy: "вадима" → "вади" → найдёт "вадиму")
    notion_desc_kw = (description_search or "")[:5].strip() if description_search else ""

    # Notion делает фильтрацию по описанию и типу на стороне API
    records = await finance_month(
        month_str,
        user_notion_id=user_notion_id,
        description_filter=notion_desc_kw,
        type_filter=type_filter or "",
    )
    now = datetime.now(MOSCOW_TZ)

    def _get_desc(props):
        title_items = (props.get("Описание", {}).get("title") or [])
        if title_items:
            return title_items[0].get("text", {}).get("content", "")
        return ""

    # Запрос по категории, описанию ИЛИ конкретному типу дохода/расхода
    if category_filter or description_search or type_filter:
        total = 0.0
        matched = []
        for r in records:
            props = r["properties"]
            amount = props.get("Сумма", {}).get("number") or 0
            cat_name = (props.get("Категория", {}).get("select") or {}).get("name", "")
            type_name = (props.get("Тип", {}).get("select") or {}).get("name", "")
            desc = _get_desc(props)

            # Фильтр по категории (Python-side, Notion не умеет exact match по select)
            if category_filter and cat_name != category_filter:
                continue
            # Фильтр по типу (Notion уже отфильтровал, но дублируем для надёжности)
            if type_filter == "expense" and "Расход" not in type_name:
                continue
            if type_filter == "income" and "Доход" not in type_name:
                continue

            total += amount
            date_str = (props.get("Дата", {}).get("date") or {}).get("start", "")
            matched.append((date_str, desc, amount))

        icon = "💸" if type_filter == "expense" else ("💰" if type_filter == "income" else "📊")
        label = "Расходы" if type_filter == "expense" else ("Доходы" if type_filter == "income" else "Итого")

        # Заголовок
        header_parts = []
        if category_filter:
            header_parts.append(category_filter)
        if description_search:
            header_parts.append(f"«{description_search}»")
        header = " · ".join(header_parts) if header_parts else "Фильтр"

        # Человекочитаемый заголовок месяца
        try:
            month_dt = datetime.strptime(month_str, "%Y-%m")
            month_label = f"{('январь февраль март апрель май июнь июль август сентябрь октябрь ноябрь декабрь'.split())[month_dt.month - 1]} {month_dt.year}"
        except Exception:
            month_label = now.strftime("%B %Y")

        report_title = f"{header} — {month_label}"

        # Сводка — строим всегда, независимо от пагинации
        lines = [
            f"{icon} <b>{header} — {month_label}</b>",
            f"{label}: <b>{total:,.0f}₽</b>  <i>({len(matched)} зап.)</i>" if matched else f"{label}: <b>{total:,.0f}₽</b>  <i>(0 зап.)</i>",
        ]

        if matched:
            all_sorted = sorted(matched, key=lambda x: x[0], reverse=True)
            from core.pagination import PAGE_SIZE as _PS, register_pages
            if uid and len(all_sorted) > _PS:
                # Регистрируем пагинацию для детального списка, в сводке добавляем подсказку
                _MONTHS = "янв фев мар апр май июн июл авг сен окт ноя дек".split()

                def _finance_fmt(it: dict) -> str:
                    try:
                        d = datetime.strptime(it["date"][:10], "%Y-%m-%d")
                        day = f"{d.day} {_MONTHS[d.month - 1]}"
                    except Exception:
                        day = it["date"][:10]
                    return f"• {day} — {it['desc'] or '—'} — {it['amount']:,.0f}₽"

                finance_items = [
                    {"date": ds, "desc": desc, "amount": amt}
                    for ds, desc, amt in all_sorted
                ]
                register_pages(uid, finance_items, f"{icon} {report_title} · {total:,.0f}₽", _finance_fmt)
                lines.append(f"\n📋 Записей много — список ниже ↓")
            else:
                lines.append("")
                for date_str, desc, amount in all_sorted:
                    try:
                        d = datetime.strptime(date_str[:10], "%Y-%m-%d")
                        day = f"{d.day} {('янв фев мар апр май июн июл авг сен окт ноя дек'.split())[d.month - 1]}"
                    except Exception:
                        day = date_str[:10]
                    lines.append(f"• {day} — {desc or '—'} — {amount:,.0f}₽")

        # Ревью по лимиту — только для запросов по категории (расходы)
        if category_filter and type_filter != "income":
            try:
                from core.praise import get_praise
                mem_db = os.environ.get("NOTION_DB_MEMORY")
                if mem_db:
                    limits = await _get_limits(mem_db)
                    link = _cat_link(category_filter)
                    limit_val: Optional[float] = None
                    for key, val in limits.items():
                        if key in link or link in key:
                            limit_val = val
                            break
                    if limit_val:
                        pct = total / limit_val * 100
                        remaining = limit_val - total
                        if pct > 100:
                            over = total - limit_val
                            lines.append(
                                f"\n📊 Лимит: {total:,.0f}₽ / {limit_val:,.0f}₽ ({pct:.0f}%)"
                                f"\n😬 Превышен на {over:,.0f}₽ — постараемся уложиться в следующем месяце"
                            )
                        elif pct >= 80:
                            lines.append(
                                f"\n📊 Лимит: {total:,.0f}₽ / {limit_val:,.0f}₽ ({pct:.0f}%)"
                                f"\n⚠️ Осталось {remaining:,.0f}₽ — почти весь бюджет"
                            )
                        else:
                            praise = get_praise("finance_under_limit")
                            lines.append(
                                f"\n📊 Лимит: {total:,.0f}₽ / {limit_val:,.0f}₽ ({pct:.0f}%)"
                                f"\n✅ Осталось {remaining:,.0f}₽\n{praise}"
                            )
            except Exception as e:
                logger.debug("stats limit review: %s", e)

        if total > 0:
            advice = await _get_finance_advice("\n".join(lines))
            if advice:
                lines.append(advice)

        return await _stats_publish(report_title, lines)

    # Общая сводка
    income_nexus_salary = 0.0
    income_arcana_salary = 0.0
    income_other = 0.0
    expense_total = 0.0
    cat_totals: dict = {}

    for r in records:
        props = r["properties"]
        amount = props.get("Сумма", {}).get("number") or 0
        type_name = (props.get("Тип", {}).get("select") or {}).get("name", "")
        cat_name = (props.get("Категория", {}).get("select") or {}).get("name", "")
        bot_name = (props.get("Бот", {}).get("select") or {}).get("name", "")

        if "Доход" in type_name:
            if cat_name == "💰 Зарплата":
                if "Nexus" in bot_name:
                    income_nexus_salary += amount
                elif "Arcana" in bot_name:
                    income_arcana_salary += amount
                else:
                    income_other += amount
            else:
                income_other += amount
        elif "Расход" in type_name:
            expense_total += amount
            cat_totals[cat_name] = cat_totals.get(cat_name, 0.0) + amount

    income_total = income_nexus_salary + income_arcana_salary + income_other
    balance = income_total - expense_total

    report_title = f"Финансы — {now.strftime('%B %Y')}"

    salary_detail = ""
    if income_nexus_salary or income_arcana_salary:
        salary_detail = (
            f"  ☀️ Nexus: {income_nexus_salary:,.0f}₽"
            f" | 🌒 Arcana: {income_arcana_salary:,.0f}₽"
        )

    lines = [
        report_title,
        "",
        f"💰 Доходы: {income_total:,.0f}₽",
    ]
    if salary_detail:
        lines.append(salary_detail)
    lines += [
        f"💸 Расходы: {expense_total:,.0f}₽",
        f"{'🟢' if balance >= 0 else '🔴'} Баланс: {'+' if balance >= 0 else ''}{balance:,.0f}₽",
    ]
    # Топ категорий расходов
    if cat_totals:
        lines.append("")
        lines.append("<b>Топ категорий:</b>")
        for cat, amt in sorted(cat_totals.items(), key=lambda x: x[1], reverse=True)[:5]:
            lines.append(f"  <b>{cat}</b>: {amt:,.0f}₽")

    if expense_total > 0:
        advice = await _get_finance_advice("\n".join(lines))
        if advice:
            lines.append(advice)

    return await _stats_publish(report_title, lines)


async def _stats_publish(title: str, lines: List[str]) -> str:
    """Создать Notion-страницу отчёта (если настроена) или вернуть текст."""
    from core.config import config
    page_reports = config.nexus.page_reports
    if page_reports:
        url = await create_report_page(title, lines, page_reports)
        if url:
            return f"📊 <b>Отчёт готов:</b> <a href=\"{url}\">{title}</a>"

    # Fallback: форматированный текст с HTML
    out = []
    for line in lines:
        if not line:
            out.append("")
        elif line == lines[0]:  # title
            out.append(f"📊 <b>{line}</b>")
        elif line.startswith("💰") or line.startswith("💸") or line.startswith("🟢") or line.startswith("🔴"):
            out.append(f"<b>{line}</b>")
        else:
            out.append(line)
    return "\n".join(out)


# ── Budget: Period Helpers ────────────────────────────────────────────────────


async def _get_payday() -> int:
    """Get payday day from Memory. Default 1."""
    mem_db = os.environ.get("NOTION_DB_MEMORY")
    if not mem_db:
        return 1
    try:
        from core.notion_client import db_query
        pages = await db_query(mem_db, filter_obj={"and": [
            {"property": "Ключ", "rich_text": {"equals": "budget_payday"}},
            {"property": "Актуально", "checkbox": {"equals": True}},
        ]}, page_size=1)
        if pages:
            fact = pages[0].get("properties", {}).get("Текст", {}).get("title", [])
            text = fact[0]["plain_text"] if fact else ""
            m = re.search(r"(\d+)", text)
            if m:
                return int(m.group(1))
    except Exception:
        pass
    return 1


def _period_bounds(payday: int, previous: bool = False) -> Tuple[str, str]:
    """Calculate start/end of budget period. If previous=True, return the PREVIOUS period."""
    now = datetime.now(MOSCOW_TZ)
    if now.day >= payday:
        start = now.replace(day=payday, hour=0, minute=0, second=0, microsecond=0)
        if now.month == 12:
            end = datetime(now.year + 1, 1, payday, tzinfo=MOSCOW_TZ) - timedelta(days=1)
        else:
            end = datetime(now.year, now.month + 1, payday, tzinfo=MOSCOW_TZ) - timedelta(days=1)
    else:
        if now.month == 1:
            start = datetime(now.year - 1, 12, payday, tzinfo=MOSCOW_TZ)
        else:
            start = datetime(now.year, now.month - 1, payday, tzinfo=MOSCOW_TZ)
        end = now.replace(day=payday, hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)

    if previous:
        # Shift back one period: end = start - 1 day, start = one month before start
        prev_end = start - timedelta(days=1)
        prev_start_month = start.month - 1 if start.month > 1 else 12
        prev_start_year = start.year if start.month > 1 else start.year - 1
        prev_start = datetime(prev_start_year, prev_start_month, payday, tzinfo=MOSCOW_TZ)
        return prev_start.strftime("%Y-%m-%d"), prev_end.strftime("%Y-%m-%d")

    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


# ── Budget: Debt/Goal/Limit Helpers ──────────────────────────────────────────


def _parse_k_amount(s: str) -> int:
    """Parse amount with к/k suffix: '10к' -> 10000."""
    s = s.strip().lower().replace(" ", "")
    if s.endswith("к") or s.endswith("k"):
        return int(float(s[:-1]) * 1000)
    return int(float(s))


def _recalc_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔄 Пересчитать бюджет", callback_data="budget_recalc_full"),
        InlineKeyboardButton(text="❌ Не надо", callback_data="msg_hide"),
    ]])


async def _deactivate_debt(name: str, user_notion_id: str = "") -> bool:
    """Deactivate a debt in Memory. Returns True if found."""
    mem_db = os.environ.get("NOTION_DB_MEMORY")
    if not mem_db:
        return False
    key_hint = name.lower().replace(" ", "_")
    from core.notion_client import db_query, update_page
    existing = await db_query(mem_db, filter_obj={"and": [
        {"property": "Ключ", "rich_text": {"contains": "долг_" + key_hint}},
        {"property": "Актуально", "checkbox": {"equals": True}},
    ]}, page_size=5)
    if existing:
        for page in existing:
            await update_page(page["id"], {"Актуально": {"checkbox": False}})
        return True
    return False


async def _partial_debt_payment(name: str, payment: int, user_notion_id: str = "") -> Optional[int]:
    """Reduce debt by payment. Returns new remaining or None."""
    mem_db = os.environ.get("NOTION_DB_MEMORY")
    if not mem_db:
        return None
    key_hint = name.lower().replace(" ", "_")
    from core.notion_client import db_query, update_page
    existing = await db_query(mem_db, filter_obj={"and": [
        {"property": "Ключ", "rich_text": {"contains": "долг_" + key_hint}},
        {"property": "Актуально", "checkbox": {"equals": True}},
    ]}, page_size=1)
    if not existing:
        return None
    page = existing[0]
    fact_parts = page.get("properties", {}).get("Текст", {}).get("title", [])
    fact = fact_parts[0]["plain_text"] if fact_parts else ""
    m = re.search(r'(\d[\d\s]*(?:[.,]\d+)?)\s*[₽р]', fact)
    if not m:
        return None
    current = int(re.sub(r'[\s.,]', '', m.group(1)))
    new_amount = max(0, current - payment)
    if new_amount == 0:
        await update_page(page["id"], {"Актуально": {"checkbox": False}})
        return 0
    # Update fact text with new amount
    new_fact = re.sub(r'(\d[\d\s]*(?:[.,]\d+)?)\s*[₽р]', f'{new_amount}₽', fact, count=1)
    await update_page(page["id"], {"Текст": _title(new_fact)})
    return new_amount


async def _deactivate_goal(name: str, user_notion_id: str = "") -> bool:
    """Deactivate a goal in Memory. Returns True if found."""
    mem_db = os.environ.get("NOTION_DB_MEMORY")
    if not mem_db:
        return False
    key_hint = name.lower().replace(" ", "_")
    from core.notion_client import db_query, update_page
    existing = await db_query(mem_db, filter_obj={"and": [
        {"property": "Ключ", "rich_text": {"contains": "цель_" + key_hint}},
        {"property": "Актуально", "checkbox": {"equals": True}},
    ]}, page_size=5)
    if existing:
        for page in existing:
            await update_page(page["id"], {"Актуально": {"checkbox": False}})
        return True
    return False


async def _save_debt(name: str, amount: int, deadline: str, user_notion_id: str = "") -> None:
    """Create a new debt entry in Memory."""
    dl_part = f" · дедлайн: {deadline}" if deadline else ""
    await _save_memory_entry(
        f"долг_{name.lower().replace(' ', '_')}",
        f"долг: {name} — {amount}₽{dl_part}",
        user_notion_id,
    )


async def _save_goal(name: str, amount: int, user_notion_id: str = "") -> None:
    """Create a new goal entry in Memory."""
    await _save_memory_entry(
        f"цель_{name.lower().replace(' ', '_')}",
        f"цель: {name} — {amount}₽ · откладываю 0₽/мес",
        user_notion_id,
    )


async def handle_debt_command(message: Message, user_notion_id: str = "") -> None:
    """Handle debt operations: close, new, partial payment."""
    text = (message.text or "").strip()

    # "закрыла долг Вике" / "погасила долг Маше"
    close_m = re.search(r'(?:закрыла?|погасила?)\s+долг\s+(\S+)', text, re.I)
    if close_m:
        name = close_m.group(1)
        found = await _deactivate_debt(name, user_notion_id)
        if found:
            await message.answer(
                f"🎉 <b>Долг {name} закрыт!</b> Молодец, Кай!",
                reply_markup=_recalc_keyboard(), parse_mode="HTML",
            )
        else:
            await message.answer(f"🤔 Не нашла долг «{name}» в памяти.", parse_mode="HTML")
        return

    # "новый долг Маша 10к до июня"
    new_m = re.search(r'новый\s+долг\s+(\S+)\s+(\d+[кk]?\d*)\s*(?:до\s+(.+))?', text, re.I)
    if new_m:
        name = new_m.group(1)
        amount = _parse_k_amount(new_m.group(2))
        deadline = (new_m.group(3) or "").strip()
        await _save_debt(name, amount, deadline, user_notion_id)
        dl_str = f" до {deadline}" if deadline else ""
        await message.answer(
            f"📋 Записала долг: <b>{name} — {amount:,.0f}₽</b>{dl_str}",
            reply_markup=_recalc_keyboard(), parse_mode="HTML",
        )
        return

    # "отдала Вике 25к" / "погасила Маше 15к"
    partial_m = re.search(r'(?:отдала?|погасила?)\s+(\S+)\s+(\d+[кk]?\d*)', text, re.I)
    if partial_m:
        name = partial_m.group(1)
        amount = _parse_k_amount(partial_m.group(2))
        remaining = await _partial_debt_payment(name, amount, user_notion_id)
        if remaining is None:
            await message.answer(f"🤔 Не нашла долг «{name}» в памяти.", parse_mode="HTML")
        elif remaining == 0:
            await message.answer(
                f"🎉 <b>Долг {name} полностью закрыт!</b> Ты молодец!",
                reply_markup=_recalc_keyboard(), parse_mode="HTML",
            )
        else:
            await message.answer(
                f"💰 Внесла {amount:,.0f}₽ за долг {name}\n📋 Осталось: <b>{remaining:,.0f}₽</b>",
                reply_markup=_recalc_keyboard(), parse_mode="HTML",
            )
        return

    await message.answer("🤔 Не поняла команду. Примеры:\n<i>новый долг Маша 10к до июня\nзакрыла долг Вике\nотдала Вике 25к</i>", parse_mode="HTML")


async def handle_goal_command(message: Message, user_notion_id: str = "") -> None:
    """Handle goal operations: new, remove, achieved."""
    text = (message.text or "").strip()

    # "новая цель ноутбук 200к"
    new_m = re.search(r'новая\s+цель\s+(.+?)\s+(\d+[кk]?\d*)', text, re.I)
    if new_m:
        name = new_m.group(1).strip()
        amount = _parse_k_amount(new_m.group(2))
        await _save_goal(name, amount, user_notion_id)
        await message.answer(
            f"🎯 Цель: <b>{name} — {amount:,.0f}₽</b>",
            reply_markup=_recalc_keyboard(), parse_mode="HTML",
        )
        return

    # "убери цель ноутбук" / "достигла цель телефон" / "купила цель телефон"
    remove_m = re.search(r'(?:убери|достигла?|купила?)\s+цель\s+(\S+)', text, re.I)
    if remove_m:
        name = remove_m.group(1)
        found = await _deactivate_goal(name, user_notion_id)
        is_achieved = bool(re.search(r'(?:достигла?|купила?)', text, re.I))
        if found:
            if is_achieved:
                await message.answer(
                    f"🎉 <b>Цель «{name}» достигнута!</b> Молодец!",
                    reply_markup=_recalc_keyboard(), parse_mode="HTML",
                )
            else:
                await message.answer(
                    f"✅ Цель «{name}» убрана.",
                    reply_markup=_recalc_keyboard(), parse_mode="HTML",
                )
        else:
            await message.answer(f"🤔 Не нашла цель «{name}» в памяти.", parse_mode="HTML")
        return

    await message.answer("🤔 Не поняла команду. Примеры:\n<i>новая цель ноутбук 200к\nубери цель ноутбук\nдостигла цель телефон</i>", parse_mode="HTML")


async def handle_limit_override(message: Message, category: str, amount_str: str, user_notion_id: str = "") -> None:
    """User manually sets a limit: 'лимит привычки 15к'."""
    amount = _parse_k_amount(amount_str)
    # Resolve category to full Notion name
    from core.notion_client import match_select
    from core.config import config
    real_cat = await match_select(config.nexus.db_finance, "Категория", category)
    if not real_cat or real_cat == category:
        # Try fuzzy: capitalize
        real_cat = category.capitalize()
    link = _cat_link(real_cat)
    await _save_memory_entry(
        f"лимит_{link}",
        f"лимит: {real_cat} — {amount}₽/мес [ручной]",
        user_notion_id,
    )
    await message.answer(
        f"✅ Лимит <b>{real_cat}: {amount:,.0f}₽/мес</b> [ручной]",
        reply_markup=_recalc_keyboard(), parse_mode="HTML",
    )


# ── Budget: Impulse Overflow ─────────────────────────────────────────────────


async def _handle_impulse_overflow(category: str, overflow: float, message: Message,
                                    user_notion_id: str, period_start: str) -> None:
    """Auto-create impulse expense for overspend."""
    from core.notion_client import finance_add
    await finance_add(
        date=datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d"),
        amount=overflow,
        category="🎲 Импульсивные",
        type_="💸 Расход",
        source="💳 Карта",
        description=f"Превышение {category}: {overflow:.0f}₽",
        user_notion_id=user_notion_id,
    )


async def _calc_impulse_status(period_start: str, user_notion_id: str = "") -> Tuple[float, float]:
    """Calculate impulse budget limit and usage for period."""
    mem_db = os.environ.get("NOTION_DB_MEMORY")
    impulse_limit = 0.0
    if mem_db:
        limits = await _get_limits(mem_db)
        for k, v in limits.items():
            if "импульсивн" in k.lower():
                impulse_limit = v
                break
    if impulse_limit == 0:
        return 0.0, 0.0
    from core.config import config
    from core.notion_client import db_query
    now = datetime.now(MOSCOW_TZ)
    records = await db_query(config.nexus.db_finance, filter_obj={"and": [
        {"property": "Тип", "select": {"equals": "💸 Расход"}},
        {"property": "Категория", "select": {"equals": "🎲 Импульсивные"}},
        {"property": "Дата", "date": {"on_or_after": period_start}},
        {"property": "Дата", "date": {"on_or_before": now.strftime("%Y-%m-%d")}},
    ]}, page_size=200)
    impulse_used = sum((p.get("properties", {}).get("Сумма", {}).get("number") or 0) for p in records)
    return impulse_limit, impulse_used


# ── Budget Setup: One-Shot Free-Form ─────────────────────────────────────────

# ── Budget Setup State ───────────────────────────────────────────────────────

_BUDGET_VARIABLE_CATS = [
    "🚬 Привычки", "💅 Бьюти", "🚕 Транспорт", "🍜 Продукты",
    "🍱 Кафе/Доставка", "🏥 Здоровье", "👗 Гардероб", "📚 Хобби/Учеба",
]

_BUDGET_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../pending_budget.db")
_BUDGET_TTL = 3600  # 60 min


def _bdb() -> _sqlite3.Connection:
    con = _sqlite3.connect(_BUDGET_DB)
    con.execute(
        "CREATE TABLE IF NOT EXISTS budget_pending "
        "(uid INTEGER PRIMARY KEY, data TEXT, ts REAL)"
    )
    con.commit()
    return con


def _budget_set(uid: int, data: dict) -> None:
    with _bdb() as con:
        con.execute(
            "INSERT OR REPLACE INTO budget_pending (uid, data, ts) VALUES (?,?,?)",
            (uid, json.dumps(data, ensure_ascii=False), _time.time()),
        )


def _budget_get(uid: int) -> Optional[dict]:
    with _bdb() as con:
        row = con.execute(
            "SELECT data, ts FROM budget_pending WHERE uid=?", (uid,)
        ).fetchone()
    if not row:
        return None
    if _time.time() - row[1] > _BUDGET_TTL:
        _budget_del(uid)
        return None
    return json.loads(row[0])


def _budget_del(uid: int) -> None:
    with _bdb() as con:
        con.execute("DELETE FROM budget_pending WHERE uid=?", (uid,))

BUDGET_SONNET_SYSTEM = (
    "Ты финансовый аналитик. Пользователь — Кай, женщина с СДВГ.\n"
    "Тон: тёплый, поддерживающий, без менторства. Женский род.\n\n"
    "АЛГОРИТМ РАСЧЁТА (СТРОГО ПО ШАГАМ):\n"
    "Шаг 1: Доход − Фикс = Распределяемые\n"
    "Шаг 2: ДОЛГИ — ОДИН ДОЛГ ЗА РАЗ:\n"
    "  - Каждый долг имеет поле monthly_payment — это сумма которую Кай РЕАЛЬНО платит\n"
    "  - КЛЮЧЕВОЕ ПРАВИЛО: считай monthly_payment ТОЛЬКО для ПЕРВОГО долга по дедлайну!\n"
    "  - Остальные долги с monthly_payment > 0 — ПЕРЕНЕСИ на месяц после закрытия первого\n"
    "  - НЕ СКЛАДЫВАЙ все платежи одновременно!\n"
    "  - Пример: Вика 50к (апрель), Илья 20к/мес → в марте платёж = ТОЛЬКО Вика.\n"
    "    Илья начнёт С АПРЕЛЯ (после закрытия Вики)\n"
    "  - Если monthly_payment = 0 → НЕ вычитай (наследство, отложен)\n"
    "  - НИКОГДА не пересчитывай monthly_payment самостоятельно. Кай уже решила.\n"
    "  - total_debt_payment = monthly_payment ПЕРВОГО горящего долга\n"
    "  - В debts_monthly включить ТОЛЬКО текущий долг (первый по дедлайну с monthly_payment > 0)\n"
    "  - В queued_debts — все остальные долги (информативно, с пометкой 'с [месяц]')\n"
    "  - Остаток = распределяемые - total_debt_payment\n"
    "Шаг 3: Оценить остаток:\n"
    "  - Остаток >= 30000₽ → НОРМАЛЬНЫЙ МЕСЯЦ → один план\n"
    "  - Остаток < 30000₽ → ТЯЖЁЛЫЙ МЕСЯЦ → ДВА ВАРИАНТА (A и B)\n\n"
    "НОРМАЛЬНЫЙ МЕСЯЦ (один план):\n"
    "  Из остатка: лимиты по категориям → подушка → цели → импульсивные\n"
    "  is_tight_month: false, variant_a и variant_b: null\n\n"
    "ТЯЖЁЛЫЙ МЕСЯЦ (два варианта):\n"
    "  is_tight_month: true\n"
    "  variant_a: \"Платить по плану\"\n"
    "    - Все monthly_payment вычитаются\n"
    "    - Жёсткий но РЕАЛЬНЫЙ план из маленького остатка\n"
    "    - ДАЖЕ В ВАРИАНТЕ А соблюдай ЖЕЛЕЗНЫЕ минимумы:\n"
    "      🍜 Продукты — минимум 3,000₽ (человек должен есть!)\n"
    "      💅 Бьюти — минимум 3,000₽ (ногти = фикс)\n"
    "      🚕 Транспорт — минимум 1,500₽ (метро)\n"
    "      🎲 Импульсивные — минимум 1,000₽\n"
    "      Итого железные минимумы = 8,500₽\n"
    "      Привычки = остаток ПОСЛЕ железных минимумов.\n"
    "      Пример: остаток 12,716₽ → 12,716 - 8,500 = 4,216₽ на привычки.\n"
    "      Привычки — ЕДИНСТВЕННАЯ категория которая может быть ниже 10к в варианте А.\n"
    "      НИКОГДА не обнуляй продукты/бьюти/транспорт ради привычек!\n"
    "    - adhd_survival_plan: КОНКРЕТНЫЙ план как пережить месяц\n"
    "    - relief: когда станет легче\n"
    "  variant_b: \"Пересмотреть стратегию\"\n"
    "    - Предложить уменьшить monthly_payment\n"
    "    - Комфортный план из большего остатка\n"
    "    - creditor_script: что сказать кредитору (1 предложение)\n"
    "    - relief: когда закроется полностью\n\n"
    "РАСПРЕДЕЛЕНИЕ ЛИМИТОВ:\n"
    "  После вычета долгов — остаток распределяется по ВСЕМ жизненным категориям.\n"
    "  НИКОГДА не ставить 0₽ на продукты или транспорт.\n\n"
    "  АБСОЛЮТНЫЕ МИНИМУМЫ (НЕ может ниже):\n"
    "  🚬 Привычки — 10,000₽ (СДВГ, невозможно ниже)\n"
    "  🍜 Продукты — 3,000₽ (человек должен есть)\n"
    "  💅 Бьюти — 3,000₽ (ногти = фикс)\n"
    "  🚕 Транспорт — 1,500₽ (СПб, метро)\n"
    "  🎲 Импульсивные — 1,000₽ (СДВГ, дофамин)\n"
    "  Сумма минимумов = 18,500₽\n\n"
    "  Если остаток < 18,500₽ → ЖЁСТКИЙ вариант, но ВСЕГДА показывать план. Кай решит сама.\n"
    "  Если остаток >= 18,500₽ → распределить пропорционально:\n"
    "    1. Сначала выделить минимумы по всем обязательным категориям\n"
    "    2. Оставшееся → распределить между привычками, продуктами, и другими\n"
    "    3. Кафе, гардероб, хобби, прочее — могут быть 0₽ в тяжёлый месяц\n\n"
    "  БАЛАНС ПРИВЫЧКИ / ПРОДУКТЫ:\n"
    "  Продукты НИКОГДА не меньше привычек более чем в 2 раза.\n"
    "  products_min = max(3000, habits / 2)\n"
    "  Если привычки 14к → продукты минимум 7к.\n"
    "  Если привычки 10к → продукты минимум 5к.\n"
    "  Не засовывать ВСЁ лишнее в привычки — делить между привычками и продуктами.\n\n"
    "  СОВЕТ СДВГ при сокращении привычек:\n"
    "  Если привычки урезаны — дать КОНКРЕТНЫЙ совет в habit_strategy:\n"
    "  купить блок Chapman заранее, заменить колу водой 3 дня/нед, 1 монстр вместо 2.\n\n"
    "ОГРАНИЧЕНИЯ:\n"
    "- Лимит с пометкой [ручной] — НЕ ТРОГАТЬ, распределять остаток вокруг него\n"
    "- Коты = ФИКСИРОВАННЫЕ расходы (живые существа!)\n"
    "- Привычки: Chapman = СИГАРЕТЫ (не чай!). Детализируй в habit_strategy "
    "(сигареты, кола, монстр) но итоговый лимит = одна строка 🚬 Привычки\n"
    "- Импульсивные ВСЕГДА > 0 (мин 1000₽)\n"
    "- note/summary/habit_strategy — максимум 15 слов\n"
    "- relief_timeline обязательно если есть долги\n"
    "- НЕ выдумывать деньги, НЕ показывать отрицательные суммы\n"
    "- НИКОГДА не ставить ВСЕ лимиты в 0₽\n"
    "- Если savings_from_last_period > 0: 'В прошлом периоде сэкономила X₽ — молодец!' в summary\n\n"
    "Ответ: ТОЛЬКО JSON, без markdown, без пояснений.\n"
    "Схема JSON:\n"
    '{"income": [{"source": "X", "amount": N}], "income_total": N,\n'
    ' "fixed": [{"name": "X", "category": "X", "amount": N}], "fixed_total": N,\n'
    ' "distributable": N,\n'
    ' "debts_monthly": [{"name": "X", "total": N, "monthly": N, "deadline": "X", "strategy": "X"}],\n'
    ' "debts_monthly_total": N,\n'
    ' "queued_debts": [{"name": "X", "total": N, "deadline": "X", "strategy": "X"}],\n'
    ' "free_after_debts": N,\n'
    ' "is_tight_month": false,\n'
    ' "variant_a": null or {"label": "Платить по плану", "debt_payment": N, "remaining": N,\n'
    '   "limits": [{"category": "X", "amount": N}], "limits_total": N,\n'
    '   "impulse_budget": N, "savings": {"amount": N, "note": "X"},\n'
    '   "adhd_survival_plan": "КОНКРЕТНЫЙ план: что купить, где сэкономить, как не сорваться",\n'
    '   "relief": "С мая +50к свободных!",\n'
    '   "warning": "СДВГ-риск: привычки -30%, высокий риск срыва" or null},\n'
    ' "variant_b": null or {"label": "Уменьшить платежи", "debt_payment": N, "remaining": N,\n'
    '   "limits": [{"category": "X", "amount": N}], "limits_total": N,\n'
    '   "impulse_budget": N, "savings": {"amount": N, "note": "X"},\n'
    '   "creditor_script": "Что сказать кредитору",\n'
    '   "relief": "Долг закроется в мае"},\n'
    ' "savings": {"amount": N, "note": "X"},\n'
    ' "limits": [{"category": "X", "amount": N, "current": N, "change": "X", "manual": false}],\n'
    ' "limits_total": N, "impulse_budget": N,\n'
    ' "goals": [{"name": "X", "monthly": N, "total": N, "starts_after": "X or null"}],\n'
    ' "relief_timeline": "X", "summary": "X", "habit_strategy": "X"}\n'
    "ВАЖНО: При нормальном месяце variant_a=null, variant_b=null, заполнить limits/savings/impulse_budget на верхнем уровне.\n"
    "При тяжёлом месяце limits/savings/impulse_budget на верхнем уровне = null, "
    "заполнить ВНУТРИ variant_a и variant_b."
)


async def _build_sonnet_input(uid: int, user_notion_id: str) -> str:
    """Build full context JSON for Sonnet analysis."""
    budget = await _load_budget_data(user_notion_id)
    payday = await _get_payday()
    period_start, period_end = _period_bounds(payday)
    now = datetime.now(MOSCOW_TZ)

    from core.config import config
    from core.notion_client import db_query
    records = await db_query(config.nexus.db_finance, filter_obj={"and": [
        {"property": "Дата", "date": {"on_or_after": period_start}},
        {"property": "Дата", "date": {"on_or_before": now.strftime("%Y-%m-%d")}},
    ]}, page_size=500)

    spending_by_cat = {}
    income_total = 0
    for r in records:
        props = r.get("properties", {})
        amt = props.get("Сумма", {}).get("number") or 0
        cat_sel = props.get("Категория", {}).get("select")
        cat = cat_sel.get("name", "Прочее") if cat_sel else "Прочее"
        type_sel = props.get("Тип", {}).get("select")
        type_name = type_sel.get("name", "") if type_sel else ""
        if "Доход" in type_name:
            income_total += amt
        else:
            spending_by_cat[cat] = spending_by_cat.get(cat, 0) + amt

    state = _budget_get(uid) or {}
    user_messages = "\n".join(state.get("buf", []))

    # Collect manual limits
    manual_limits = {}
    for item in budget.get("лимиты", []):
        fact = item.get("fact", "")
        if "[ручной]" in fact:
            m = _LIMIT_FACT_RE.search(fact)
            if m:
                manual_limits[m.group(1).strip()] = True

    # Savings from last period review (if available)
    savings_bonus = state.get("savings_from_last_period", 0)

    # Build debts with monthly_payment for Sonnet
    debts_for_sonnet = []
    for d in budget.get("долги", []):
        debts_for_sonnet.append({
            "name": d.get("name", "?"),
            "total": d.get("amount", 0),
            "deadline": d.get("deadline", ""),
            "strategy": d.get("strategy", ""),
            "monthly_payment": d.get("monthly_payment", 0),
        })

    # Also check state for debt_strategies from dialog
    if state.get("debt_strategies"):
        strategy_map = {s["name"].lower(): s for s in state["debt_strategies"]}
        for ds in debts_for_sonnet:
            name_lower = ds["name"].lower()
            if name_lower in strategy_map and not ds["strategy"]:
                matched = strategy_map[name_lower]
                ds["strategy"] = matched.get("strategy", "")
                ds["monthly_payment"] = matched.get("monthly_payment", 0)

    context = {
        "user_messages": user_messages,
        "current_date": now.strftime("%d.%m.%Y"),
        "period": f"{period_start} — {period_end}",
        "payday": payday,
        "income_this_period": income_total,
        "savings_from_last_period": savings_bonus,
        "income_from_memory": budget.get("доходы", []),
        "obligatory": budget.get("обязательные", []),
        "debts": debts_for_sonnet,
        "goals": budget.get("цели", []),
        "current_limits": budget.get("лимиты", []),
        "manual_limits": manual_limits,
        "spending_by_category": spending_by_cat,
        "finance_categories": CATEGORIES,
    }
    return json.dumps(context, ensure_ascii=False, indent=2)


_BUDGET_PARSE_PROMPT_LEGACY = """Финансовый советник. Пользователь — Кай, женщина с СДВГ (женский род).
Тон: тёплый, поддерживающий, без менторства.

КОНТЕКСТ: Chapman = СИГАРЕТЫ (не чай!). СДВГ → резкие ограничения = срыв.
Текущая дата: {current_date}.

Входные данные: {all_messages}
Категории финансов: {finance_categories}

РАСЧЁТ:
1. ДОХОД — все источники. "к"=тысяч, "млн"=миллионов, "в год"→/12. Диапазон→верхняя граница.
2. ФИКС = жилье+коммуналка+подписки+интернет+вода+коты(живые существа). Не трогать.
3. РАСПРЕДЕЛЯЕМЫЕ = доход - фикс.
4. ДОЛГИ — использовать ТОЛЬКО monthly_payment из данных:
   - Каждый долг имеет поле monthly_payment — это сумма которую Кай РЕАЛЬНО платит
   - КЛЮЧЕВОЕ ПРАВИЛО: считай ТОЛЬКО ПЕРВЫЙ долг по дедлайну!
   - Остальные долги с monthly_payment > 0 — перенеси на месяц после закрытия первого
   - НЕ СКЛАДЫВАЙ все платежи одновременно!
   - Если monthly_payment = 0 → НЕ вычитай (наследство, отложен)
   - НИКОГДА не пересчитывай monthly_payment. Кай уже решила.
   - total_debt_payment = monthly_payment ПЕРВОГО горящего долга
   - В debts_monthly — ТОЛЬКО текущий долг (первый по дедлайну)
   - В queued_debts — все остальные (с пометкой "с [месяц]")
5. Остаток = распределяемые - total_debt_payment
6. Остаток >= 30к → НОРМАЛЬНЫЙ МЕСЯЦ (is_tight_month: false)
   Остаток < 30к → ТЯЖЁЛЫЙ МЕСЯЦ (is_tight_month: true) → ДВА ВАРИАНТА

РАСПРЕДЕЛЕНИЕ ЛИМИТОВ:
После вычета долгов — остаток по ВСЕМ жизненным категориям.
НИКОГДА не ставить 0₽ на продукты или транспорт.

АБСОЛЮТНЫЕ МИНИМУМЫ (НЕ может ниже):
🚬 Привычки — 10,000₽ (СДВГ, невозможно ниже)
🍜 Продукты — 3,000₽ (человек должен есть)
💅 Бьюти — 3,000₽ (ногти = фикс)
🚕 Транспорт — 1,500₽ (СПб, метро)
🎲 Импульсивные — 1,000₽ (СДВГ, дофамин)
Сумма минимумов = 18,500₽

Если остаток < 18,500₽ → ЖЁСТКИЙ вариант, но ВСЕГДА показывать. Кай решит сама.
Если остаток >= 18,500₽ → распределить пропорционально:
1. Минимумы по всем обязательным
2. Остаток → распределить между привычками, продуктами, и другими
3. Кафе, гардероб, хобби — могут быть 0₽

БАЛАНС ПРИВЫЧКИ / ПРОДУКТЫ:
Продукты НИКОГДА не меньше привычек более чем в 2 раза.
products_min = max(3000, habits / 2)
Если привычки 14к → продукты минимум 7к.
Не засовывать ВСЁ лишнее в привычки.

ТЯЖЁЛЫЙ МЕСЯЦ:
variant_a: "Платить по плану" — все monthly_payment
  ДАЖЕ В ВАРИАНТЕ А — железные минимумы: продукты 3к, бьюти 3к, транспорт 1.5к, импульсивные 1к = 8.5к.
  Привычки = остаток после железных минимумов. ТОЛЬКО привычки могут быть < 10к.
  НИКОГДА не обнуляй продукты/бьюти/транспорт ради привычек!
variant_b: "Уменьшить платежи" — предложить снизить

ПРАВИЛА:
- Привычки (сигареты+кола+энергетики) = ОДНА категория "🚬 Привычки". НЕ разбивать!
- Коты = фиксированные расходы (живые существа!)
- Цели: если 0₽/мес → "после долгов (месяц год)"
- note/summary/habit_strategy — макс 15 слов

Ответ: ТОЛЬКО JSON, без markdown.
Схема JSON:
{{"income": [{{"source": "X", "amount": N}}], "income_total": N,
 "fixed": [{{"name": "X", "category": "X", "amount": N}}], "fixed_total": N,
 "distributable": N,
 "debts_monthly": [{{"name": "X", "total": N, "monthly": N, "deadline": "X", "strategy": "X"}}],
 "debts_monthly_total": N,
 "queued_debts": [{{"name": "X", "total": N, "deadline": "X", "strategy": "X"}}],
 "free_after_debts": N,
 "is_tight_month": false,
 "variant_a": null or {{"viable": true, "label": "X", "debt_payment": N, "remaining": N,
   "limits": [{{"category": "X", "amount": N}}], "limits_total": N,
   "impulse_budget": N, "savings": {{"amount": N, "note": "X"}},
   "adhd_survival_plan": "X", "relief": "X", "warning": "X or null"}},
 "variant_b": null or {{"label": "X", "debt_payment": N, "remaining": N,
   "limits": [{{"category": "X", "amount": N}}], "limits_total": N,
   "impulse_budget": N, "savings": {{"amount": N, "note": "X"}},
   "creditor_script": "X", "relief": "X"}},
 "savings": {{"amount": N, "note": "X"}},
 "limits": [{{"category": "X", "amount": N, "current": N, "change": "X", "manual": false}}],
 "limits_total": N, "impulse_budget": N,
 "goals": [{{"name": "X", "monthly": N, "total": N, "starts_after": "X or null"}}],
 "relief_timeline": "X", "summary": "X", "habit_strategy": "X"}}
ВАЖНО: При нормальном месяце variant_a=null, variant_b=null, заполнить limits/savings/impulse_budget на верхнем уровне.
При тяжёлом — limits/savings/impulse_budget на верхнем уровне = null, заполнить ВНУТРИ variant_a и variant_b."""


_DEBT_EXTRACT_HAIKU_PROMPT = """Извлеки долги из текста пользователя. Ищи паттерны:
- "долг вике 50к до апреля" → name=Вика, amount=50000, deadline=апрель 2026
- "дядя 100к август" → name=Дядя, amount=100000, deadline=август 2026
- "должна илье 40к до сентября" → name=Илья, amount=40000, deadline=сентябрь 2026
"к" = тысяч. Если год не указан — текущий (2026) или следующий.
Текст: {user_text}
Верни ТОЛЬКО JSON массив (без markdown). Если долгов нет — верни [].
[{{"name": "Вика", "amount": 50000, "deadline": "апрель 2026"}}]"""


_DEBT_STRATEGY_HAIKU_PROMPT = """Определи стратегию погашения для каждого долга из текста пользователя.
Варианты:
- Конкретный платёж: "по 20к в месяц" → monthly_payment = 20000
- Разовый: "закрою в апреле" → monthly_payment = вся сумма долга
- Рассрочка: "25к + 25к" → monthly_payment = 25000 (первый платёж)
- Наследство/подарок: "наследством" → monthly_payment = 0
- Отложен: "после вики", "потом" → monthly_payment = 0
- Неизвестно: "хз" → monthly_payment = сумма / месяцев до дедлайна

Долги:
{debts_list}

Текст пользователя: {user_text}

Верни ТОЛЬКО JSON массив (без markdown):
[{{"name": "Вика", "strategy": "25к апрель + 25к май", "monthly_payment": 25000}}]"""


def _format_debts_for_strategy_question(debts: list) -> str:
    """Format parsed debts for the strategy question message."""
    lines = []
    for d in debts:
        dl = d.get("deadline", "")
        dl_part = " · {}".format(dl) if dl else ""
        lines.append("{} — {:,.0f}₽{}".format(
            d.get("name", "?"), d.get("amount", 0), dl_part))
    return "\n".join(lines)


def _format_debts_for_haiku(debts: list) -> str:
    """Format debts list for the Haiku strategy prompt."""
    lines = []
    for d in debts:
        dl = d.get("deadline", "?")
        lines.append("- {} — {:,.0f}₽, дедлайн: {}".format(
            d.get("name", "?"), d.get("amount", 0), dl))
    return "\n".join(lines)


async def _extract_debts_from_text(user_text: str) -> list:
    """Use Haiku to extract debts from freeform user text."""
    from core.config import config as _cfg
    prompt = _DEBT_EXTRACT_HAIKU_PROMPT.format(user_text=user_text)
    try:
        raw = await ask_claude(prompt, model=_cfg.model_haiku, max_tokens=512)
        raw = raw.strip()
        json_match = re.search(r'\[[\s\S]*\]', raw)
        if json_match:
            raw = json_match.group(0)
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return parsed
    except Exception as e:
        logger.error("_extract_debts_from_text failed: %s", e)
    return []


async def _parse_debt_strategy_with_haiku(debts: list, user_text: str) -> list:
    """Use Haiku to parse user's free-text debt strategy into structured data."""
    from core.config import config as _cfg
    prompt = _DEBT_STRATEGY_HAIKU_PROMPT.format(
        debts_list=_format_debts_for_haiku(debts),
        user_text=user_text,
    )
    try:
        raw = await ask_claude(prompt, model=_cfg.model_haiku, max_tokens=1024)
        raw = raw.strip()
        json_match = re.search(r'\[[\s\S]*\]', raw)
        if json_match:
            raw = json_match.group(0)
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return parsed
    except Exception as e:
        logger.error("_parse_debt_strategy_with_haiku failed: %s", e)
    # Fallback: default strategy for each debt
    result = []
    for d in debts:
        amt = d.get("amount", 0)
        dl = d.get("deadline", "")
        months = _months_until(dl) or 1
        result.append({
            "name": d.get("name", "?"),
            "strategy": "{}₽/мес".format(int(amt / months)),
            "monthly_payment": int(amt / months),
        })
    return result


def _months_until(deadline_str: str) -> int:
    """Parse 'апрель 2026' -> months from now. Returns 0 if can't parse."""
    if not deadline_str:
        return 0
    _RU_MONTH_NUM = {
        "январ": 1, "феврал": 2, "март": 3, "апрел": 4,
        "май": 5, "мая": 5, "июн": 6, "июл": 7, "август": 8,
        "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
    }
    dl_lower = deadline_str.strip().lower()
    month_num = 0
    for prefix, num in _RU_MONTH_NUM.items():
        if dl_lower.startswith(prefix) or prefix in dl_lower:
            month_num = num
            break
    if not month_num:
        return 0
    year_m = re.search(r'20\d\d', dl_lower)
    year = int(year_m.group(0)) if year_m else datetime.now(MOSCOW_TZ).year
    now = datetime.now(MOSCOW_TZ)
    months = (year - now.year) * 12 + (month_num - now.month)
    return max(months, 0)


# ── Start / Collect / Finish ─────────────────────────────────────────────────

async def start_budget_analysis(message: Message, user_notion_id: str = "") -> None:
    """v3.0: /budget shows SAVED plan from Memory. Recalc only via button."""
    uid = message.from_user.id
    budget = await _load_budget_data(user_notion_id)
    has_data = budget.get("лимиты") or budget.get("обязательные")

    if not has_data:
        await start_budget_setup(message, user_notion_id)
        return

    # Has data → show saved plan with progress (NO Sonnet call)
    budget_text = await build_budget_message(user_notion_id)
    if not budget_text:
        await start_budget_setup(message, user_notion_id)
        return

    # Store notion_uid for callbacks
    state = _budget_get(uid) or {}
    state["notion_uid"] = user_notion_id
    _budget_set(uid, state)

    buttons = [[
        InlineKeyboardButton(text="🔄 Пересчитать", callback_data="budget_recalc_full"),
        InlineKeyboardButton(text="📋 Стратегия долгов", callback_data="bsetup_change_strategy"),
    ], [
        InlineKeyboardButton(text="✏️ Изменить данные", callback_data="bsetup_adjust"),
    ]]
    await message.answer(
        budget_text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


async def start_budget_setup(message: Message, user_notion_id: str = "") -> None:
    """Начать сбор данных для бюджета (one-shot)."""
    uid = message.from_user.id
    state = {"buf": [], "notion_uid": user_notion_id, "state": "collecting"}
    _budget_set(uid, state)

    sent = await message.answer(
        "💰 <b>Давай настроим бюджет!</b>\n\n"
        "Напиши всё что знаешь о своих финансах — я сам разберу.\n"
        "Можно в свободной форме, например:\n\n"
        "<i>зп 100к, аренда 15к\n"
        "квартира 20к, коммуналка 7к, своя кв 4к\n"
        "вода 1500, интернет 950\n"
        "клод 9500, спотифай 170, впн 500, тг 170\n"
        "коты: корм 5к, наполнитель 2500, влажный 500\n"
        "привычки 15-20к, бьюти 12к, транспорт 3-4к\n"
        "долги: вика 50к до апреля, илья 40к до августа\n"
        "цели: телефон 100к, подушка 200к</i>\n\n"
        "Пиши одним сообщением — я сразу посчитаю.",
        parse_mode="HTML",
    )
    state["msg_id"] = sent.message_id
    _budget_set(uid, state)


async def handle_budget_setup_text(message: Message, user_notion_id: str = "") -> bool:
    """Перехват текста во время настройки бюджета. Возвращает True если обработано."""
    uid = message.from_user.id
    state = _budget_get(uid)
    if not state:
        return False

    # Если есть план и пользователь пишет текст — считать как корректировку
    plan = state.get("plan")
    if plan:
        if plan.get("_adjusting"):
            return await _handle_adjust_text(message, uid)
        # План уже показан, новое сообщение = корректировка -> пересчитать
        text = (message.text or "").strip()
        if text and text.lower() not in ("отмена", "cancel", "стоп"):
            buf = state.get("buf", [])
            buf.append("КОРРЕКТИРОВКА: " + text)
            state["buf"] = buf
            _budget_set(uid, state)
            try:
                await message.react([{"type": "emoji", "emoji": "✏️"}])
            except Exception:
                pass
            await _run_budget_analysis(message, uid)
            return True

    cur_state = state.get("state", "")
    if cur_state not in ("collecting", "adjusting", "awaiting_debt_strategy"):
        return False

    text = (message.text or "").strip()
    if not text:
        return False

    if text.lower() in ("отмена", "cancel", "стоп"):
        _budget_del(uid)
        await message.answer("❌ Настройка бюджета отменена.")
        return True

    # ── Awaiting debt strategy response ──
    if cur_state == "awaiting_debt_strategy":
        try:
            await message.react([{"type": "emoji", "emoji": "👀"}])
        except Exception:
            pass
        pending_debts = state.get("pending_debts", [])
        strategies = await _parse_debt_strategy_with_haiku(pending_debts, text)
        # Merge strategies into debts and store in buf
        debt_strategy_text = []
        for s in strategies:
            debt_strategy_text.append(
                "СТРАТЕГИЯ ДОЛГА: {} — стратегия: {}, платёж: {}₽/мес".format(
                    s.get("name", "?"), s.get("strategy", "?"), s.get("monthly_payment", 0)))
        buf = state.get("buf", [])
        buf.extend(debt_strategy_text)
        state["buf"] = buf
        state["debt_strategies"] = strategies
        state["state"] = "collecting"
        del state["pending_debts"]
        _budget_set(uid, state)
        await _run_budget_analysis(message, uid)
        return True

    # Получил данные -> проверить долги, если > 1 спросить стратегию
    buf = state.get("buf", [])
    buf.append(text)
    state["buf"] = buf
    _budget_set(uid, state)
    try:
        await message.react([{"type": "emoji", "emoji": "👀"}])
    except Exception:
        pass

    # Check for debts: first from Notion Memory, then from user text
    notion_uid = state.get("notion_uid", "")
    budget_data = await _load_budget_data(notion_uid)
    existing_debts = budget_data.get("долги", [])
    has_strategies = any(d.get("strategy") for d in existing_debts)

    if has_strategies:
        # Strategies already in Memory — run analysis directly
        await _run_budget_analysis(message, uid)
        return True

    # If no debts in Memory — try to extract from user text with Haiku
    debts_to_check = existing_debts
    if not debts_to_check:
        all_text = "\n".join(state.get("buf", []))
        debts_to_check = await _extract_debts_from_text(all_text)
        logger.info("handle_budget_setup_text: extracted %d debts from text", len(debts_to_check))

    if len(debts_to_check) > 1:
        # Multiple debts without strategies — ask
        state["state"] = "awaiting_debt_strategy"
        state["pending_debts"] = debts_to_check
        _budget_set(uid, state)
        debts_text = _format_debts_for_strategy_question(debts_to_check)
        await message.answer(
            "📋 <b>Нашла долги:</b>\n{}\n\n"
            "Как планируешь отдавать? Напиши своими словами.\n"
            "<i>Например: «Вику закрою в апреле, дядю наследством, Илье по 20к с мая»</i>".format(debts_text),
            parse_mode="HTML",
        )
        return True

    if len(debts_to_check) == 1:
        # Single debt — auto-calculate: amount / months_until_deadline
        d = debts_to_check[0]
        months = _months_until(d.get("deadline", "")) or 1
        mp = int(d.get("amount", 0) / months)
        state["debt_strategies"] = [{
            "name": d.get("name", "?"),
            "strategy": "{:,}₽/мес".format(mp),
            "monthly_payment": mp,
        }]
        buf = state.get("buf", [])
        buf.append("СТРАТЕГИЯ ДОЛГА: {} — {:,}₽/мес".format(d.get("name", "?"), mp))
        state["buf"] = buf
        _budget_set(uid, state)

    # Run analysis
    await _run_budget_analysis(message, uid)
    return True


@router.callback_query(F.data.in_({"bsetup_change_strategy", "budget_change_strategy"}))
async def on_budget_change_strategy(call: CallbackQuery) -> None:
    """Re-ask debt strategy."""
    uid = call.from_user.id
    state = _budget_get(uid)
    if not state:
        await call.answer("⚠️ Сессия устарела — /budget заново", show_alert=True)
        return
    await call.answer("📋 Меняем стратегию...")
    notion_uid = state.get("notion_uid", "")
    budget_data = await _load_budget_data(notion_uid)
    debts = budget_data.get("долги", [])
    if not debts:
        await call.message.answer("Долгов не найдено.")
        return
    state["state"] = "awaiting_debt_strategy"
    state["pending_debts"] = debts
    state["msg_id"] = call.message.message_id
    _budget_set(uid, state)
    debts_text = _format_debts_for_strategy_question(debts)
    await call.message.answer(
        "📋 <b>Долги:</b>\n{}\n\n"
        "Как планируешь отдавать? Напиши своими словами.\n"
        "<i>Например: «Вику закрою в апреле, дядю наследством, Илье по 20к с мая»</i>".format(debts_text),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "bsetup_accept")
async def on_budget_accept(call: CallbackQuery) -> None:
    """Принять план -> сохранить."""
    uid = call.from_user.id
    state = _budget_get(uid)
    if not state or not state.get("plan"):
        await call.answer("⚠️ Сессия устарела — /budget заново", show_alert=True)
        return
    await call.answer("💾 Сохраняю...")
    await _save_budget_plan(call.message, uid)


@router.callback_query(F.data == "bsetup_recalc")
async def on_budget_recalc(call: CallbackQuery) -> None:
    """Пересчитать план."""
    uid = call.from_user.id
    state = _budget_get(uid)
    if not state:
        await call.answer("⚠️ Сессия устарела — /budget заново", show_alert=True)
        return
    await call.answer("🔍 Пересчитываю...")
    state["msg_id"] = call.message.message_id
    _budget_set(uid, state)
    await _run_budget_analysis(call.message, uid)


@router.callback_query(F.data == "bsetup_adjust")
async def on_budget_adjust(call: CallbackQuery) -> None:
    """Режим корректировки."""
    uid = call.from_user.id
    state = _budget_get(uid)
    if not state or not state.get("plan"):
        await call.answer("⚠️ Сессия устарела — /budget заново", show_alert=True)
        return
    plan = state["plan"]
    plan["_adjusting"] = True
    state["plan"] = plan
    state["msg_id"] = call.message.message_id
    state["state"] = "adjusting"
    _budget_set(uid, state)
    await call.message.edit_text(
        "✏️ <b>Корректировка</b>\n\n"
        "Напиши что изменить в свободной форме:\n"
        "<i>привычки 15к, убери цель квартира, добавь цель отпуск 80к</i>\n\n"
        "Когда закончишь — нажми «Пересчитать».",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔄 Пересчитать", callback_data="bsetup_recalc"),
        ]]),
        parse_mode="HTML",
    )
    await call.answer()


@router.callback_query(F.data == "budget_recalc_full")
async def on_budget_recalc_full(call: CallbackQuery) -> None:
    """Full budget recalculation via Sonnet."""
    uid = call.from_user.id
    state = _budget_get(uid) or {}
    notion_uid = state.get("notion_uid", "")
    await call.answer("📊 Пересчитываю...")
    # Force Sonnet recalculation (not just show saved)
    state["buf"] = state.get("buf", [])
    state["notion_uid"] = notion_uid
    state["state"] = "analyzing"
    _budget_set(uid, state)
    await _run_budget_analysis(call.message, uid)


@router.callback_query(F.data.in_({"bsetup_variant_a", "bsetup_variant_b"}))
async def on_budget_variant_choice(call: CallbackQuery) -> None:
    """Кай выбрала вариант А или Б при тяжёлом месяце."""
    uid = call.from_user.id
    state = _budget_get(uid)
    if not state or not state.get("plan"):
        await call.answer("⚠️ Сессия устарела — /budget заново", show_alert=True)
        return
    plan = state["plan"]
    chosen_key = "variant_a" if call.data == "bsetup_variant_a" else "variant_b"
    variant = plan.get(chosen_key)
    if not variant:
        await call.answer("⚠️ Вариант не найден", show_alert=True)
        return

    label = "А" if chosen_key == "variant_a" else "Б"
    await call.answer("✅ Вариант {} выбран!".format(label))

    # Merge variant into top-level plan for saving
    plan["limits"] = variant.get("limits", [])
    plan["limits_total"] = variant.get("limits_total", 0)
    plan["impulse_budget"] = variant.get("impulse_budget", 0)
    plan["savings"] = variant.get("savings", {})
    plan["chosen_variant"] = chosen_key
    plan["debts_monthly_total"] = variant.get("debt_payment", plan.get("debts_monthly_total", 0))
    plan["free_after_debts"] = variant.get("remaining", 0)
    # Clear variants so _save_budget_plan uses top-level data
    plan["variant_a"] = None
    plan["variant_b"] = None
    plan["is_tight_month"] = False

    state["plan"] = plan
    state["state"] = "has_plan"
    _budget_set(uid, state)

    # Show chosen plan with accept button
    plan_text = "✅ <b>Вариант {} выбран</b>\n\n".format(label) + _format_plan(plan)
    buttons = [[
        InlineKeyboardButton(text="✅ Принять", callback_data="bsetup_accept"),
        InlineKeyboardButton(text="📋 Изменить стратегию", callback_data="bsetup_change_strategy"),
        InlineKeyboardButton(text="🔄 Пересчитать", callback_data="bsetup_recalc"),
    ]]
    try:
        await call.message.edit_text(
            plan_text, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )
    except Exception:
        await call.message.answer(
            plan_text, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )


@router.callback_query(F.data.startswith("bsetup_prio_"))
async def on_budget_priority_goal(call: CallbackQuery) -> None:
    """Выбор приоритетной цели — ТОЛЬКО сохранить в Память, НЕ пересчитывать."""
    uid = call.from_user.id
    state = _budget_get(uid)
    notion_uid = state.get("notion_uid", "") if state else ""
    idx = int(call.data.split("_")[-1])

    # Get goals from plan (may still be in state) or from saved data
    plan = state.get("plan", {}) if state else {}
    goals = plan.get("goals", [])
    if idx >= len(goals):
        await call.answer()
        return

    chosen = goals[idx]
    goal_name = chosen.get("name", "?")

    # Save priority to Memory — just a marker, no recalculation
    if notion_uid:
        await _save_memory_entry(
            "goal_priority",
            "приоритет цели: {}".format(goal_name),
            notion_uid,
        )

    # Cleanup state after goal selection
    if state:
        _budget_del(uid)

    await call.answer("🎯 Приоритет: {}".format(goal_name))
    try:
        # Remove goal buttons, show selection
        existing_text = call.message.html_text or call.message.text or ""
        # Remove the "Какая цель важнее?" line
        existing_text = existing_text.replace("🎯 <b>Какая цель важнее?</b>", "").strip()
        new_text = existing_text + "\n\n🎯 <b>Приоритет: {}</b>".format(goal_name)
        await call.message.edit_text(new_text, parse_mode="HTML")
    except Exception:
        pass


# ── Sonnet Analysis ──────────────────────────────────────────────────────────

async def _run_budget_analysis(message: Message, uid: int) -> None:
    """Собрать буфер, отправить Sonnet, показать план."""
    state = _budget_get(uid) or {}
    all_text = "\n".join(state.get("buf", []))
    notion_uid = state.get("notion_uid", "")

    # Удалить старое сообщение-инструкцию и отправить "считаю" НИЖЕ чата
    old_msg_id = state.get("msg_id", 0)
    if old_msg_id:
        try:
            await message.bot.delete_message(message.chat.id, old_msg_id)
        except Exception:
            pass

    loading = await message.answer(
        "🔍 <b>Анализирую бюджет...</b>\nSonnet считает оптимальный план.",
        parse_mode="HTML",
    )
    state["msg_id"] = loading.message_id
    _budget_set(uid, state)

    # Decide: use new BUDGET_SONNET_SYSTEM with full context, or legacy prompt for setup flow
    budget_data = await _load_budget_data(notion_uid)
    has_existing_data = any(budget_data.get(k) for k in budget_data)

    plan = None
    raw = ""
    try:
        from core.config import config as _cfg
        if has_existing_data:
            # Use new Sonnet system with full context from Memory + finance DB
            sonnet_input = await _build_sonnet_input(uid, notion_uid)
            raw = await ask_claude(sonnet_input, system=BUDGET_SONNET_SYSTEM, model=_cfg.model_sonnet, max_tokens=4096)
        else:
            # Legacy: setup flow with user messages only
            finance_cats_str = ", ".join(CATEGORIES) if CATEGORIES else "неизвестно"
            current_date = datetime.now(MOSCOW_TZ).strftime("%d.%m.%Y")
            prompt = _BUDGET_PARSE_PROMPT_LEGACY.format(
                all_messages=all_text, finance_categories=finance_cats_str, current_date=current_date,
            )
            raw = await ask_claude(prompt, model=_cfg.model_sonnet, max_tokens=4096)

        if not raw or not raw.strip():
            raise ValueError("Empty response from Sonnet")
        raw = raw.strip()
        logger.info("Sonnet budget raw response length: %d chars", len(raw))
        # Извлечь JSON
        json_match = re.search(r'\{[\s\S]*\}', raw)
        if json_match:
            raw = json_match.group(0)
        plan = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("Sonnet budget JSON parse failed: %s\nRaw (first 300): %s", e, raw[:300])
        try:
            fixed = raw.rstrip()
            open_braces = fixed.count("{") - fixed.count("}")
            open_brackets = fixed.count("[") - fixed.count("]")
            if open_braces > 0 or open_brackets > 0:
                fixed = fixed.rstrip(",\n ")
                fixed += "]" * max(0, open_brackets) + "}" * max(0, open_braces)
                plan = json.loads(fixed)
                logger.info("Sonnet budget JSON fixed by closing brackets")
        except Exception:
            pass
    except Exception as e:
        logger.error("Sonnet budget analysis failed: %s", e)

    if plan is None:
        await loading.edit_text(
            "⚠️ Не удалось получить анализ. Попробуй ещё раз.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="bsetup_recalc"),
            ]]),
        )
        return

    state["plan"] = plan
    state["state"] = "has_plan"
    _budget_set(uid, state)

    # Показать план + кнопки выбора приоритетной цели (если есть)
    plan_text = _format_plan(plan)

    is_tight = plan.get("is_tight_month", False) and plan.get("variant_a") and plan.get("variant_b")
    free_after = plan.get("free_after_debts", 0)

    # Дефицит / мало — предупреждение, но ВСЕГДА показываем план + кнопки
    if free_after < 0:
        plan_text += "\n\n⚠️ <b>После платежей дефицит {:,}₽.</b>\n".format(abs(free_after))
        plan_text += "Можно пересмотреть стратегию долгов."
    elif 0 < free_after < 18500 and not is_tight:
        plan_text += "\n\n⚠️ <b>После платежей остаётся {:,}₽ — жёстко.</b>".format(free_after)

    if is_tight:
        # Тяжёлый месяц → ВСЕГДА оба варианта, Кай сама решит
        buttons = [[
            InlineKeyboardButton(text="🅰️ Вариант А", callback_data="bsetup_variant_a"),
            InlineKeyboardButton(text="🅱️ Вариант Б", callback_data="bsetup_variant_b"),
        ], [
            InlineKeyboardButton(text="📋 Изменить стратегию", callback_data="bsetup_change_strategy"),
            InlineKeyboardButton(text="🔄 Пересчитать", callback_data="bsetup_recalc"),
        ]]
    else:
        # Нормальный месяц → стандартные кнопки
        buttons = [[
            InlineKeyboardButton(text="✅ Принять", callback_data="bsetup_accept"),
            InlineKeyboardButton(text="✏️ Изменить", callback_data="bsetup_adjust"),
            InlineKeyboardButton(text="🔄 Пересчитать", callback_data="bsetup_recalc"),
        ]]

    try:
        await loading.edit_text(
            plan_text, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )
    except Exception:
        try:
            await message.bot.delete_message(message.chat.id, loading.message_id)
        except Exception:
            pass
        sent = await message.answer(
            plan_text, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )
        state["msg_id"] = sent.message_id
        _budget_set(uid, state)


def _format_limits_block(limits: list, limits_total: int = 0) -> list:
    """Format limits into lines."""
    lines = []
    if not limits:
        return lines
    total = limits_total or sum(l.get("amount", 0) for l in limits)
    lines.append("<b>📊 Лимиты: {:,}₽</b>".format(total))
    for l in limits:
        change = " ({})".format(l["change"]) if l.get("change") else ""
        amt = l.get("amount", 0)
        manual = " 🔒" if l.get("manual") else ""
        lines.append("  {} — {:,}₽{}{}".format(l.get("category", "?"), amt, change, manual))
    return lines


def _format_variant(v: dict, label: str) -> list:
    """Format a single variant (A or B) block."""
    lines = []
    lines.append("\n━━━ {} ━━━".format(label))
    remaining = v.get("remaining", 0)
    debt_pay = v.get("debt_payment", 0)
    if debt_pay:
        lines.append("📋 Платёж: {:,}₽ → остаётся {:,}₽".format(debt_pay, remaining))
    # Limits
    limits = v.get("limits", [])
    if limits:
        lines.append("")
        lines.extend(_format_limits_block(limits, v.get("limits_total", 0)))
    # Impulse
    impulse = v.get("impulse_budget", 0)
    if impulse:
        lines.append("\n🎲 Импульсивный: {:,}₽".format(impulse))
    # Savings
    savings = v.get("savings", {})
    if savings and savings.get("amount", 0) > 0:
        lines.append("💰 Подушка: {:,}₽/мес".format(savings["amount"]))
    # Warning (ADHD risk)
    if v.get("warning"):
        lines.append("\n⚠️ <b>{}</b>".format(v["warning"]))
    # ADHD survival plan
    if v.get("adhd_survival_plan"):
        lines.append("\n📋 <b>План как пережить месяц:</b>")
        lines.append("<i>{}</i>".format(v["adhd_survival_plan"]))
    # Creditor script
    if v.get("creditor_script"):
        lines.append("\n💬 <i>Сказать кредитору: «{}»</i>".format(v["creditor_script"]))
    # Relief
    if v.get("relief"):
        lines.append("\n📅 <i>{}</i>".format(v["relief"]))
    return lines


def _format_plan(plan: dict) -> str:
    """Форматирует Sonnet-план в красивое сообщение."""
    lines = ["<b>💰 Финансовый план</b>"]

    # Доход
    income_total = plan.get("income_total", 0)
    if income_total:
        lines.append("\n<b>📥 Доход: {:,}₽</b>".format(income_total))
        for i in plan.get("income", []):
            lines.append("  {} — {:,}₽".format(i.get("source", "?"), i.get("amount", 0)))

    # Фикс
    fixed = plan.get("fixed", [])
    if fixed:
        fixed_total = plan.get("fixed_total", sum(f.get("amount", 0) for f in fixed))
        lines.append("\n<b>🔒 Фикс: {:,}₽</b>".format(fixed_total))
        for f in fixed:
            cat_emoji = f.get("category", "").split()[0] if f.get("category") else "📌"
            lines.append("  {} {} — {:,}₽".format(cat_emoji, f.get("name", "?"), f.get("amount", 0)))

    # Распределяемые = Доход - Фикс (ВСЕГДА)
    income_total = plan.get("income_total", 0)
    fixed_total = plan.get("fixed_total", sum(f.get("amount", 0) for f in plan.get("fixed", [])))
    distributable = income_total - fixed_total if income_total > 0 else plan.get("distributable", 0)
    if distributable < 0:
        lines.append("\n⚠️ <b>Обязательные расходы ({:,}₽) превышают доход ({:,}₽). Проверь данные.</b>".format(
            fixed_total, income_total))
        return "\n".join(lines)
    if distributable:
        lines.append("\n💳 Распределяемые: <b>{:,}₽</b>".format(distributable))

    # Долги — все с пометкой стратегии
    debts_monthly = plan.get("debts_monthly", plan.get("debts", []))
    queued = plan.get("queued_debts", [])
    all_debts = debts_monthly + queued
    total_monthly_payments = 0
    if all_debts:
        lines.append("\n<b>📋 Долги:</b>")
        for d in debts_monthly:
            mon = d.get("monthly", 0)
            total_monthly_payments += mon
            strategy = d.get("strategy", "")
            strat_part = " ({})".format(strategy) if strategy else ""
            lines.append("  {} — {:,}₽ · платёж {:,}₽/мес{}".format(
                d.get("name", "?"), d.get("total", d.get("amount", 0)), mon, strat_part))
        for q in queued:
            strategy = q.get("strategy", "отложен")
            lines.append("  {} — {:,}₽ · {}".format(
                q.get("name", "?"), q.get("total", 0), strategy))
        if total_monthly_payments > 0:
            lines.append("💳 Всего платежей: <b>{:,}₽/мес</b>".format(total_monthly_payments))

    # ── ТЯЖЁЛЫЙ МЕСЯЦ: два варианта ──
    is_tight = plan.get("is_tight_month", False)
    variant_a = plan.get("variant_a")
    variant_b = plan.get("variant_b")

    if is_tight and variant_a and variant_b:
        free = plan.get("free_after_debts", 0)
        lines.append("\n⚠️ <b>После платежей остаётся {:,}₽ — тяжёлый месяц. Два варианта:</b>".format(
            max(free, 0)))
    else:
        # Свободные после долгов — только для нормального месяца
        free = plan.get("free_after_debts")
        if free is not None and free > 0:
            lines.append("\n💳 Свободных после долгов: <b>{:,}₽</b>".format(free))

    if is_tight and variant_a and variant_b:
        # ВСЕГДА показывать оба варианта — Кай сама решит
        remaining_a = variant_a.get("remaining", 0)
        if remaining_a < 18500:
            lines.extend(_format_variant(variant_a, "Вариант А: {} ⚠️ жёстко".format(
                variant_a.get("label", "Платить по плану"))))
            lines.append("\n⚠️ <i>Остаётся {:,}₽ — жёстко, но реально если готова.</i>".format(
                max(remaining_a, 0)))
        else:
            lines.extend(_format_variant(variant_a, "Вариант А: {}".format(
                variant_a.get("label", "Платить по плану"))))
        lines.extend(_format_variant(variant_b, "Вариант Б: {}".format(
            variant_b.get("label", "Рассрочка"))))
    else:
        # ── НОРМАЛЬНЫЙ МЕСЯЦ: один план ──
        # Подушка
        savings = plan.get("savings") or {}
        if savings and savings.get("amount", 0) > 0:
            lines.append("\n💰 Подушка: <b>{:,}₽/мес</b>".format(savings["amount"]))
            if savings.get("note"):
                lines.append("  <i>{}</i>".format(savings["note"]))

        # Лимиты
        limits = plan.get("limits", [])
        if limits:
            lines.append("")
            lines.extend(_format_limits_block(limits, plan.get("limits_total", 0)))

        # Импульсивный
        impulse = plan.get("impulse_budget", 0)
        if impulse:
            lines.append("\n<b>🎲 Импульсивный: {:,}₽</b>".format(impulse))
            lines.append("  <i>Резерв на превышения лимитов</i>")

    # Цели (всегда)
    goals = plan.get("goals", [])
    if goals:
        lines.append("\n<b>🎯 Цели:</b>")
        for g in goals:
            monthly = g.get("monthly", 0)
            total = g.get("total", 0)
            if monthly and monthly > 0:
                months = g.get("months", 0)
                time_str = " → {} мес".format(months) if months else ""
                lines.append("  {} — {:,}₽/мес{}".format(g.get("name", "?"), monthly, time_str))
            else:
                starts = g.get("starts_after", g.get("note", ""))
                if starts:
                    lines.append("  {} — {:,}₽ · после {}".format(g.get("name", "?"), total, starts))
                else:
                    lines.append("  {} — {:,}₽ · после закрытия долгов".format(g.get("name", "?"), total))

    # Timeline
    if plan.get("relief_timeline"):
        lines.append("\n📅 <i>{}</i>".format(plan["relief_timeline"]))

    # Summary + habits
    if plan.get("summary"):
        lines.append("\n💡 <i>{}</i>".format(plan["summary"]))
    if plan.get("habit_strategy"):
        lines.append("🚬 <i>{}</i>".format(plan["habit_strategy"]))

    return "\n".join(lines)


# ── Adjust mode ──────────────────────────────────────────────────────────────

async def _handle_adjust_text(message: Message, uid: int) -> bool:
    """Обработка текста в режиме корректировки. Добавляет в буфер."""
    text = (message.text or "").strip()
    if not text:
        return False

    # Добавить корректировку в буфер
    state = _budget_get(uid) or {}
    buf = state.get("buf", [])
    buf.append("КОРРЕКТИРОВКА: " + text)
    state["buf"] = buf
    _budget_set(uid, state)

    try:
        await message.react([{"type": "emoji", "emoji": "✏️"}])
    except Exception:
        pass

    # Убрать флаг — ждём нажатия "Пересчитать"
    return True


# ── Save Plan ────────────────────────────────────────────────────────────────

async def _save_budget_plan(message: Message, uid: int) -> None:
    """Сохранить принятый план в Notion Память."""
    state = _budget_get(uid) or {}
    plan = state.get("plan", {})
    notion_uid = state.get("notion_uid", "")
    bot_msg_id = state.get("msg_id", 0)

    logger.info("_save_budget_plan: uid=%s notion_uid=%s plan_keys=%s", uid, notion_uid[:8] if notion_uid else "none", list(plan.keys()))

    # Удаляем старое сообщение и шлём статус ниже
    if bot_msg_id:
        try:
            await message.bot.delete_message(message.chat.id, bot_msg_id)
        except Exception:
            pass
    loading = await message.answer("💾 <b>Сохраняю план...</b>", parse_mode="HTML")

    # Доходы — категория 🔒 Обязательные, ключ income_*
    for inc in plan.get("income", []):
        name = inc.get("source", inc.get("name", "?"))
        amt = inc.get("amount", 0)
        key = "income_{}".format(name.lower().replace(" ", "_"))
        text = "доход: {} — {}₽/мес".format(name, amt)
        await _save_memory_entry(key, text, notion_uid)

    # Фиксы
    for f in plan.get("fixed", []):
        cat = f.get("category", "📌 Прочее")
        name = f.get("name", "?")
        amt = f.get("amount", 0)
        await _save_memory_entry(
            "обязательно_{}".format(_cat_link(cat) + "_" + name.lower().replace(" ", "_")),
            "обязательно: {} ({}) — {}₽/мес".format(name, cat, amt),
            notion_uid,
        )

    # Лимиты — НЕ перезаписывать [ручной]
    existing_limits = await _get_limits(os.environ.get("NOTION_DB_MEMORY", ""))
    for l in plan.get("limits", []):
        cat = l.get("category", "📌 Прочее")
        amt = l.get("amount", 0)
        cat_key = _cat_link(cat)
        # Check if existing limit is manual — don't overwrite
        if l.get("manual"):
            manual_tag = " [ручной]"
        else:
            # Check existing memory for manual tag
            from core.notion_client import db_query as _dbq
            mem_db = os.environ.get("NOTION_DB_MEMORY", "")
            _skip = False
            if mem_db:
                try:
                    _existing = await _dbq(mem_db, filter_obj={"and": [
                        {"property": "Ключ", "rich_text": {"equals": "лимит_{}".format(cat_key)}},
                        {"property": "Бот", "select": {"equals": "☀️ Nexus"}},
                    ]}, page_size=1)
                    if _existing:
                        _fact = (_existing[0]["properties"].get("Текст", {}).get("title", [{}])[0].get("plain_text", ""))
                        if "[ручной]" in _fact:
                            _skip = True
                except Exception:
                    pass
            if _skip:
                continue
            manual_tag = ""
        await _save_memory_entry(
            "лимит_{}".format(cat_key),
            "лимит: {} — {}₽/мес{}".format(cat, amt, manual_tag),
            notion_uid,
        )

    # Импульсивный
    impulse = plan.get("impulse_budget", 0)
    if impulse:
        await _save_memory_entry(
            "лимит_импульсивный",
            "лимит: 🎲 Импульсивный — {}₽/мес".format(impulse),
            notion_uid,
        )

    # Подушка
    savings = plan.get("savings", {})
    if savings and savings.get("amount", 0) > 0:
        await _save_memory_entry(
            "цель_подушка",
            "цель: 💰 Подушка — 100000₽ · откладываю {}₽/мес".format(savings["amount"]),
            notion_uid,
        )

    # Долги (поддержка и debts_monthly и queued_debts)
    all_plan_debts = plan.get("debts_monthly", plan.get("debts", []))
    all_plan_debts += plan.get("queued_debts", [])
    # Also merge strategies from state
    debt_strategies = state.get("debt_strategies", [])
    strategy_map = {s["name"].lower(): s for s in debt_strategies} if debt_strategies else {}
    for d in all_plan_debts:
        name = d.get("name", "?")
        amt = d.get("total", d.get("amount", 0))
        dl = d.get("deadline", "")
        strategy = d.get("strategy", "")
        monthly = d.get("monthly", d.get("monthly_payment", 0))
        # Merge from strategy dialog if not in plan
        name_lower = name.lower()
        if not strategy and name_lower in strategy_map:
            strategy = strategy_map[name_lower].get("strategy", "")
            monthly = strategy_map[name_lower].get("monthly_payment", monthly)
        dl_part = " · дедлайн: {}".format(dl) if dl else ""
        strat_part = " · стратегия: {}".format(strategy) if strategy else ""
        mon_part = " · платёж: {}".format(int(monthly)) if monthly else ""
        await _save_memory_entry(
            "долг_{}".format(name_lower.replace(" ", "_")),
            "долг: {} — {}₽{}{}{}".format(name, int(amt), dl_part, strat_part, mon_part),
            notion_uid,
        )

    # Цели
    for g in plan.get("goals", []):
        name = g.get("name", "?")
        total = g.get("total", 0)
        monthly = g.get("monthly", 0)
        await _save_memory_entry(
            "цель_{}".format(name.lower().replace(" ", "_")),
            "цель: {} — {}₽ · откладываю {}₽/мес".format(name, total, monthly),
            notion_uid,
        )

    logger.info("_save_budget_plan: all entries saved, building budget message")

    # Build goal priority buttons — only for goals with monthly > 0 (not "после долгов")
    goals = plan.get("goals", [])
    active_goals = [g for g in goals if g.get("monthly", 0) > 0]
    goal_buttons_rows = []
    if len(active_goals) > 1:
        goal_btns = []
        for i, g in enumerate(goals[:6]):
            if g.get("monthly", 0) > 0:
                goal_btns.append(InlineKeyboardButton(
                    text="🎯 {}".format(g.get("name", "?")),
                    callback_data="bsetup_prio_{}".format(i),
                ))
        for j in range(0, len(goal_btns), 2):
            goal_buttons_rows.append(goal_btns[j:j+2])

    # Показать итоговый бюджет
    budget_msg = await build_budget_message(notion_uid)
    result_text = "✅ <b>План на {} принят и сохранён!</b>\n\n{}".format(
        _RU_MONTHS.get(datetime.now(MOSCOW_TZ).month, "месяц"),
        budget_msg or "Вызови /budget для просмотра.",
    )

    if active_goals and len(active_goals) > 1:
        result_text += "\n\n🎯 <b>Какая цель важнее?</b>"
    elif goals and not active_goals:
        # All goals are deferred
        result_text += "\n\n🎯 Цели стартуют после закрытия долгов"

    markup = InlineKeyboardMarkup(inline_keyboard=goal_buttons_rows) if goal_buttons_rows else None

    # Cleanup state AFTER showing result (keep plan for goal priority callback)
    if not goal_buttons_rows:
        _budget_del(uid)
    else:
        # Keep state briefly for goal callback
        state["state"] = "goal_priority"
        _budget_set(uid, state)

    try:
        await loading.edit_text(result_text, parse_mode="HTML", reply_markup=markup)
    except Exception:
        try:
            await message.answer(result_text, parse_mode="HTML", reply_markup=markup)
        except Exception:
            await message.answer("✅ План сохранён! Вызови /budget для просмотра.")


# ── Save to Memory ───────────────────────────────────────────────────────────


def _notion_cat_for_key(key: str) -> str:
    """Определить категорию Notion по префиксу ключа."""
    for prefix, cat in _BUDGET_KEY_TO_CATEGORY.items():
        if key.startswith(prefix):
            return cat
    return "💰 Лимит"


async def _save_memory_entry(key: str, fact: str, user_notion_id: str = "") -> None:
    """Сохранить или обновить запись в Памяти с правильной категорией."""
    mem_db = os.environ.get("NOTION_DB_MEMORY")
    if not mem_db:
        logger.error("_save_memory_entry: NOTION_DB_MEMORY not set!")
        return
    notion_cat = _notion_cat_for_key(key)
    logger.info("_save_memory_entry: key=%s cat=%s user=%s", key, notion_cat, user_notion_id[:8] if user_notion_id else "none")
    props = {
        "Текст": _title(fact),
        "Ключ": _text(key),
        "Категория": _select(notion_cat),
        "Бот": _select("☀️ Nexus"),
        "Актуально": {"checkbox": True},
    }
    if user_notion_id:
        from core.notion_client import _relation
        props["🪪 Пользователи"] = _relation(user_notion_id)
    try:
        from core.notion_client import db_query
        # Ищем существующую запись по ключу (в ЛЮБОЙ бюджетной категории — на случай миграции)
        existing = await db_query(mem_db, filter_obj={"and": [
            {"property": "Ключ", "rich_text": {"equals": key}},
            {"property": "Бот", "select": {"equals": "☀️ Nexus"}},
        ]}, page_size=1)
        if existing:
            logger.info("_save_memory_entry: updating existing page %s", existing[0]["id"])
            await update_page(existing[0]["id"], props)
        else:
            logger.info("_save_memory_entry: creating new page")
            result = await page_create(mem_db, props)
            logger.info("_save_memory_entry: created page %s", result)
    except Exception as e:
        logger.error("_save_memory_entry FAILED: %s for key=%s", e, key)


# ── Payday Review + Reminder ─────────────────────────────────────────────────


async def _budget_period_review(user_notion_id: str = "") -> Tuple[str, float]:
    """Review spending vs limits for the PREVIOUS period. Returns (formatted_text, savings_total)."""
    payday = await _get_payday()
    period_start_str, period_end_str = _period_bounds(payday, previous=True)

    # Get spending for that period
    from core.config import config
    from core.notion_client import db_query
    records = await db_query(config.nexus.db_finance, filter_obj={"and": [
        {"property": "Тип", "select": {"equals": "💸 Расход"}},
        {"property": "Дата", "date": {"on_or_after": period_start_str}},
        {"property": "Дата", "date": {"on_or_before": period_end_str}},
    ]}, page_size=500)

    spending_by_cat: Dict[str, float] = {}
    for r in records:
        props = r.get("properties", {})
        amt = props.get("Сумма", {}).get("number") or 0
        cat_sel = props.get("Категория", {}).get("select")
        cat = cat_sel.get("name", "Прочее") if cat_sel else "Прочее"
        spending_by_cat[cat] = spending_by_cat.get(cat, 0) + amt

    # Get limits from Memory
    mem_db = os.environ.get("NOTION_DB_MEMORY")
    limits = await _get_limits(mem_db) if mem_db else {}

    # Get debt payments
    budget_data = await _load_budget_data(user_notion_id)
    debts = budget_data.get("долги", [])

    # Format review
    start_date = datetime.strptime(period_start_str, "%Y-%m-%d")
    month_name = _RU_MONTHS.get(start_date.month, str(start_date.month))
    lines = ["📊 <b>Ревью за {} ({} → {})</b>".format(
        month_name,
        start_date.strftime("%d.%m"),
        datetime.strptime(period_end_str, "%Y-%m-%d").strftime("%d.%m"),
    )]

    total_saved = 0.0
    cat_lines = []
    for cat_link, limit_amount in sorted(limits.items(), key=lambda x: -x[1]):
        # Find matching full category name
        full_cat = None
        for c in CATEGORIES:
            if _cat_link(c) == cat_link or cat_link in _cat_link(c):
                full_cat = c
                break
        if not full_cat:
            full_cat = cat_link

        spent = spending_by_cat.get(full_cat, 0)
        diff = limit_amount - spent
        if diff >= 0:
            cat_lines.append("  {} {:,.0f} / {:,.0f}₽ — сэкономила {:,.0f}₽ 🟢".format(
                full_cat, spent, limit_amount, diff))
            total_saved += diff
        else:
            cat_lines.append("  {} {:,.0f} / {:,.0f}₽ — перерасход {:,.0f}₽ 🔴".format(
                full_cat, spent, limit_amount, abs(diff)))
            total_saved += diff  # negative

    if cat_lines:
        lines.append("\n📊 По категориям:")
        lines.extend(cat_lines)

    if total_saved >= 0:
        lines.append("\n💰 Итого: сэкономила <b>{:,.0f}₽</b>".format(total_saved))
    else:
        lines.append("\n💸 Итого: перерасход <b>{:,.0f}₽</b>".format(abs(total_saved)))

    # Debt info
    for d in debts:
        fact = d.get("fact", "")
        m = _DEBT_RE.search(fact)
        if m:
            name = m.group(1).strip()
            amt = int(re.sub(r'[\s.,]', '', m.group(2)))
            lines.append("\n📋 Долг {}: осталось {:,.0f}₽".format(name, amt))

    return "\n".join(lines), total_saved


async def maybe_payday_reminder(message: Message, user_notion_id: str = "") -> None:
    """Send period review + payday reminder once per period start."""
    payday = await _get_payday()
    if payday <= 1:
        return  # No custom payday set
    now = datetime.now(MOSCOW_TZ)
    if now.day != payday:
        return
    uid = message.from_user.id
    state = _budget_get(uid) or {}
    today_str = now.strftime("%Y-%m-%d")
    if state.get("last_payday_reminder") == today_str:
        return
    state["last_payday_reminder"] = today_str
    state["notion_uid"] = state.get("notion_uid", user_notion_id)

    # Generate period review first
    try:
        review_text, savings_total = await _budget_period_review(user_notion_id)
        state["savings_from_last_period"] = savings_total
        _budget_set(uid, state)
        await message.answer(review_text, parse_mode="HTML")
    except Exception as e:
        logger.error("payday review error: %s", e, exc_info=True)
        _budget_set(uid, state)

    # Then the reminder
    await message.answer(
        "📊 <b>Пора обновить бюджет на следующий период!</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="📊 Обновить", callback_data="budget_recalc_full"),
            InlineKeyboardButton(text="✅ Без изменений", callback_data="msg_hide"),
        ]]),
        parse_mode="HTML",
    )
