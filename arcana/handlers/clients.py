"""arcana/handlers/clients.py"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from core.claude_client import ask_claude
from core.notion_client import (
    client_add, client_find, sessions_by_client, rituals_by_client,
    arcana_all_debts, get_page, _extract_text, _extract_number, log_error,
)

router = Router()

logger = logging.getLogger("arcana.clients")
MOSCOW_TZ = timezone(timedelta(hours=3))

PARSE_CLIENT_SYSTEM = (
    "Извлеки данные нового клиента. Ответь ТОЛЬКО JSON без markdown:\n"
    '{"name": "имя", "contact": "@ник или телефон или null", "request": "запрос или null"}'
)


async def handle_add_client(message: Message, text: str, user_notion_id: str = "") -> None:
    raw = await ask_claude(text, system=PARSE_CLIENT_SYSTEM, max_tokens=256)
    try:
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(raw)
    except Exception:
        await log_error(text, "parse_error", raw)
        await message.answer("⚠️ Не смог разобрать данные клиента.")
        return

    name = data.get("name") or ""
    if not name:
        await message.answer("⚠️ Не нашёл имя клиента.")
        return

    existing = await client_find(name, user_notion_id=user_notion_id)
    if existing:
        existing_name = _extract_text(existing["properties"].get("Имя", {}))
        await message.answer(f"👤 Клиент «{existing_name}» уже есть. Обновить данные?")
        return

    today = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")
    result = await client_add(
        name=name,
        contact=data.get("contact") or "",
        request=data.get("request") or "",
        date=today,
        user_notion_id=user_notion_id,
    )
    if not result:
        await message.answer("⚠️ Ошибка записи в Notion.")
        return

    await message.answer(
        f"✅ Клиент добавлен\n"
        f"👤 <b>{name}</b>\n"
        f"📱 {data.get('contact') or '—'}\n"
        f"💬 {data.get('request') or '—'}"
    )


async def handle_client_info(message: Message, text: str, user_notion_id: str = "") -> None:
    name = (await ask_claude(
        text,
        system="Извлеки только имя клиента. Ответь ТОЛЬКО именем.",
        max_tokens=30,
    )).strip()

    client = await client_find(name, user_notion_id=user_notion_id)
    if not client:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=f"➕ Создать {name}", callback_data=f"create_client:{name}"),
        ]])
        await message.answer(f"❌ Клиент «{name}» не найден.", reply_markup=kb)
        return

    cid = client["id"]
    props = client["properties"]
    client_name = _extract_text(props.get("Имя", {}))
    contact = _extract_text(props.get("Контакт", {}))
    request = _extract_text(props.get("Запрос", {}))
    notes = _extract_text(props.get("Заметки", {}))

    sessions = await sessions_by_client(cid, user_notion_id=user_notion_id)
    rituals = await rituals_by_client(cid, user_notion_id=user_notion_id)

    total = 0.0
    debt = 0.0
    history = []
    for item in sessions + rituals:
        p = item["properties"]
        amount = _extract_number(p.get("Сумма", {})) or 0
        paid = _extract_number(p.get("Оплачено", {})) or 0
        total += amount
        debt += max(0, amount - paid)
        q_items = p.get("Вопрос", p.get("Название", {}))
        desc = _extract_text(q_items)
        date_val = (p.get("Дата и время") or p.get("Дата") or {}).get("date", {})
        d = (date_val.get("start", "") if date_val else "")[:10]
        history.append(f"  {d} — {desc} — {amount:.0f}₽")

    debt_str = f"⚠️ {debt:,.0f}₽" if debt > 0 else "✅ 0₽"
    hist_str = "\n".join(history[:5]) or "  (нет записей)"

    from core.memory import get_memories_for_context
    memory_context = await get_memories_for_context(user_notion_id, [client_name])
    mem_block = f"\n\n🧠 <b>Из памяти:</b>\n{memory_context}" if memory_context else ""

    await message.answer(
        f"👤 <b>{client_name}</b>\n"
        f"📱 {contact or '—'} · с {props.get('Первое обращение', {}).get('date', {}).get('start', '—')[:10]}\n"
        f"💬 {request or '—'}\n"
        f"📝 {notes or '—'}\n\n"
        f"💰 Всего: {total:,.0f}₽ | Долг: {debt_str}\n"
        f"🃏 Сеансов: {len(sessions)} | 🕯 Ритуалов: {len(rituals)}\n\n"
        f"<b>История:</b>\n{hist_str}"
        f"{mem_block}"
    )


@router.callback_query(F.data.startswith("create_client:"))
async def cb_create_client(callback: CallbackQuery, user_notion_id: str = "") -> None:
    name = callback.data.split(":", 1)[1]
    await callback.answer()
    try:
        from datetime import datetime, timezone, timedelta
        today = datetime.now(timezone(timedelta(hours=3))).strftime("%Y-%m-%d")
        result = await client_add(name=name, date=today, user_notion_id=user_notion_id)
        if result:
            await callback.message.edit_text(f"✅ Клиент <b>{name}</b> создан.")
        else:
            await callback.message.edit_text("⚠️ Ошибка при создании клиента.")
    except Exception as e:
        logger.exception("cb_create_client: %s", e)
        await callback.message.edit_text("⚠️ Ошибка при создании клиента.")


async def handle_debts(message: Message, user_notion_id: str = "") -> None:
    items = await arcana_all_debts(user_notion_id=user_notion_id)
    if not items:
        await message.answer("✅ Долгов нет.")
        return

    total_debt = 0.0
    lines = []
    client_name_cache: dict[str, str] = {}
    for item in items:
        p = item["properties"]
        amount = _extract_number(p.get("Сумма", {})) or 0
        paid = _extract_number(p.get("Оплачено", {})) or 0
        debt = amount - paid
        total_debt += debt
        rel = p.get("Клиент", {}).get("relation", [])
        client_label = "Личный"
        if rel:
            cid = rel[0]["id"]
            if cid in client_name_cache:
                client_label = client_name_cache[cid]
            else:
                try:
                    page = await get_page(cid)
                    client_label = _extract_text(page.get("properties", {}).get("Имя", {})) or cid[:8] + "…"
                except Exception:
                    client_label = cid[:8] + "…"
                client_name_cache[cid] = client_label
        name_items = p.get("Название", p.get("Вопрос", {}))
        desc = _extract_text(name_items)[:40]
        lines.append(f"• {client_label} — {desc}: <b>{debt:,.0f}₽</b>")

    await message.answer(
        f"⚠️ <b>Долги клиентов:</b>\n\n" +
        "\n".join(lines) +
        f"\n\n💸 Итого: <b>{total_debt:,.0f}₽</b>"
    )
