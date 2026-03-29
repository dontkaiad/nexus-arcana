"""arcana/handlers/finance.py — Финансовая аналитика практики."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from aiogram.types import Message

from core.claude_client import ask_claude
from core.notion_client import (
    arcana_finance_summary,
    arcana_clients_summary,
    log_error,
    _extract_number,
    _extract_select,
    _extract_text,
    _extract_rollup_number,
)

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


async def handle_arcana_finance(message: Message, user_notion_id: str = "", text: str = "") -> None:
    """Финансовая аналитика практики. Без text — текущий месяц."""
    try:
        from core.shared_handlers import get_user_tz
        tz_offset = await get_user_tz(message.from_user.id)
        tz = timezone(timedelta(hours=tz_offset))
        now = datetime.now(tz)

        month: Optional[int] = now.month
        year: int = now.year

        # Если есть текст — попытаться распарсить месяц через Haiku
        if text:
            prompt = f"Сегодня {now.strftime('%d.%m.%Y')}. Текст: «{text}»"
            raw = await ask_claude(prompt, system=_PARSE_MONTH_SYSTEM, max_tokens=50, model="haiku")
            parsed = _parse_json_safe(raw)
            if parsed.get("month"):
                month = int(parsed["month"])
            if parsed.get("year"):
                year = int(parsed["year"])

        # 1. Финансовые записи
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
