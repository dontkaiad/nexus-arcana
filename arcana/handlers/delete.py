"""arcana/handlers/delete.py"""
from __future__ import annotations

import logging
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram import Router, F
from core.deleter import (
    parse_delete_intent, find_pages_to_delete,
    delete_pages, format_page_preview
)
from core.config import config

router = Router()
logger = logging.getLogger("arcana.delete")

ARCANA_TARGETS = {
    "sessions": (config.arcana.db_sessions, "Дата и время", "🃏 Сеансы"),
    "rituals":  (config.arcana.db_rituals,  "Дата",         "🕯️ Ритуалы"),
    "clients":  (config.arcana.db_clients,  "Первое обращение", "👤 Клиенты"),
}

PARSE_TARGET_SYSTEM = """Определи что удаляем в боте Arcana. Ответь ТОЛЬКО одним словом:
sessions — сеансы, расклады, таро
rituals  — ритуалы
clients  — клиенты
unknown  — непонятно"""

_pending: dict[int, list[str]] = {}


async def handle_delete(message: Message, text: str) -> None:
    from core.claude_client import ask_claude

    target = (await ask_claude(text, system=PARSE_TARGET_SYSTEM, max_tokens=10)).strip().lower()
    if target not in ARCANA_TARGETS:
        await message.answer("⚠️ Уточни что удалить: сеансы, ритуалы или клиенты.")
        return

    db_id, date_field, label = ARCANA_TARGETS[target]
    intent = await parse_delete_intent(text)

    pages = await find_pages_to_delete(
        db_id=db_id,
        date_field=date_field,
        scope=intent["scope"],
        date=intent.get("date"),
        month=intent.get("month"),
        count=int(intent.get("count") or 1),
    )

    if not pages:
        await message.answer("📭 Записей не найдено.")
        return

    previews = [format_page_preview(p, date_field=date_field) for p in pages[:10]]
    preview_text = "\n".join(f"• {p}" for p in previews if p)
    if len(pages) > 10:
        preview_text += f"\n... и ещё {len(pages) - 10}"

    uid = message.from_user.id
    _pending[uid] = [p["id"] for p in pages]

    from core.utils import cancel_button
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        cancel_button(f"🗑 Да, удалить ({len(pages)})", "del_confirm_arcana"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="del_cancel_arcana"),
    ]])

    await message.answer(
        f"{label} — найдено {len(pages)} записей:\n\n{preview_text}\n\nУдалить?",
        reply_markup=kb,
    )


@router.callback_query(F.data == "del_confirm_arcana")
async def confirm_delete(call: CallbackQuery) -> None:
    uid = call.from_user.id
    page_ids = _pending.pop(uid, [])
    if not page_ids:
        await call.answer("Ничего не найдено.")
        return
    deleted = await delete_pages(page_ids)
    await call.message.edit_text(f"✅ Удалено {deleted} записей.")
    await call.answer()


@router.callback_query(F.data == "del_cancel_arcana")
async def cancel_delete(call: CallbackQuery) -> None:
    _pending.pop(call.from_user.id, None)
    await call.message.edit_text("❌ Отмена. Ничего не удалено.")
    await call.answer()
