"""arcana/handlers/clients.py — Client CRUD + multi-step creation flow."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from core.claude_client import ask_claude, analyze_image
from core.notion_client import (
    client_add, client_find, sessions_by_client, rituals_by_client,
    arcana_all_debts, get_page, update_page, _extract_text, _extract_number,
    log_error, _text as _ntext,
)

logger = logging.getLogger("arcana.clients")
MOSCOW_TZ = timezone(timedelta(hours=3))

router = Router()

# ── Parse systems ─────────────────────────────────────────────────────────────

PARSE_CLIENT_SYSTEM = (
    "Извлеки данные нового клиента. Ответь ТОЛЬКО JSON без markdown:\n"
    '{"name": "имя", "contact": "@ник или телефон или null", "request": "запрос или null"}'
)

PARSE_INFO_SYSTEM = (
    "Извлеки контактную информацию и запрос. Ответь ТОЛЬКО JSON без markdown:\n"
    '{"contacts": [{"value": "@ник_или_телефон", "label": "TG/WhatsApp/Phone/etc"}], '
    '"request": "запрос клиента или null", "notes": "заметки или null"}'
)

VISION_CONTACT_SYSTEM = (
    "Извлеки все контакты из скриншота. Ответь ТОЛЬКО JSON без markdown:\n"
    '{"contacts": [{"value": "контакт", "label": "TG/WhatsApp/Phone/etc"}], '
    '"name": "имя человека или null"}'
)

# ── Keyboards ─────────────────────────────────────────────────────────────────

def _confirm_kb(uid: int) -> InlineKeyboardMarkup:
    """Подтверждение: создать нового или нет."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="➕ Создать", callback_data=f"client_start_create:{uid}"),
        InlineKeyboardButton(text="❌ Нет",     callback_data=f"client_cancel:{uid}"),
    ]])


def _awaiting_kb(uid: int) -> InlineKeyboardMarkup:
    """Кнопки пока накапливаем инфо."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Создать", callback_data=f"client_create_final:{uid}"),
        InlineKeyboardButton(text="❌ Отмена",  callback_data=f"client_cancel:{uid}"),
    ]])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _format_contacts(contacts: List[Dict[str, str]]) -> str:
    if not contacts:
        return "—"
    parts = []
    for c in contacts:
        val = c.get("value", "")
        label = c.get("label", "")
        parts.append(f"{val} ({label})" if label else val)
    return ", ".join(parts)


def _pending_summary(pending: dict) -> str:
    name = pending.get("name") or "—"
    contacts = _format_contacts(pending.get("contacts") or [])
    request = pending.get("request") or "—"
    notes = pending.get("notes") or "—"
    return (
        f"👤 <b>{name}</b>\n"
        f"📱 {contacts}\n"
        f"💬 {request}\n"
        f"📝 {notes}"
    )


async def _do_create_from_pending(message: Message, uid: int, pending: dict) -> None:
    """Создать клиента из накопленного pending-состояния."""
    from arcana.pending_clients import delete_pending_client

    name = pending.get("name") or ""
    contacts = pending.get("contacts") or []
    request = pending.get("request") or ""
    notes = pending.get("notes") or ""
    user_notion_id = pending.get("user_notion_id") or ""

    if not name:
        await message.answer("⚠️ Не знаю имя клиента. Напиши «клиент Имя».")
        return

    contact_str = _format_contacts(contacts)
    today = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")

    page_id = await client_add(
        name=name,
        contact=contact_str if contact_str != "—" else "",
        request=request,
        date=today,
        user_notion_id=user_notion_id,
    )
    await delete_pending_client(uid)

    if not page_id:
        await message.answer("⚠️ Ошибка записи в Notion.")
        return

    # Записать заметки отдельно если есть
    if notes and page_id:
        try:
            await update_page(page_id, {"Заметки": _ntext(notes)})
        except Exception as e:
            logger.warning("_do_create_from_pending notes update: %s", e)

    await message.answer(
        f"✅ Клиент создан\n"
        f"👤 <b>{name}</b>\n"
        f"📱 {contact_str}\n"
        f"💬 {request or '—'}\n"
        f"📝 {notes or '—'}",
        parse_mode="HTML",
    )


def _parse_json_safe(raw: str) -> dict:
    try:
        cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(cleaned)
    except Exception:
        return {}


# ── Main handlers ─────────────────────────────────────────────────────────────

async def handle_add_client(message: Message, text: str, user_notion_id: str = "") -> None:
    """Намерение new_client — проверить БД, если нет — создать сразу с имеющейся инфой."""
    raw = await ask_claude(text, system=PARSE_CLIENT_SYSTEM, max_tokens=256)
    data = _parse_json_safe(raw)

    name = data.get("name") or ""
    if not name:
        await message.answer("⚠️ Не нашла имя клиента.")
        return

    existing = await client_find(name, user_notion_id=user_notion_id)
    if existing:
        existing_name = _extract_text(existing["properties"].get("Имя", {}))
        await message.answer(f"👤 Клиент «{existing_name}» уже есть в базе.")
        return

    today = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")
    contact = data.get("contact") or ""
    request = data.get("request") or ""

    page_id = await client_add(
        name=name,
        contact=contact,
        request=request,
        date=today,
        user_notion_id=user_notion_id,
    )
    if not page_id:
        await message.answer("⚠️ Ошибка записи в Notion.")
        return

    await message.answer(
        f"✅ Клиент добавлен\n"
        f"👤 <b>{name}</b>\n"
        f"📱 {contact or '—'}\n"
        f"💬 {request or '—'}",
        parse_mode="HTML",
    )


async def handle_client_info(message: Message, text: str, user_notion_id: str = "") -> None:
    """Запрос инфо о клиенте."""
    name = (await ask_claude(
        text,
        system="Извлеки только имя клиента. Ответь ТОЛЬКО именем.",
        max_tokens=30,
    )).strip()

    client = await client_find(name, user_notion_id=user_notion_id)
    if not client:
        await message.answer(f"❌ Клиент «{name}» не найден в базе.")
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


async def handle_client_info_input(message: Message, text: str, pending: dict) -> None:
    """Текст во время ожидания инфо о клиенте — накапливаем, не создаём."""
    from arcana.pending_clients import update_pending_client

    uid = message.from_user.id

    raw = await ask_claude(text, system=PARSE_INFO_SYSTEM, max_tokens=300)
    data = _parse_json_safe(raw)

    # Merge contacts
    new_contacts = data.get("contacts") or []
    existing_contacts = list(pending.get("contacts") or [])
    seen = {c["value"] for c in existing_contacts}
    for c in new_contacts:
        if c.get("value") and c["value"] not in seen:
            existing_contacts.append(c)
            seen.add(c["value"])

    updates: Dict[str, Any] = {"contacts": existing_contacts}
    if data.get("request"):
        updates["request"] = data["request"]
    if data.get("notes"):
        updates["notes"] = (pending.get("notes") or "") + (" " + data["notes"]).strip()

    await update_pending_client(uid, updates)

    # Fetch fresh pending for display
    from arcana.pending_clients import get_pending_client
    fresh = await get_pending_client(uid) or {**pending, **updates}

    await message.answer(
        f"📋 <b>Данные клиента</b>\n\n{_pending_summary(fresh)}\n\n"
        f"Добавь ещё контакты/фото, или нажми Создать.",
        reply_markup=_awaiting_kb(uid),
        parse_mode="HTML",
    )


async def handle_client_photo_input(message: Message, image_b64: str, pending: dict) -> None:
    """Фото во время ожидания инфо — Vision извлекает контакты, накапливает."""
    from arcana.pending_clients import update_pending_client, get_pending_client

    uid = message.from_user.id

    raw = await analyze_image(
        image_b64,
        prompt="Извлеки все контакты из скриншота.",
        system=VISION_CONTACT_SYSTEM,
    )
    data = _parse_json_safe(raw) if raw else {}

    new_contacts = data.get("contacts") or []
    existing_contacts = list(pending.get("contacts") or [])
    seen = {c["value"] for c in existing_contacts}
    for c in new_contacts:
        if c.get("value") and c["value"] not in seen:
            existing_contacts.append(c)
            seen.add(c["value"])

    updates: Dict[str, Any] = {"contacts": existing_contacts, "step": "awaiting_info"}
    # If Vision found a name and we don't have one yet
    if data.get("name") and not pending.get("name"):
        updates["name"] = data["name"]

    await update_pending_client(uid, updates)
    fresh = await get_pending_client(uid) or {**pending, **updates}

    contacts_str = _format_contacts(existing_contacts) if existing_contacts else "—"
    await message.answer(
        f"📸 Контакт добавлен: {contacts_str}\n\n"
        f"{_pending_summary(fresh)}\n\n"
        f"Добавь ещё или нажми Создать.",
        reply_markup=_awaiting_kb(uid),
        parse_mode="HTML",
    )


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
        f"\n\n💸 Итого: <b>{total_debt:,.0f}₽</b>",
        parse_mode="HTML",
    )


# ── Callback handlers ─────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("client_start_create:"))
async def cb_start_create(callback: CallbackQuery, user_notion_id: str = "") -> None:
    """Подтверждение создания → перейти в режим ожидания инфо."""
    uid = int(callback.data.split(":", 1)[1])
    if uid != callback.from_user.id:
        return
    await callback.answer()

    from arcana.pending_clients import update_pending_client, get_pending_client
    await update_pending_client(uid, {"step": "awaiting_info"})
    pending = await get_pending_client(uid) or {}
    name = pending.get("name") or "клиент"

    await callback.message.edit_text(
        f"👤 Создаю «<b>{name}</b>»\n\n"
        f"Отправь контакты (фото скриншота, @ник, телефон) или напиши запрос.\n"
        f"Когда готова — нажми Создать.",
        reply_markup=_awaiting_kb(uid),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("client_create_final:"))
async def cb_create_final(callback: CallbackQuery, user_notion_id: str = "") -> None:
    """Создать клиента со всеми накопленными данными."""
    uid = int(callback.data.split(":", 1)[1])
    if uid != callback.from_user.id:
        return
    await callback.answer()

    from arcana.pending_clients import get_pending_client
    pending = await get_pending_client(uid)
    if not pending:
        await callback.message.edit_text("⏱ Сессия истекла. Начни заново.")
        return

    await _do_create_from_pending(callback.message, uid, pending)


@router.callback_query(F.data.startswith("client_create_empty:"))
async def cb_create_empty(callback: CallbackQuery, user_notion_id: str = "") -> None:
    """Создать клиента без доп. инфо (только имя)."""
    uid = int(callback.data.split(":", 1)[1])
    if uid != callback.from_user.id:
        return
    await callback.answer()

    from arcana.pending_clients import get_pending_client
    pending = await get_pending_client(uid)
    if not pending:
        await callback.message.edit_text("⏱ Сессия истекла. Начни заново.")
        return

    await _do_create_from_pending(callback.message, uid, pending)


@router.callback_query(F.data.startswith("client_cancel:"))
async def cb_cancel(callback: CallbackQuery, user_notion_id: str = "") -> None:
    """Отмена создания клиента."""
    uid = int(callback.data.split(":", 1)[1])
    if uid != callback.from_user.id:
        return
    await callback.answer("Отменено")

    from arcana.pending_clients import delete_pending_client
    await delete_pending_client(uid)
    await callback.message.edit_text("❌ Создание клиента отменено.")
