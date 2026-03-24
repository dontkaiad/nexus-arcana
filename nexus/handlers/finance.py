"""nexus/handlers/finance.py"""
from __future__ import annotations

import base64
import calendar
import json
import logging
import os
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Set, Tuple

from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram import Router, F
from core.claude_client import ask_claude, ask_claude_vision
from core.notion_client import finance_month, log_error, page_create, update_page, create_report_page, _title, _number, _select, _date, _text

logger = logging.getLogger("nexus.finance")
MOSCOW_TZ = timezone(timedelta(hours=3))

router = Router()

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
    r'(?:.*?дедлайн:\s*(.+?))?$',
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

    filt = {"property": "Категория", "select": {"equals": "💰 Лимит"}}
    if user_notion_id:
        filt = {"and": [filt, {"property": "🪪 Пользователи", "relation": {"contains": user_notion_id}}]}
    try:
        pages = await db_query(mem_db, filter_obj=filt, page_size=200)
    except Exception as e:
        logger.error("_load_budget_data: %s", e)
        return {"обязательные": [], "цели": [], "долги": [], "лимиты": []}

    result: Dict[str, list] = {"обязательные": [], "цели": [], "долги": [], "лимиты": []}
    for p in pages:
        props = p["properties"]
        fact_parts = props.get("Текст", {}).get("title", [])
        fact = fact_parts[0]["plain_text"] if fact_parts else ""
        key_parts = props.get("Ключ", {}).get("rich_text", [])
        key = key_parts[0]["plain_text"].strip().lower() if key_parts else ""
        active = props.get("Актуально", {}).get("checkbox", True)
        if not active:
            continue

        if key.startswith("обязательно_"):
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
                result["долги"].append({
                    "name": m.group(1).strip(),
                    "amount": _parse_amount(m.group(2)),
                    "deadline": (m.group(3) or "").strip(),
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

    free_total = total_income - obligatory_total - savings_total
    free_left = free_total - total_expenses
    return (free_left, days_remaining)


async def build_budget_message(user_notion_id: str = "") -> Optional[str]:
    """Формирует полное сообщение /budget. Возвращает HTML-строку или None."""
    from core.config import config
    from core.notion_client import db_query

    budget = await _load_budget_data(user_notion_id)
    has_data = any(budget[k] for k in budget)

    now = datetime.now(MOSCOW_TZ)
    month_str = now.strftime("%Y-%m")
    month_start = f"{month_str}-01"
    today_str = now.strftime("%Y-%m-%d")
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    days_remaining = days_in_month - now.day
    ru_month = _RU_MONTHS.get(now.month, "")

    # Доходы за месяц
    try:
        income_records = await db_query(config.nexus.db_finance, filter_obj={"and": [
            {"property": "Тип", "select": {"equals": "💰 Доход"}},
            {"property": "Дата", "date": {"on_or_after": month_start}},
            {"property": "Дата", "date": {"on_or_before": today_str}},
        ]}, page_size=200)
    except Exception:
        income_records = []
    total_income = sum((p["properties"].get("Сумма", {}).get("number") or 0) for p in income_records)

    # Расходы за месяц
    try:
        expense_records = await db_query(config.nexus.db_finance, filter_obj={"and": [
            {"property": "Тип", "select": {"equals": "💸 Расход"}},
            {"property": "Дата", "date": {"on_or_after": month_start}},
            {"property": "Дата", "date": {"on_or_before": today_str}},
        ]}, page_size=500)
    except Exception:
        expense_records = []
    total_expenses = sum((p["properties"].get("Сумма", {}).get("number") or 0) for p in expense_records)

    if not budget["обязательные"]:
        return None  # нет обязательных → нужна настройка

    # ── Формируем сообщение ──
    lines = [f"<b>💰 Бюджет на {ru_month}</b>"]

    # Доход
    if total_income:
        lines.append(f"\n<b>📥 Доход: {total_income:,.0f}₽</b>")
        # Разбивка по категориям дохода
        by_cat: Dict[str, float] = {}
        for r in income_records:
            cat = (r["properties"].get("Категория", {}).get("select") or {}).get("name", "💳 Прочее")
            amt = r["properties"].get("Сумма", {}).get("number") or 0
            by_cat[cat] = by_cat.get(cat, 0) + amt
        for cat, amt in sorted(by_cat.items(), key=lambda x: -x[1]):
            lines.append(f"  {cat} — {amt:,.0f}₽")

    # Обязательные
    obligatory_total = sum(o["amount"] for o in budget["обязательные"])
    if budget["обязательные"]:
        lines.append(f"\n<b>📌 Обязательные: {obligatory_total:,.0f}₽</b>")
        for o in sorted(budget["обязательные"], key=lambda x: -x["amount"]):
            lines.append(f"  {o['name']} — {o['amount']:,.0f}₽")

    # Свободные
    savings_total = sum(g["saving"] for g in budget["цели"])
    free_total = total_income - obligatory_total - savings_total
    if total_income > 0:
        free_left = free_total - total_expenses
        daily = free_left / max(days_remaining, 1)
        lines.append(f"\n<b>💳 Свободные: {free_total:,.0f}₽</b>")
        lines.append(f"  📊 Уже потрачено: {total_expenses:,.0f}₽")
        lines.append(f"  ✅ Ещё можно: {free_left:,.0f}₽")
        lines.append(f"  📅 Осталось дней: {days_remaining}")
        lines.append(f"  💸 В день: {daily:,.0f}₽")

    # Долги
    if budget["долги"]:
        debt_total = sum(d["amount"] for d in budget["долги"])
        lines.append(f"\n<b>📋 Долги: {debt_total:,.0f}₽</b>")
        for d in budget["долги"]:
            dl = f" · {d['deadline']}" if d["deadline"] else ""
            lines.append(f"  {d['name']} — {d['amount']:,.0f}₽{dl}")

    # Цели
    if budget["цели"]:
        lines.append(f"\n<b>🎯 Цели:</b>")
        for g in budget["цели"]:
            saving_label = f" · откладываю {g['saving']:,.0f}₽/мес" if g["saving"] else ""
            lines.append(f"  {g['name']} — {g['target']:,.0f}₽{saving_label}")

    return "\n".join(lines)


async def _check_budget_limit(category: str, message: Message, user_notion_id: str = "") -> None:
    """После записи расхода — проверить бюджетный лимит по категории."""
    logger.info("_check_budget_limit called: category=%s", category)
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
    month_start = now.strftime("%Y-%m-01")
    today_str = now.strftime("%Y-%m-%d")
    try:
        records = await db_query(config.nexus.db_finance, filter_obj={"and": [
            {"property": "Тип",       "select": {"equals": "💸 Расход"}},
            {"property": "Категория", "select": {"equals": category}},
            {"property": "Дата",      "date":   {"on_or_after": month_start}},
            {"property": "Дата",      "date":   {"on_or_before": today_str}},
        ]}, page_size=200)
        month_total = sum((p["properties"].get("Сумма", {}).get("number") or 0) for p in records)
        logger.info("_check_budget_limit: month_total=%.0f limit=%.0f category=%s",
                    month_total, limit_amount, category)
    except Exception as e:
        logger.error("_check_budget_limit db_query: %s", e, exc_info=True)
        return

    pct = month_total / limit_amount * 100 if limit_amount else 0

    # Собираем всё в ОДНО сообщение
    parts = []

    # Прогресс по категории
    parts.append(f"📊 {category}: {month_total:,.0f} / {limit_amount:,.0f}₽ ({pct:.0f}%)")

    if pct >= 100:
        over = month_total - limit_amount
        parts[0] = f"🚨 {category}: <b>{month_total:,.0f}₽</b> из {limit_amount:,.0f}₽ (+{over:,.0f}₽)"
    elif pct >= 80:
        parts[0] = f"⚠️ {category}: <b>{month_total:,.0f}₽</b> из {limit_amount:,.0f}₽ ({pct:.0f}%)"

    # Прогноз до конца месяца
    if pct < 100 and limit_amount:
        now2 = datetime.now(MOSCOW_TZ)
        day2 = now2.day
        if 5 <= day2 <= 24:
            days_in_month = calendar.monthrange(now2.year, now2.month)[1]
            projected = month_total / day2 * days_in_month
            if projected > limit_amount:
                parts.append(f"📈 Прогноз: ~{projected:,.0f}₽ — темп высоковат")

    # Остаток свободных
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


# ── Budget Setup: One-Shot Free-Form ─────────────────────────────────────────

# ── Budget Setup State ───────────────────────────────────────────────────────

_BUDGET_VARIABLE_CATS = [
    "🚬 Привычки", "💅 Бьюти", "🚕 Транспорт", "🍜 Продукты",
    "🍱 Кафе/Доставка", "🏥 Здоровье", "👗 Гардероб", "📚 Хобби/Учеба",
]

# uid → list of raw text messages (buffer)
_budget_buf: Dict[int, list] = {}
# uid → user_notion_id (for saving after accept)
_budget_uid: Dict[int, str] = {}
# uid → sonnet plan JSON (for accept/adjust)
_budget_plan: Dict[int, dict] = {}
# uid → bot message id (for editing)
_budget_msg: Dict[int, int] = {}

BUDGET_PARSE_PROMPT = """Ты финансовый советник для человека с СДВГ (женский род).

Входной текст (может быть с опечатками, сокращениями, в свободной форме):
{all_messages}

Извлеки и структурируй:
1. Доходы (зп, аренда, фриланс и тд)
2. Фиксированные траты (жилье, коммуналка, подписки, интернет, вода, коты — НЕЛЬЗЯ сократить)
3. Вариативные траты (привычки, бьюти, транспорт, продукты, кафе — можно оптимизировать)
4. Долги (кому, сколько, дедлайн)
5. Цели (что, сколько стоит)

"к" = тысяч, "млн" = миллионов.
Диапазон (15-20к) — бери верхнюю границу для безопасности.
"в год" — раздели на 12. Пример: "впн 6к в год" = 500₽/мес.

ЗАТЕМ — оптимальный финансовый план. ПРАВИЛА:
- Фикс не трогать
- Коты = ФИКС (живые существа)
- Привычки: -10% макс от текущего (СДВГ = резкий отказ → срыв)
- ОБЯЗАТЕЛЬНО 3-5к "импульсивный бюджет" — дофамин без вины (СДВГ)
- ДОЛГИ ПРИОРИТЕТНЕЕ ЦЕЛЕЙ. Дедлайн → обязательный платёж
- Цели: дешёвые сначала (мотивация), подушка минимум всегда
- Дорогие цели — реалистично (не мучить)
- Остаток >= 0. Не хватает — сокращай цели, не лимиты
- Объясни КАЖДОЕ решение

Ответ СТРОГО в JSON (без markdown):
{{
  "income": [{{"source": "ЗП", "amount": 100000}}],
  "income_total": 115000,
  "fixed": [
    {{"name": "Съём квартиры", "category": "🏠 Жилье", "amount": 20000}},
    {{"name": "Корм коты", "category": "🐾 Коты", "amount": 5000}}
  ],
  "fixed_total": 54200,
  "limits": [
    {{"category": "🚬 Привычки", "amount": 18000, "current": 20000, "change": "-10%", "note": "плавно"}}
  ],
  "impulse_budget": 5000,
  "impulse_note": "Дофамин — трать без вины",
  "debts": [
    {{"name": "Вика", "amount": 50000, "deadline": "апрель 2026", "monthly": 50000, "note": "разом с ЗП", "priority": 1}}
  ],
  "debts_total": 190000,
  "goals": [
    {{"name": "Подушка", "total": 200000, "monthly": 3000, "months": 67, "priority": 1}}
  ],
  "summary": "2-3 предложения: стратегия и прогноз",
  "habit_strategy": "Стратегия сокращения привычек"
}}"""


# ── Start / Collect / Finish ─────────────────────────────────────────────────

async def start_budget_setup(message: Message, user_notion_id: str = "") -> None:
    """Начать сбор данных для бюджета (one-shot)."""
    uid = message.from_user.id
    _budget_buf[uid] = []
    _budget_uid[uid] = user_notion_id
    _budget_plan.pop(uid, None)

    sent = await message.answer(
        "💰 <b>Давай настроим бюджет!</b>\n\n"
        "Напиши всё что знаешь о своих финансах — я сама разберу.\n"
        "Можно в свободной форме, например:\n\n"
        "<i>зп 100к, аренда 15к\n"
        "квартира 20к, коммуналка 7к, своя кв 4к\n"
        "вода 1500, интернет 950\n"
        "клод 9500, спотифай 170, впн 500, тг 170\n"
        "коты: корм 5к, наполнитель 2500, влажный 500\n"
        "привычки 15-20к, бьюти 12к, транспорт 3-4к\n"
        "долги: вика 50к до апреля, илья 40к до августа\n"
        "цели: телефон 100к, подушка 200к</i>\n\n"
        "Пиши как удобно — одним сообщением или несколькими.\n"
        "Когда закончишь — напиши <b>готово</b> или нажми кнопку.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Готово, считай!", callback_data="bsetup_go"),
        ]]),
        parse_mode="HTML",
    )
    _budget_msg[uid] = sent.message_id


async def handle_budget_setup_text(message: Message, user_notion_id: str = "") -> bool:
    """Перехват текста во время сбора бюджета. Возвращает True если обработано."""
    uid = message.from_user.id

    # Режим корректировки
    if uid in _budget_plan and _budget_plan[uid].get("_adjusting"):
        return await _handle_adjust_text(message, uid)

    if uid not in _budget_buf:
        return False

    text = (message.text or "").strip()
    if not text:
        return False

    if text.lower() in ("отмена", "cancel", "стоп"):
        _budget_buf.pop(uid, None)
        _budget_uid.pop(uid, None)
        _budget_msg.pop(uid, None)
        await message.answer("❌ Настройка бюджета отменена.")
        return True

    # "готово" → запустить анализ
    if text.lower() in ("готово", "готов", "всё", "все", "давай", "считай", "поехали"):
        if not _budget_buf[uid]:
            await message.answer("⚠️ Ты ещё ничего не написала. Напиши данные о финансах.")
            return True
        await _run_budget_analysis(message, uid)
        return True

    # Копим в буфер, ставим реакцию
    _budget_buf[uid].append(text)
    try:
        await message.react([{"type": "emoji", "emoji": "👀"}])
    except Exception:
        pass  # Реакции могут быть недоступны
    return True


@router.callback_query(F.data == "bsetup_go")
async def on_budget_go(call: CallbackQuery) -> None:
    """Кнопка 'Готово, считай!'"""
    uid = call.from_user.id
    if uid not in _budget_buf or not _budget_buf[uid]:
        await call.answer("⚠️ Сначала напиши данные о финансах!", show_alert=True)
        return
    await _run_budget_analysis(call.message, uid)
    await call.answer()


@router.callback_query(F.data == "bsetup_accept")
async def on_budget_accept(call: CallbackQuery) -> None:
    """Принять план → сохранить."""
    uid = call.from_user.id
    plan = _budget_plan.get(uid)
    if not plan:
        await call.answer("⚠️ Сессия устарела — /budget заново", show_alert=True)
        return
    await _save_budget_plan(call.message, uid)
    await call.answer()


@router.callback_query(F.data == "bsetup_recalc")
async def on_budget_recalc(call: CallbackQuery) -> None:
    """Пересчитать план."""
    uid = call.from_user.id
    if uid not in _budget_buf:
        await call.answer("⚠️ Сессия устарела — /budget заново", show_alert=True)
        return
    _budget_msg[uid] = call.message.message_id
    await _run_budget_analysis(call.message, uid)
    await call.answer()


@router.callback_query(F.data == "bsetup_adjust")
async def on_budget_adjust(call: CallbackQuery) -> None:
    """Режим корректировки."""
    uid = call.from_user.id
    plan = _budget_plan.get(uid)
    if not plan:
        await call.answer("⚠️ Сессия устарела — /budget заново", show_alert=True)
        return
    plan["_adjusting"] = True
    _budget_msg[uid] = call.message.message_id
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


# ── Sonnet Analysis ──────────────────────────────────────────────────────────

async def _run_budget_analysis(message: Message, uid: int) -> None:
    """Собрать буфер, отправить Sonnet, показать план."""
    all_text = "\n".join(_budget_buf.get(uid, []))

    # Показать "считаю..."
    bot_msg_id = _budget_msg.get(uid, 0)
    try:
        if bot_msg_id:
            await message.bot.edit_message_text(
                "🔍 <b>Анализирую бюджет...</b>\nSonnet считает оптимальный план.",
                chat_id=message.chat.id, message_id=bot_msg_id, parse_mode="HTML",
            )
        else:
            sent = await message.answer(
                "🔍 <b>Анализирую бюджет...</b>\nSonnet считает оптимальный план.",
                parse_mode="HTML",
            )
            _budget_msg[uid] = sent.message_id
            bot_msg_id = sent.message_id
    except Exception:
        sent = await message.answer(
            "🔍 <b>Анализирую бюджет...</b>\nSonnet считает оптимальный план.",
            parse_mode="HTML",
        )
        _budget_msg[uid] = sent.message_id
        bot_msg_id = sent.message_id

    prompt = BUDGET_PARSE_PROMPT.format(all_messages=all_text)
    try:
        from core.config import config as _cfg
        raw = await ask_claude(prompt, model=_cfg.model_sonnet, max_tokens=4096)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r'^```\w*\n?', '', raw)
            raw = re.sub(r'\n?```$', '', raw)
        plan = json.loads(raw)
        _budget_plan[uid] = plan
    except Exception as e:
        logger.error("Sonnet budget analysis failed: %s", e)
        try:
            await message.bot.edit_message_text(
                "⚠️ Не удалось получить анализ. Попробуй ещё раз.",
                chat_id=message.chat.id, message_id=bot_msg_id, parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="bsetup_recalc"),
                ]]),
            )
        except Exception:
            await message.answer("⚠️ Не удалось получить анализ. Попробуй /budget заново.")
        return

    plan_text = _format_plan(plan)
    try:
        await message.bot.edit_message_text(
            plan_text, chat_id=message.chat.id, message_id=bot_msg_id,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Принять", callback_data="bsetup_accept"),
                InlineKeyboardButton(text="✏️ Изменить", callback_data="bsetup_adjust"),
                InlineKeyboardButton(text="🔄 Пересчитать", callback_data="bsetup_recalc"),
            ]]),
        )
    except Exception:
        sent = await message.answer(
            plan_text, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Принять", callback_data="bsetup_accept"),
                InlineKeyboardButton(text="✏️ Изменить", callback_data="bsetup_adjust"),
                InlineKeyboardButton(text="🔄 Пересчитать", callback_data="bsetup_recalc"),
            ]]),
        )
        _budget_msg[uid] = sent.message_id


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

    # Лимиты
    limits = plan.get("limits", [])
    if limits:
        limits_total = sum(l.get("amount", 0) for l in limits)
        lines.append("\n<b>📊 Лимиты: {:,}₽</b>".format(limits_total))
        for l in limits:
            change = ""
            if l.get("change"):
                change = " ({})".format(l["change"])
            elif l.get("current"):
                change = " (было {:,})".format(l["current"])
            note = " — <i>{}</i>".format(l["note"]) if l.get("note") else ""
            lines.append("  {} — {:,}₽{}{}".format(l.get("category", "?"), l["amount"], change, note))

    # Импульсивный
    impulse = plan.get("impulse_budget", 0)
    if impulse:
        lines.append("\n<b>🎲 Импульсивный: {:,}₽</b>".format(impulse))
        lines.append("  <i>{}</i>".format(plan.get("impulse_note", "Трать без вины!")))

    # Долги
    debts = plan.get("debts", [])
    if debts:
        debts_total = plan.get("debts_total", sum(d.get("amount", 0) for d in debts))
        lines.append("\n<b>📋 Долги (приоритет!): {:,}₽</b>".format(debts_total))
        for d in debts:
            dl = " · {}".format(d["deadline"]) if d.get("deadline") else ""
            mon = " · {:,}₽/мес".format(d["monthly"]) if d.get("monthly") else ""
            lines.append("  {} — {:,}₽{}{}".format(d["name"], d["amount"], dl, mon))

    # Цели
    goals = plan.get("goals", [])
    if goals:
        lines.append("\n<b>🎯 Цели (после долгов):</b>")
        for g in goals:
            mon = " {:,}₽/мес →".format(g["monthly"]) if g.get("monthly") else ""
            months = " {} мес".format(g["months"]) if g.get("months") else ""
            lines.append("  {} —{}{} (всего {:,}₽)".format(
                g["name"], mon, months, g.get("total", 0)))

    # Summary
    if plan.get("summary"):
        lines.append("\n💡 <i>{}</i>".format(plan["summary"]))
    if plan.get("habit_strategy"):
        lines.append("\n🚬 <i>{}</i>".format(plan["habit_strategy"]))

    return "\n".join(lines)


# ── Adjust mode ──────────────────────────────────────────────────────────────

async def _handle_adjust_text(message: Message, uid: int) -> bool:
    """Обработка текста в режиме корректировки. Добавляет в буфер."""
    text = (message.text or "").strip()
    if not text:
        return False

    # Добавить корректировку в буфер
    if uid in _budget_buf:
        _budget_buf[uid].append("КОРРЕКТИРОВКА: " + text)
    else:
        _budget_buf[uid] = ["КОРРЕКТИРОВКА: " + text]

    try:
        await message.react([{"type": "emoji", "emoji": "✏️"}])
    except Exception:
        pass

    # Убрать флаг — ждём нажатия "Пересчитать"
    return True


# ── Save Plan ────────────────────────────────────────────────────────────────

async def _save_budget_plan(message: Message, uid: int) -> None:
    """Сохранить принятый план в Notion Память."""
    plan = _budget_plan.get(uid, {})
    notion_uid = _budget_uid.get(uid, "")
    bot_msg_id = _budget_msg.get(uid, 0)

    try:
        if bot_msg_id:
            await message.bot.edit_message_text(
                "💾 <b>Сохраняю план...</b>",
                chat_id=message.chat.id, message_id=bot_msg_id, parse_mode="HTML",
            )
    except Exception:
        pass

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

    # Лимиты
    for l in plan.get("limits", []):
        cat = l.get("category", "📌 Прочее")
        amt = l.get("amount", 0)
        await _save_memory_entry(
            "лимит_{}".format(_cat_link(cat)),
            "лимит: {} — {}₽/мес".format(cat, amt),
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

    # Долги
    for d in plan.get("debts", []):
        name = d["name"]
        amt = d["amount"]
        dl = d.get("deadline", "")
        dl_part = " · дедлайн: {}".format(dl) if dl else ""
        mon_part = " · платёж: {}₽/мес".format(d["monthly"]) if d.get("monthly") else ""
        await _save_memory_entry(
            "долг_{}".format(name.lower().replace(" ", "_")),
            "долг: {} — {}₽{}{}".format(name, amt, dl_part, mon_part),
            notion_uid,
        )

    # Цели
    for g in plan.get("goals", []):
        name = g["name"]
        total = g.get("total", 0)
        monthly = g.get("monthly", 0)
        await _save_memory_entry(
            "цель_{}".format(name.lower().replace(" ", "_")),
            "цель: {} — {}₽ · откладываю {}₽/мес".format(name, total, monthly),
            notion_uid,
        )

    # Cleanup
    _budget_buf.pop(uid, None)
    _budget_uid.pop(uid, None)
    _budget_plan.pop(uid, None)

    # Показать итоговый бюджет
    budget_msg = await build_budget_message(notion_uid)
    try:
        if bot_msg_id:
            await message.bot.edit_message_text(
                "🎉 <b>План принят и сохранён!</b>\n\n{}".format(budget_msg or "Вызови /budget для просмотра."),
                chat_id=message.chat.id, message_id=bot_msg_id, parse_mode="HTML",
            )
        else:
            await message.answer(
                "🎉 <b>План принят и сохранён!</b>\n\n{}".format(budget_msg or "Вызови /budget для просмотра."),
                parse_mode="HTML",
            )
    except Exception:
        await message.answer("✅ План сохранён! Вызови /budget для просмотра.")
    _budget_msg.pop(uid, None)


# ── Save to Memory ───────────────────────────────────────────────────────────

async def _save_memory_entry(key: str, fact: str, user_notion_id: str = "") -> None:
    """Сохранить или обновить запись в Памяти (💰 Лимит)."""
    mem_db = os.environ.get("NOTION_DB_MEMORY")
    if not mem_db:
        return
    props = {
        "Текст": _title(fact),
        "Ключ": _text(key),
        "Категория": _select("💰 Лимит"),
        "Бот": _select("☀️ Nexus"),
        "Актуально": {"checkbox": True},
    }
    if user_notion_id:
        from core.notion_client import _relation
        props["🪪 Пользователи"] = _relation(user_notion_id)
    try:
        from core.notion_client import db_query
        existing = await db_query(mem_db, filter_obj={"and": [
            {"property": "Ключ", "rich_text": {"contains": key}},
            {"property": "Категория", "select": {"equals": "💰 Лимит"}},
        ]}, page_size=1)
        if existing:
            await update_page(existing[0]["id"], props)
        else:
            await page_create(mem_db, props)
    except Exception as e:
        logger.error("_save_memory_entry: %s for key=%s", e, key)


# ── Compat save functions ────────────────────────────────────────────────────

async def _save_goal(name: str, amount: int, user_notion_id: str = "") -> None:
    await _save_memory_entry(
        "цель_{}".format(name.lower().replace(" ", "_")),
        "цель: {} — {}₽ · откладываю 0₽/мес".format(name, amount),
        user_notion_id,
    )

async def _save_debt(name: str, amount: int, deadline: str = "", user_notion_id: str = "") -> None:
    dl_part = " · дедлайн: {}".format(deadline) if deadline else ""
    await _save_memory_entry(
        "долг_{}".format(name.lower().replace(" ", "_")),
        "долг: {} — {}₽{}".format(name, amount, dl_part),
        user_notion_id,
    )
