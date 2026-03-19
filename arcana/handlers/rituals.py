"""arcana/handlers/rituals.py"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta

from aiogram.types import Message
from core.claude_client import ask_claude
from core.notion_client import ritual_add, client_find, log_error

logger = logging.getLogger("arcana.rituals")
MOSCOW_TZ = timezone(timedelta(hours=3))

PARSE_RITUAL_SYSTEM = (
    "Извлеки данные ритуала. Ответь ТОЛЬКО JSON без markdown:\n"
    '{"client_name": "имя или null", "name": "название", '
    '"consumables": "расходники строкой", "consumables_cost": число, '
    '"duration_min": число, "offerings": "подношения", "forces": "силы", '
    '"structure": "последовательность", "amount": число, "paid": число}'
)


async def handle_add_ritual(message: Message, text: str, user_notion_id: str = "") -> None:
    raw = await ask_claude(text, system=PARSE_RITUAL_SYSTEM, max_tokens=600)
    try:
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(raw)
    except Exception:
        await log_error(text, "parse_error", raw)
        await message.answer("⚠️ Не смог разобрать ритуал. Опиши подробнее.")
        return

    client_name = data.get("client_name")
    client_id = None
    if client_name:
        client = await client_find(client_name, user_notion_id=user_notion_id)
        if client:
            client_id = client["id"]

    today = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")
    result = await ritual_add(
        name=data.get("name", "Ритуал"),
        date=today,
        ritual_type="Личный" if not client_name else "Клиентский",
        consumables=data.get("consumables") or "",
        consumables_cost=float(data.get("consumables_cost") or 0),
        duration_min=float(data.get("duration_min") or 0),
        offerings=data.get("offerings") or "",
        forces=data.get("forces") or "",
        structure=data.get("structure") or "",
        amount=float(data.get("amount") or 0),
        paid=float(data.get("paid") or 0),
        client_id=client_id,
        user_notion_id=user_notion_id,
    )
    if not result:
        await message.answer("⚠️ Ошибка записи в Notion.")
        return

    debt = max(0, float(data.get("amount") or 0) - float(data.get("paid") or 0))
    await message.answer(
        f"✅ Ритуал записан\n"
        f"🕯️ <b>{data.get('name', 'Ритуал')}</b>\n"
        f"{'👤 ' + client_name if client_name else '🔮 Личный'}\n"
        f"⏱ {data.get('duration_min') or '?'} мин · 🌿 расходники {data.get('consumables_cost') or 0}₽\n"
        f"{'💰 ' + str(int(data.get('amount'))) + '₽' if data.get('amount') else ''}"
        f"{'  ⚠️ долг ' + str(int(debt)) + '₽' if debt > 0 else ''}"
    )
