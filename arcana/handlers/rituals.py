"""arcana/handlers/rituals.py"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta

from aiogram.types import Message
from core.claude_client import ask_claude
from core.notion_client import ritual_add, client_find, log_error, finance_add
from core.shared_handlers import get_user_tz

logger = logging.getLogger("arcana.rituals")

PAYMENT_SOURCE_MAP = {
    "карта": "💳 Карта",
    "наличные": "💵 Наличные",
    "бартер": "🔄 Бартер",
}

PARSE_RITUAL_SYSTEM = (
    "Извлеки данные ритуала. Ответь ТОЛЬКО JSON без markdown:\n"
    '{"client_name": "имя или null", "name": "название", '
    '"goal": "привлечение|защита|очищение|любовь|финансы|деструктив|развязка|приворот|другое или null", '
    '"place": "дома|лес|погост|перекрёсток|церковь|водоём|поле|другое или null", '
    '"consumables": "расходники строкой", "consumables_cost": число, '
    '"duration_min": число, "offerings": "подношения", "offerings_cost": число, '
    '"forces": "силы", "structure": "последовательность", '
    '"notes": "заметки или null", '
    '"amount": число, "paid": число, '
    '"payment_source": "карта|наличные|бартер или null"}'
)


async def handle_add_ritual(message: Message, text: str, user_notion_id: str = "") -> None:
    tg_id = message.from_user.id
    tz_offset = await get_user_tz(tg_id)
    tz = timezone(timedelta(hours=tz_offset))

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

    goal = data.get("goal") or None
    place = data.get("place") or None
    notes = data.get("notes") or None
    offerings_cost = float(data.get("offerings_cost") or 0)
    payment_source_raw = data.get("payment_source") or None
    payment_source = PAYMENT_SOURCE_MAP.get((payment_source_raw or "").lower(), payment_source_raw) if payment_source_raw else None
    amount = float(data.get("amount") or 0)
    paid = float(data.get("paid") or 0)

    today = datetime.now(tz).strftime("%Y-%m-%d")
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
        amount=amount,
        paid=paid,
        client_id=client_id,
        user_notion_id=user_notion_id,
        goal=goal,
        place=place,
        notes=notes,
        payment_source=payment_source,
        offerings_cost=offerings_cost if offerings_cost > 0 else None,
    )
    if not result:
        await message.answer("⚠️ Ошибка записи в Notion.")
        return

    if amount > 0:
        await finance_add(
            date=today,
            amount=amount,
            category="🔮 Практика",
            type_="💰 Доход",
            source=payment_source or "💳 Карта",
            bot_label="🌒 Arcana",
            description=f"🕯️ {data.get('name') or 'Ритуал'}" + (f" — {client_name}" if client_name else ""),
            user_notion_id=user_notion_id,
        )

    debt = max(0, amount - paid)
    await message.answer(
        f"✅ Ритуал записан\n"
        f"🕯️ <b>{data.get('name', 'Ритуал')}</b>\n"
        f"{'👤 ' + client_name if client_name else '🔮 Личный'}\n"
        f"⏱ {data.get('duration_min') or '?'} мин · 🌿 расходники {data.get('consumables_cost') or 0}₽\n"
        f"{'💰 ' + str(int(amount)) + '₽' if amount else ''}"
        f"{'  ⚠️ долг ' + str(int(debt)) + '₽' if debt > 0 else ''}"
    )
