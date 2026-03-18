"""nexus/handlers/delete.py"""
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
logger = logging.getLogger("nexus.delete")

# db_id → (date_field, label)
NEXUS_TARGETS = {
    "finance":  (config.nexus.db_finance, "Дата",    "💰 Финансы"),
    "tasks":    (config.nexus.db_tasks,   "Дедлайн", "✅ Задачи"),
    "notes":    (config.nexus.db_notes,   "Дата",    "💡 Заметки"),
}

PARSE_TARGET_SYSTEM = """Определи что удаляем в боте Nexus. Ответь ТОЛЬКО одним словом:
finance  — финансы, расходы, доходы, операции
tasks    — задачи
notes    — заметки
unknown  — непонятно"""

# Временное хранилище page_ids до подтверждения (в памяти, не персистентно)
_pending: dict[int, list[str]] = {}


async def handle_delete(message: Message, text: str) -> None:
    from core.claude_client import ask_claude

    target = (await ask_claude(text, system=PARSE_TARGET_SYSTEM, max_tokens=10)).strip().lower()
    if target not in NEXUS_TARGETS:
        await message.answer("⚠️ Уточни что удалить: финансы, задачи или заметки.")
        return

    db_id, date_field, label = NEXUS_TARGETS[target]
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
        await message.answer(f"📭 Записей не найдено.")
        return

    # Показываем что нашли
    previews = [format_page_preview(p, date_field=date_field) for p in pages[:10]]
    preview_text = "\n".join(f"• {p}" for p in previews if p)
    if len(pages) > 10:
        preview_text += f"\n... и ещё {len(pages) - 10}"

    # Сохраняем IDs
    uid = message.from_user.id
    _pending[uid] = [p["id"] for p in pages]

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"🗑 Да, удалить ({len(pages)})", callback_data=f"del_confirm_nexus"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="del_cancel"),
    ]])

    await message.answer(
        f"{label} — найдено {len(pages)} записей:\n\n{preview_text}\n\nУдалить?",
        reply_markup=kb,
    )


@router.callback_query(F.data == "del_confirm_nexus")
async def confirm_delete(call: CallbackQuery) -> None:
    uid = call.from_user.id
    page_ids = _pending.pop(uid, [])
    if not page_ids:
        await call.answer("Ничего не найдено.")
        return
    deleted = await delete_pages(page_ids)
    await call.message.edit_text(f"✅ Удалено {deleted} записей.")
    await call.answer()


@router.callback_query(F.data == "del_cancel")
async def cancel_delete(call: CallbackQuery) -> None:
    _pending.pop(call.from_user.id, None)
    await call.message.edit_text("❌ Отмена. Ничего не удалено.")
    await call.answer()
