"""arcana/handlers/finance.py — Финансовая аналитика практики + касса/выплата."""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

from aiogram import Router, F
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from core.cash_register import compute_pnl, BOT_NEXUS, SALARY_CATEGORY
from core.claude_client import ask_claude
from core.notion_client import (
    arcana_finance_summary,
    arcana_clients_summary,
    finance_add,
    log_error,
    _extract_number,
    _extract_select,
    _extract_text,
    _extract_rollup_number,
)

router = Router()

logger = logging.getLogger("arcana.finance")

_MONTH_NAMES = [
    "", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]

_PARSE_MONTH_SYSTEM = """Извлеки месяц и год из текста. Ответь JSON: {"month": 3, "year": 2026}.
Если месяц не указан — верни null для month.
Если год не указан — используй текущий год.
Примеры: "в марте" → {"month": 3, "year": 2026}, "за январь 2025" → {"month": 1, "year": 2025},
"сколько заработала" → {"month": null, "year": 2026}.
Отвечай ТОЛЬКО валидным JSON."""


def _parse_json_safe(text: str) -> dict:
    try:
        return json.loads(text.strip())
    except Exception:
        return {}


def _pay_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💸 Выплатить", callback_data="arc_pay_self"),
    ]])


async def _format_pnl(pnl: dict) -> str:
    period = pnl.get("period") or {}
    label = f"{_MONTH_NAMES[int(period.get('month', 0))]} {period.get('year', '')}".strip()
    inc_total = pnl.get("income_month", 0)
    breakdown = pnl.get("income_breakdown", {})
    sess = breakdown.get("sessions", {}) or {}
    rit = breakdown.get("rituals", {}) or {}
    exp_total = pnl.get("expenses_month", 0)
    exp_cats = pnl.get("expenses_by_category", []) or []
    profit = pnl.get("profit_month", 0)
    salary_month = pnl.get("salary_month", 0)
    cash = pnl.get("cash_balance", 0)
    debt = pnl.get("debt_money", 0)
    barter = pnl.get("barter_open_count", 0)

    lines = [f"🌒 <b>АРКАНА · {label}</b>", ""]
    lines.append(f"📥 Доход: <b>{inc_total:,}₽</b>")
    if sess.get("amount"):
        lines.append(f"  Сеансы: {sess['amount']:,}₽ ({sess.get('count', 0)} шт)")
    if rit.get("amount"):
        lines.append(f"  Ритуалы: {rit['amount']:,}₽ ({rit.get('count', 0)} шт)")
    lines.append("")
    lines.append(f"📤 Расход: <b>{exp_total:,}₽</b>")
    for c in exp_cats:
        lines.append(f"  {c['name']}: {c['amount']:,}₽")
    lines.append("")
    profit_label = "📈 Прибыль месяца" if profit >= 0 else "📉 Убыток месяца"
    lines.append(f"💰 {profit_label}: <b>{profit:,}₽</b>")
    if salary_month:
        lines.append(f"💼 Выплачено себе: <b>{salary_month:,}₽</b>")
    lines.append(f"🏦 В кассе: <b>{cash:,}₽</b>")
    obligations = []
    if debt:
        obligations.append(f"{debt:,}₽")
    if barter:
        obligations.append(f"{barter} бартер пунктов")
    if obligations:
        lines.append(f"\n📋 Должны: {' + '.join(obligations)}")
    return "\n".join(lines)


async def handle_pay_self(message: Message, text: str, user_notion_id: str = "") -> None:
    """«выплати X / зарплата X / выплати себе X» → Финансы Бот=Nexus, Кат=Зарплата."""
    m = re.search(r"(\d[\d\s.,]*)\s*(?:к|тыс|тысяч|т)?", text or "", re.IGNORECASE)
    if not m:
        await message.answer("Не понял сумму. Например: «выплати 20к» или «зарплата 20000».")
        return
    raw = re.sub(r"[^\d.]", "", m.group(1))
    try:
        amount = float(raw)
    except ValueError:
        await message.answer("Не понял сумму.")
        return
    # «20к» / «20 тыс» → 20000
    suffix = (text or "")[m.end():m.end() + 5].lower()
    if "к" in (text or "")[m.end():m.end() + 2].lower() or "тыс" in suffix:
        amount *= 1000

    today = datetime.now(timezone(timedelta(hours=3))).date()
    pnl = await compute_pnl(user_notion_id, today.year, today.month)
    cash_before = pnl["cash_balance"]
    fin_id = await finance_add(
        date=today.strftime("%Y-%m-%d"),
        amount=amount,
        category=SALARY_CATEGORY,
        type_="💰 Доход",
        source="💳 Карта",
        description="Выплата себе",
        bot_label=BOT_NEXUS,
        user_notion_id=user_notion_id,
    )
    if not fin_id:
        await message.answer("⚠️ Не удалось записать выплату.")
        return
    cash_after = cash_before - amount
    await message.answer(
        f"💸 Выплатила {int(amount):,}₽. В кассе {int(cash_after):,}₽.",
    )


@router.callback_query(F.data == "arc_pay_self")
async def cb_pay_self(cb: CallbackQuery, user_notion_id: str = "") -> None:
    await cb.answer()
    await cb.message.answer(
        "💸 Сколько выплатить? Напиши «выплати 20к» или «зарплата 20000».",
    )


async def handle_arcana_finance(message: Message, user_notion_id: str = "", text: str = "") -> None:
    """Финансовая аналитика практики. Без text — текущий месяц."""
    try:
        from core.shared_handlers import get_user_tz
        tz_offset = await get_user_tz(message.from_user.id)
        tz = timezone(timedelta(hours=tz_offset))
        now = datetime.now(tz)

        # Если текст похож на pay_self — раскручиваем
        if text and re.search(r"\bвыплат|зарплат\b", text, re.IGNORECASE) and re.search(r"\d", text):
            await handle_pay_self(message, text, user_notion_id)
            return

        month: Optional[int] = now.month
        year: int = now.year

        # Если есть текст — попытаться распарсить месяц через Haiku
        if text:
            prompt = f"Сегодня {now.strftime('%d.%m.%Y')}. Текст: «{text}»"
            raw = await ask_claude(prompt, system=_PARSE_MONTH_SYSTEM, max_tokens=50,
                                   model="claude-haiku-4-5-20251001")
            parsed = _parse_json_safe(raw)
            if parsed.get("month"):
                month = int(parsed["month"])
            if parsed.get("year"):
                year = int(parsed["year"])

        # Новый формат — P&L с кассой через cash_register.compute_pnl.
        pnl = await compute_pnl(user_notion_id, year, month)
        reply = await _format_pnl(pnl)
        await message.answer(reply, parse_mode="HTML", reply_markup=_pay_kb())
        return

        # ── legacy detail block (оставлен как fallback) ──────────────────────
        records = await arcana_finance_summary(user_notion_id, month, year)

        income = 0.0
        expenses = 0.0
        by_category: dict = {}

        for r in records:
            p = r.get("properties", {})
            amount = _extract_number(p.get("Сумма", {})) or 0
            type_ = _extract_select(p.get("Тип", {})) or ""
            category = _extract_select(p.get("Категория", {})) or "Прочее"

            if "Доход" in type_:
                income += amount
            else:
                expenses += amount
                by_category[category] = by_category.get(category, 0.0) + amount

        profit = income - expenses

        # 2. Клиенты с rollups
        clients = await arcana_clients_summary(user_notion_id)
        client_lines = []
        total_debt = 0.0

        for c in clients:
            p = c.get("properties", {})
            name = _extract_text(p.get("Имя", {}))
            if not name:
                continue
            paid = _extract_rollup_number(p.get("Всего оплачено", {}))
            debt = _extract_rollup_number(p.get("Общий долг", {}))
            n_sessions = _extract_rollup_number(p.get("Кол-во сеансов", {}))
            n_rituals = _extract_rollup_number(p.get("Кол-во ритуалов", {}))
            total_debt += debt

            if paid > 0 or debt > 0:
                debt_str = f" · ⚠️ долг {debt:,.0f}₽" if debt > 0 else ""
                parts = []
                if n_sessions > 0:
                    parts.append(f"{int(n_sessions)} расклад.")
                if n_rituals > 0:
                    parts.append(f"{int(n_rituals)} ритуал.")
                counts = " · ".join(parts) if parts else ""
                count_str = f" ({counts})" if counts else ""
                client_lines.append(f"  {name} — {paid:,.0f}₽{count_str}{debt_str}")

        # 3. Сборка ответа
        period_label = f"{_MONTH_NAMES[month]} {year}" if month else str(year)
        reply = f"📊 <b>{period_label}</b>\n\n"

        reply += f"💰 Доход: <b>{income:,.0f}₽</b>\n"

        if by_category:
            reply += f"💸 Расходы: <b>{expenses:,.0f}₽</b>\n"
            for cat, amt in sorted(by_category.items(), key=lambda x: -x[1]):
                reply += f"  {cat}: {amt:,.0f}₽\n"

        reply += "━" * 20 + "\n"

        if profit >= 0:
            reply += f"📈 Прибыль: <b>{profit:,.0f}₽</b>\n"
        else:
            reply += f"📉 Убыток: <b>{abs(profit):,.0f}₽</b>\n"

        if client_lines:
            reply += f"\n👥 <b>По клиентам:</b>\n"
            reply += "\n".join(client_lines[:10]) + "\n"

        if total_debt > 0:
            reply += f"\n⚠️ Всего должны: <b>{total_debt:,.0f}₽</b>"

        if not records and not client_lines:
            reply += "\nЗаписей за период не найдено."

        await message.answer(reply, parse_mode="HTML")

    except Exception as e:
        logger.exception("handle_arcana_finance error: %s", e)
        await log_error(str(e), context="handle_arcana_finance", bot_label="🌒 Arcana")
        await message.answer("Не удалось загрузить финансовую аналитику.")
