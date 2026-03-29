"""arcana/handlers/clients.py"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from core.claude_client import ask_claude
from core.notion_client import (
    client_add, client_find, sessions_by_client, rituals_by_client,
    arcana_all_debts, get_page, update_page, _extract_text, _extract_number, log_error,
)

logger = logging.getLogger("arcana.clients")
router = Router()

MOSCOW_TZ = timezone(timedelta(hours=3))

PARSE_CLIENT_SYSTEM = (
    "Извлеки данные нового клиента. Ответь ТОЛЬКО JSON без markdown:\n"
    '{"name": "имя", "contact": "@ник или телефон или null", "request": "запрос или null"}'
)

PARSE_INFO_SYSTEM = (
    "Извлеки данные клиента из свободного текста. Ответь ТОЛЬКО JSON без markdown:\n"
    '{"contact": "@ник или телефон или null", '
    '"contact_type": "telegram/телефон/email/null", '
    '"request": "запрос/тема обращения или null", '
    '"notes": "заметки о характере/подходе или null"}'
)

VISION_CONTACT_SYSTEM = (
    "Это скриншот контакта. Извлеки данные. Ответь ТОЛЬКО JSON без markdown:\n"
    '{"contact": "@ник или номер телефона", '
    '"contact_type": "telegram/телефон/whatsapp", '
    '"name": "имя если видно или null"}'
)


def _today() -> str:
    return datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")


def _parse_json_safe(raw: str) -> dict:
    try:
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(raw)
    except Exception:
        return {}


def _awaiting_kb(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="➕ Создать без деталей", callback_data=f"client_create_empty:{uid}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"client_cancel:{uid}"),
    ]])


async def _create_and_confirm(
    message: Message,
    name: str,
    contact: str,
    request: str,
    notes: str,
    user_notion_id: str,
    page_id: Optional[str] = None,
) -> None:
    """Создать клиента (или обновить notes) и показать карточку."""
    if page_id is None:
        page_id = await client_add(
            name=name,
            contact=contact,
            request=request,
            date=_today(),
            user_notion_id=user_notion_id,
        )
    if not page_id:
        await message.answer("⚠️ Ошибка записи в Notion.")
        return
    if notes:
        try:
            await update_page(page_id, {"Заметки": {"rich_text": [{"text": {"content": notes[:2000]}}]}})
        except Exception as e:
            logger.warning("update notes error: %s", e)
    await message.answer(
        f"✅ Клиент добавлен\n"
        f"👤 <b>{name}</b>\n"
        f"📱 {contact or '—'}\n"
        f"💬 {request or '—'}\n"
        f"📝 {notes or '—'}",
        parse_mode="HTML",
    )


# ── Handlers ──────────────────────────────────────────────────────────────────

async def handle_add_client(message: Message, text: str, user_notion_id: str = "") -> None:
    try:
        raw = await ask_claude(text, system=PARSE_CLIENT_SYSTEM, max_tokens=256)
        data = _parse_json_safe(raw)
        name = data.get("name") or ""
        if not name:
            await message.answer("⚠️ Не нашла имя клиента.")
            return

        existing = await client_find(name, user_notion_id=user_notion_id)
        if existing:
            existing_name = _extract_text(existing["properties"].get("Имя", {}))
            await message.answer(f"👤 Клиент «{existing_name}» уже есть.")
            return

        contact = data.get("contact") or ""
        request = data.get("request") or ""

        if contact or request:
            # Достаточно данных — создаём сразу
            await _create_and_confirm(message, name, contact, request, "", user_notion_id)
            return

        # Мало данных — запрашиваем дополнительно
        from arcana.pending_clients import save_pending_client
        await save_pending_client(message.from_user.id, {
            "name": name,
            "user_notion_id": user_notion_id,
            "step": "awaiting_info",
        })
        await message.answer(
            f"👤 Создаю клиента <b>{name}</b>\n\n"
            "Скинь инфу — любым способом:\n"
            "• Текстом: контакт, запрос, заметки\n"
            "• Скрин контакта из TG\n"
            "• Поделиться контактом\n"
            "• Голосовое\n\n"
            "Или нажми «Создать без деталей»",
            reply_markup=_awaiting_kb(message.from_user.id),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.exception("handle_add_client: %s", e)
        await log_error(str(e), context="handle_add_client", bot_label="🌒 Arcana")
        await message.answer("⚠️ Ошибка при создании клиента.")


async def handle_client_info(message: Message, text: str, user_notion_id: str = "") -> None:
    try:
        name = (await ask_claude(
            text,
            system="Извлеки только имя клиента. Ответь ТОЛЬКО именем.",
            max_tokens=30,
        )).strip()

        client = await client_find(name, user_notion_id=user_notion_id)
        if not client:
            from arcana.pending_clients import save_pending_client
            await save_pending_client(message.from_user.id, {
                "name": name,
                "user_notion_id": user_notion_id,
                "step": "awaiting_info",
            })
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=f"➕ Создать {name}", callback_data=f"client_create_empty:{message.from_user.id}"),
                InlineKeyboardButton(text="❌ Нет", callback_data=f"client_cancel:{message.from_user.id}"),
            ]])
            await message.answer(f"❌ Клиент «{name}» не найден. Создать?", reply_markup=kb)
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
            f"{mem_block}",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.exception("handle_client_info: %s", e)
        await log_error(str(e), context="handle_client_info", bot_label="🌒 Arcana")
        await message.answer("⚠️ Ошибка загрузки клиента.")


async def handle_client_info_input(message: Message, text: str, pending: dict) -> None:
    """Юзер прислал инфу о клиенте (текст/голосовое) — парсим и создаём."""
    uid = message.from_user.id
    name = pending.get("name", "")
    user_notion_id = pending.get("user_notion_id", "")

    try:
        raw = await ask_claude(text, system=PARSE_INFO_SYSTEM, max_tokens=200)
        data = _parse_json_safe(raw)

        contact = data.get("contact") or ""
        contact_type = data.get("contact_type") or ""
        request = data.get("request") or ""
        notes = data.get("notes") or ""

        if contact and contact_type and contact_type.lower() not in ("null", ""):
            contact = f"{contact} ({contact_type})"

        from arcana.pending_clients import delete_pending_client
        await delete_pending_client(uid)

        await _create_and_confirm(message, name, contact, request, notes, user_notion_id)
    except Exception as e:
        logger.exception("handle_client_info_input: %s", e)
        await log_error(str(e), context="handle_client_info_input", bot_label="🌒 Arcana")
        await message.answer("⚠️ Ошибка при обработке данных клиента.")


async def handle_client_photo_input(message: Message, image_b64: str, pending: dict) -> None:
    """Юзер прислал скрин контакта — Vision парсит, создаём клиента."""
    uid = message.from_user.id
    name = pending.get("name", "")
    user_notion_id = pending.get("user_notion_id", "")

    try:
        from core.claude_client import ask_claude_vision
        raw = await ask_claude_vision(
            "Извлеки контактные данные с этого скриншота.",
            image_b64,
            system=VISION_CONTACT_SYSTEM,
        )
        data = _parse_json_safe(raw)

        contact = data.get("contact") or ""
        contact_type = data.get("contact_type") or ""
        if contact and contact_type and contact_type.lower() not in ("null", ""):
            contact = f"{contact} ({contact_type})"

        # Если Vision нашёл имя и у нас не было — использовать
        if not name:
            name = data.get("name") or "Клиент"

        from arcana.pending_clients import delete_pending_client
        await delete_pending_client(uid)

        await _create_and_confirm(message, name, contact, "", "", user_notion_id)
    except Exception as e:
        logger.exception("handle_client_photo_input: %s", e)
        await log_error(str(e), context="handle_client_photo_input", bot_label="🌒 Arcana")
        await message.answer("⚠️ Ошибка при обработке скриншота контакта.")


# ── Callbacks ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("client_create_empty:"))
async def cb_create_empty(callback: CallbackQuery, user_notion_id: str = "") -> None:
    uid = int(callback.data.split(":", 1)[1])
    if uid != callback.from_user.id:
        return
    await callback.answer()
    try:
        from arcana.pending_clients import get_pending_client, delete_pending_client
        pending = await get_pending_client(uid)
        if not pending:
            await callback.message.edit_text("⏱ Сессия истекла.")
            return
        name = pending.get("name", "")
        result = await client_add(
            name=name,
            date=_today(),
            user_notion_id=pending.get("user_notion_id", ""),
        )
        await delete_pending_client(uid)
        if result:
            await callback.message.edit_text(f"✅ Клиент <b>{name}</b> создан", parse_mode="HTML")
        else:
            await callback.message.edit_text("⚠️ Ошибка записи в Notion.")
    except Exception as e:
        logger.exception("cb_create_empty: %s", e)
        await callback.message.edit_text("⚠️ Ошибка.")


@router.callback_query(F.data.startswith("client_cancel:"))
async def cb_client_cancel(callback: CallbackQuery, user_notion_id: str = "") -> None:
    uid = int(callback.data.split(":", 1)[1])
    if uid != callback.from_user.id:
        return
    await callback.answer()
    from arcana.pending_clients import delete_pending_client
    await delete_pending_client(uid)
    await callback.message.edit_text("❌ Отмена.")


# ── Debts ─────────────────────────────────────────────────────────────────────

async def handle_debts(message: Message, user_notion_id: str = "") -> None:
    items = await arcana_all_debts(user_notion_id=user_notion_id)
    if not items:
        await message.answer("✅ Долгов нет.")
        return

    total_debt = 0.0
    lines = []
    client_name_cache: dict = {}
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
        f"\n\n💸 Итого: <b>{total_debt:,.0f}₽</b>",
        parse_mode="HTML",
    )
