"""arcana/handlers/clients.py — Client CRUD + multi-step creation flow."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from core.claude_client import ask_claude, ask_claude_vision
from arcana.repos.clients_repo import ClientsRepo, CLIENT_TYPE_PAID, CLIENT_TYPE_FREE

logger = logging.getLogger("arcana.clients")
_repo = ClientsRepo()
MOSCOW_TZ = timezone(timedelta(hours=3))

router = Router()

# ── Parse systems ─────────────────────────────────────────────────────────────

PARSE_CLIENT_SYSTEM = (
    "Извлеки данные нового клиента. Ответь ТОЛЬКО JSON без markdown:\n"
    '{"name": "имя", "contact": "@ник или телефон или null", '
    '"request": "запрос или null", "client_type": "Платный" | "Бесплатный" | null}'
    "\n\nclient_type:\n"
    "- 'бесплатно', 'без оплаты', 'по дружбе', 'просто так', 'в подарок' → 'Бесплатный'\n"
    "- иначе → null (код подставит дефолт «Платный»).\n"
    "Никогда не возвращай 'Self' — Self ставится только вручную в Notion."
)

PARSE_CLIENT_INFO = (
    "Извлеки ВСЕ данные о клиенте из текста. Ответь ТОЛЬКО JSON без markdown:\n"
    '{"contacts": [{"value": "@ник или телефон", "label": "личный/рабочий/null"}], '
    '"request": "запрос/тема обращения или null", '
    '"notes": "заметки о характере/подходе или null"}'
)

VISION_CONTACT = (
    "Это скриншот профиля из Telegram, соцсети или контакта телефона. "
    "Извлеки имя/username, день рождения если виден, контакты, заметки. "
    "Ответь ТОЛЬКО JSON без markdown:\n"
    '{"name": "имя если видно или null", '
    '"birthday": "YYYY-MM-DD если видна дата, иначе null", '
    '"contacts": [{"value": "@username или номер", "label": "описание если есть или null"}], '
    '"notes": "любые заметки или null"}'
)

# ── Keyboards ─────────────────────────────────────────────────────────────────

def _confirm_kb(uid: int) -> InlineKeyboardMarkup:
    """«Не найден. Создать?» — из client_info."""
    from core.utils import cancel_button
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="➕ Создать", callback_data=f"client_create_from_search:{uid}"),
        cancel_button("❌ Нет", f"client_cancel:{uid}"),
    ]])


def _duplicate_kb(uid: int) -> InlineKeyboardMarkup:
    """«Нашла совпадение» — из new_client."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да, это она",  callback_data=f"client_update_existing:{uid}"),
        InlineKeyboardButton(text="➕ Нет, новый",   callback_data=f"client_create_new:{uid}"),
    ]])


def _collecting_kb(uid: int) -> InlineKeyboardMarkup:
    """Режим сбора — всегда виден [✅ Готово]."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Готово", callback_data=f"client_done:{uid}"),
    ]])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _format_contacts(contacts: List[Dict[str, str]]) -> str:
    if not contacts:
        return "—"
    parts = []
    for c in contacts:
        val = c.get("value", "")
        label = c.get("label") or ""
        parts.append(f"{val} ({label})" if label else val)
    return ", ".join(parts)


def _card(pending: dict) -> str:
    """Текущее состояние карточки клиента."""
    return (
        f"👤 <b>{pending.get('name') or '—'}</b>\n"
        f"📱 {_format_contacts(pending.get('contacts') or [])}\n"
        f"💬 {pending.get('request') or '—'}\n"
        f"📝 {pending.get('notes') or '—'}"
    )


def _parse_json_safe(raw: str) -> dict:
    try:
        cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(cleaned)
    except Exception:
        return {}


async def _update_notion(page_id: str, pending: dict) -> None:
    """Записать все накопленные поля в Notion page."""
    contact_str = _format_contacts(pending.get("contacts") or [])
    await _repo.update_profile(
        page_id,
        contact=contact_str if contact_str and contact_str != "—" else None,
        request=pending.get("request") or None,
        notes=pending.get("notes") or None,
        birthday=pending.get("birthday") or None,
    )


# ── Main handlers ─────────────────────────────────────────────────────────────

async def handle_client_info(message: Message, text: str, user_notion_id: str = "") -> None:
    """«клиент Оля» / «что у Оли» → поиск. Найден: полное досье. Нет: предложить создать."""
    from arcana.pending_clients import save_pending_client

    name = (await ask_claude(
        text,
        system="Извлеки только имя клиента. Ответь ТОЛЬКО именем.",
        max_tokens=30,
        model="claude-haiku-4-5-20251001",
    )).strip()

    client = await _repo.find(name, user_notion_id=user_notion_id)
    if not client:
        uid = message.from_user.id
        await save_pending_client(uid, {
            "step": "confirm_create",
            "name": name,
            "contacts": [],
            "request": "",
            "notes": "",
            "user_notion_id": user_notion_id,
        })
        await message.answer(
            f"❌ Не нашла «<b>{name}</b>» в базе. Создать?",
            reply_markup=_confirm_kb(uid),
            parse_mode="HTML",
        )
        return

    sessions = await _repo.sessions_for(client.id, user_notion_id=user_notion_id)
    rituals = await _repo.rituals_for(client.id, user_notion_id=user_notion_id)

    total = 0.0
    debt = 0.0
    history = []
    for item in sessions + rituals:
        total += item.amount
        debt += max(0, item.amount - item.paid)
        history.append(f"  {item.date} — {item.description} — {item.amount:.0f}₽")

    debt_str = f"⚠️ {debt:,.0f}₽" if debt > 0 else "✅ 0₽"
    hist_str = "\n".join(history[:5]) or "  (нет записей)"

    from core.memory import get_memories_for_context
    memory_context = await get_memories_for_context(user_notion_id, [client.name])
    mem_block = f"\n\n🧠 <b>Из памяти:</b>\n{memory_context}" if memory_context else ""

    n_sessions = len(sessions)
    n_rituals = len(rituals)
    await message.answer(
        f"👤 <b>{client.name}</b>\n"
        f"📱 {client.contact or '—'} · с {client.since or '—'}\n"
        f"💬 {client.request or '—'}\n"
        f"📝 {client.notes or '—'}\n\n"
        f"💰 Всего: {total:,.0f}₽ | Долг: {debt_str}\n"
        f"🃏 Сеансов: {n_sessions} | 🕯 Ритуалов: {n_rituals}\n\n"
        f"<b>История:</b>\n{hist_str}"
        f"{mem_block}",
        parse_mode="HTML",
    )


async def handle_add_client(message: Message, text: str, user_notion_id: str = "") -> None:
    """«создай клиента Оля» → проверка дублей → создать / дополнить / сбор инфы."""
    from arcana.pending_clients import save_pending_client

    raw = await ask_claude(text, system=PARSE_CLIENT_SYSTEM, max_tokens=256,
                           model="claude-haiku-4-5-20251001")
    data = _parse_json_safe(raw)
    name = data.get("name") or ""
    if not name:
        await message.answer("⚠️ Не нашла имя клиента.")
        return

    uid = message.from_user.id

    # ── Проверка дублей ──────────────────────────────────────────────────────
    existing = await _repo.find(name, user_notion_id=user_notion_id)
    if existing:
        await save_pending_client(uid, {
            "step": "confirm_duplicate",
            "name": existing.name,
            "page_id": existing.id,
            "contacts": [{"value": existing.contact, "label": ""}] if existing.contact else [],
            "request": existing.request,
            "notes": "",
            "user_notion_id": user_notion_id,
        })
        await message.answer(
            f"👤 Нашла <b>{existing.name}</b>\n"
            f"📱 {existing.contact or '—'} · с {existing.since or '—'}\n"
            f"💬 {existing.request or '—'}\n\n"
            f"Дополнить карточку?",
            reply_markup=_duplicate_kb(uid),
            parse_mode="HTML",
        )
        return

    # ── Не найден — создаём сразу ────────────────────────────────────────────
    contact = data.get("contact") or ""
    request = data.get("request") or ""
    today = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")

    parsed_type = (data.get("client_type") or "").strip().lower()
    client_type = (
        CLIENT_TYPE_FREE if parsed_type == "бесплатный"
        else CLIENT_TYPE_PAID
    )
    page_id = await _repo.add(
        name=name,
        contact=contact,
        request=request,
        date=today,
        user_notion_id=user_notion_id,
        client_type=client_type,
    )
    if not page_id:
        await message.answer("⚠️ Ошибка записи в Notion.")
        return

    contacts_list = [{"value": contact, "label": ""}] if contact else []

    type_glyph = "🎁" if client_type == CLIENT_TYPE_FREE else "🤝"
    if not contact or not request:
        # Нет всей инфы — режим сбора
        await save_pending_client(uid, {
            "step": "collecting",
            "name": name,
            "page_id": page_id,
            "contacts": contacts_list,
            "request": request,
            "notes": "",
            "user_notion_id": user_notion_id,
        })
        pending_stub = {"name": name, "contacts": contacts_list, "request": request, "notes": ""}
        bot_msg = await message.answer(
            f"👥 Клиент создан · {type_glyph}\n🔮 <b>{name}</b>\n🟢 Активный\n\n{_card(pending_stub)}\n\n"
            f"Скинь инфу: контакт, запрос, заметки.",
            reply_markup=_collecting_kb(uid),
            parse_mode="HTML",
        )
    else:
        pending_stub = {"name": name, "contacts": contacts_list, "request": request, "notes": ""}
        bot_msg = await message.answer(
            f"👥 Клиент создан · {type_glyph}\n🔮 <b>{name}</b>\n🟢 Активный\n\n{_card(pending_stub)}",
            parse_mode="HTML",
        )

    # Сохраняем msg → page_id, чтобы reply на это сообщение мог менять тип/имя.
    try:
        from core.message_pages import save_message_page
        await save_message_page(
            chat_id=bot_msg.chat.id,
            message_id=bot_msg.message_id,
            page_id=page_id,
            page_type="client",
            bot="arcana",
        )
    except Exception:
        pass

    # Auto-attach фото если оно пришло в этом же сообщении (caption + photo).
    if getattr(message, "photo", None):
        try:
            from arcana.handlers.client_photo import attach_photo_to_client
            ok = await attach_photo_to_client(message, page_id, silent=True)
            if ok:
                await message.answer(f"✨ Создана: <b>{name}</b>. Фото добавлено.", parse_mode="HTML")
        except Exception:
            logger.exception("auto-attach photo on create failed")


async def _handle_collecting(
    message: Message, text: str, pending: dict, user_notion_id: str = ""
) -> None:
    """Режим сбора инфы — каждый текст дополняет карточку и сразу пишет в Notion."""
    from arcana.pending_clients import update_pending_client, get_pending_client

    uid = message.from_user.id
    page_id = pending.get("page_id")

    raw = await ask_claude(text, system=PARSE_CLIENT_INFO, max_tokens=300,
                           model="claude-haiku-4-5-20251001")
    data = _parse_json_safe(raw)

    updates: Dict[str, Any] = {}
    new_contacts = data.get("contacts") or []
    if new_contacts:
        updates["contacts"] = new_contacts  # накапливается в update_pending_client
    if data.get("request"):
        updates["request"] = data["request"]
    if data.get("notes"):
        existing_notes = pending.get("notes") or ""
        updates["notes"] = (existing_notes + " " + data["notes"]).strip()

    if updates:
        await update_pending_client(uid, updates)

    fresh = await get_pending_client(uid) or {**pending, **updates}

    if page_id:
        await _update_notion(page_id, fresh)

    await message.answer(
        f"👥 Клиент обновлён!\n🔮 <b>{fresh.get('name')}</b>\n\n{_card(fresh)}\n\n"
        f"Можешь прислать ещё или нажать Готово.",
        reply_markup=_collecting_kb(uid),
        parse_mode="HTML",
    )


async def handle_client_photo_input(message: Message, image_b64: str, pending: dict) -> None:
    """Фото в режиме collecting — Vision извлекает контакты, накапливает."""
    from arcana.pending_clients import update_pending_client, get_pending_client

    uid = message.from_user.id
    page_id = pending.get("page_id")

    raw = await ask_claude_vision(
        "Извлеки контакты, имя и день рождения если видны.",
        image_b64,
        system=VISION_CONTACT,
    )
    data = _parse_json_safe(raw) if raw else {}

    updates: Dict[str, Any] = {}
    new_contacts = data.get("contacts") or []
    if new_contacts:
        updates["contacts"] = new_contacts  # накапливается
    if data.get("name") and not pending.get("name"):
        updates["name"] = data["name"]
    bday = (data.get("birthday") or "").strip()
    if bday and not pending.get("birthday"):
        updates["birthday"] = bday

    if updates:
        await update_pending_client(uid, updates)

    fresh = await get_pending_client(uid) or {**pending, **updates}

    if page_id:
        await _update_notion(page_id, fresh)

    await message.answer(
        f"📸 Контакт добавлен\n\n{_card(fresh)}\n\n"
        f"Добавь ещё или нажми Готово.",
        reply_markup=_collecting_kb(uid),
        parse_mode="HTML",
    )


async def handle_debts(message: Message, user_notion_id: str = "") -> None:
    items = await _repo.all_debts(user_notion_id=user_notion_id)
    if not items:
        await message.answer("✅ Долгов нет.")
        return

    total_debt = sum(i.debt for i in items)
    lines = [
        f"• {i.client_label} — {i.description}: <b>{i.debt:,.0f}₽</b>"
        for i in items
    ]
    await message.answer(
        "⚠️ <b>Долги клиентов:</b>\n\n" +
        "\n".join(lines) +
        f"\n\n💸 Итого: <b>{total_debt:,.0f}₽</b>",
        parse_mode="HTML",
    )


# ── Callback handlers ─────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("client_create_from_search:"))
async def cb_create_from_search(callback: CallbackQuery, user_notion_id: str = "") -> None:
    """«Не найден» → юзер нажал [➕ Создать] → создаём в Notion → collecting."""
    uid = int(callback.data.split(":", 1)[1])
    if uid != callback.from_user.id:
        return
    await callback.answer()

    from arcana.pending_clients import get_pending_client, update_pending_client

    pending = await get_pending_client(uid)
    if not pending:
        await callback.message.edit_text("⏱ Сессия истекла.")
        return

    name = pending.get("name") or ""
    user_nid = pending.get("user_notion_id") or user_notion_id
    today = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")

    page_id = await _repo.add(name=name, date=today, user_notion_id=user_nid)
    if not page_id:
        await callback.message.edit_text("⚠️ Ошибка создания в Notion.")
        return

    await update_pending_client(uid, {"step": "collecting", "page_id": page_id})
    pending_stub = {"name": name, "contacts": [], "request": "", "notes": ""}

    await callback.message.edit_text(
        f"👥 Клиент создан!\n🔮 <b>{name}</b>\n🟢 Активный\n\n{_card(pending_stub)}\n\n"
        f"Скинь инфу: контакт, запрос, заметки.",
        reply_markup=_collecting_kb(uid),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("client_update_existing:"))
async def cb_update_existing(callback: CallbackQuery, user_notion_id: str = "") -> None:
    """«Да, это она» → дополняем существующую карточку → collecting."""
    uid = int(callback.data.split(":", 1)[1])
    if uid != callback.from_user.id:
        return
    await callback.answer()

    from arcana.pending_clients import update_pending_client, get_pending_client
    await update_pending_client(uid, {"step": "collecting"})
    pending = await get_pending_client(uid) or {}

    await callback.message.edit_text(
        f"👥 Дополняем карточку\n🔮 <b>{pending.get('name')}</b>\n\n{_card(pending)}\n\n"
        f"Скинь новые контакты, запрос или заметки.",
        reply_markup=_collecting_kb(uid),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("client_create_new:"))
async def cb_create_new(callback: CallbackQuery, user_notion_id: str = "") -> None:
    """«Нет, новый клиент» → создаём нового → collecting."""
    uid = int(callback.data.split(":", 1)[1])
    if uid != callback.from_user.id:
        return
    await callback.answer()

    from arcana.pending_clients import get_pending_client, update_pending_client

    pending = await get_pending_client(uid)
    if not pending:
        await callback.message.edit_text("⏱ Сессия истекла.")
        return

    name = pending.get("name") or ""
    user_nid = pending.get("user_notion_id") or user_notion_id
    today = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")

    page_id = await _repo.add(name=name, date=today, user_notion_id=user_nid)
    if not page_id:
        await callback.message.edit_text("⚠️ Ошибка создания.")
        return

    await update_pending_client(uid, {
        "step": "collecting",
        "page_id": page_id,
        "contacts": [],
        "request": "",
        "notes": "",
    })
    pending_stub = {"name": name, "contacts": [], "request": "", "notes": ""}

    await callback.message.edit_text(
        f"👥 Новый клиент создан!\n🔮 <b>{name}</b>\n🟢 Активный\n\n{_card(pending_stub)}\n\n"
        f"Скинь инфу: контакт, запрос, заметки.",
        reply_markup=_collecting_kb(uid),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("client_done:"))
async def cb_done(callback: CallbackQuery, user_notion_id: str = "") -> None:
    """Завершить сбор — показать итоговую карточку, очистить pending."""
    uid = int(callback.data.split(":", 1)[1])
    if uid != callback.from_user.id:
        return
    await callback.answer("Сохранено")

    from arcana.pending_clients import get_pending_client, delete_pending_client
    pending = await get_pending_client(uid) or {}
    await delete_pending_client(uid)

    await callback.message.edit_text(
        f"✅ <b>{pending.get('name') or 'Клиент'}</b> сохранён\n\n{_card(pending)}",
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("client_cancel:"))
async def cb_cancel(callback: CallbackQuery, user_notion_id: str = "") -> None:
    uid = int(callback.data.split(":", 1)[1])
    if uid != callback.from_user.id:
        return
    await callback.answer("Отменено")

    from arcana.pending_clients import delete_pending_client
    await delete_pending_client(uid)
    await callback.message.edit_text("❌ Отменено.")
