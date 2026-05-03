"""arcana/handlers/client_photo.py — фото клиентов: /client_photo и reply-flow.

Два сценария:
  A) /client_photo → бот спрашивает имя → находит/создаёт клиента →
     просит фото → загружает в Cloudinary → пишет URL в Notion 👥 Клиенты.Фото.
  B) Reply фото на сообщение бота, у которого page_type='client' в
     core.message_pages → подтверждение → загрузка → запись.
"""
from __future__ import annotations

import logging
from typing import Optional

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from core.cloudinary_client import cloudinary_upload
from core.message_pages import get_message_page
from core.notion_client import find_or_create_client, update_page

from arcana.pending_client_photo import (
    delete as drop_pending,
    get as get_pending,
    save as save_pending,
)

logger = logging.getLogger("arcana.client_photo")

router = Router()


# ── Reactions ────────────────────────────────────────────────────────────────

REACTION_START = "📸"
REACTION_DONE = "💅"


async def _react(message: Message, emoji: str) -> None:
    try:
        from arcana.handlers.reactions import react
        await react(message, emoji)
    except Exception:
        pass


# ── Commands ─────────────────────────────────────────────────────────────────

@router.message(Command("client_photo"))
async def cmd_client_photo(message: Message, user_notion_id: str = "") -> None:
    uid = message.from_user.id
    # Если /client_photo прислан реплаем на сообщение бота, у которого page_type='client',
    # сразу прыгаем на await_photo для того клиента.
    if message.reply_to_message and message.reply_to_message.from_user and message.reply_to_message.from_user.is_bot:
        mp = await get_message_page(message.chat.id, message.reply_to_message.message_id)
        if mp and mp.get("page_type") == "client":
            await save_pending(uid, {
                "step": "await_photo",
                "client_id": mp["page_id"],
                "client_name": "клиент",
            })
            await _react(message, REACTION_START)
            await message.answer("📸 Пришли фото — привяжу к этому клиенту.")
            return

    await save_pending(uid, {"step": "await_name"})
    await _react(message, REACTION_START)
    await message.answer(
        "📸 Кому добавить фото? Напиши имя клиента "
        "(или сначала сделай reply на сообщение клиента и пришли /client_photo)."
    )


# ── Text handler — приходит из base.py роутера ────────────────────────────────

async def handle_pending_text(message: Message, text: str, user_notion_id: str = "") -> bool:
    """Возвращает True если перехватили этап «await_name»."""
    uid = message.from_user.id
    pending = await get_pending(uid)
    if not pending or pending.get("step") != "await_name":
        return False
    name = (text or "").strip()
    if not name:
        await message.answer("Напиши имя клиента или /cancel.")
        return True
    page_id = await find_or_create_client(name, user_notion_id=user_notion_id)
    if not page_id:
        await message.answer(f"Не получилось найти/создать клиента «{name}».")
        await drop_pending(uid)
        return True
    await save_pending(uid, {
        "step": "await_photo",
        "client_id": page_id,
        "client_name": name,
    })
    await message.answer(f"✨ Найдена: <b>{name}</b>. Пришли фото.", parse_mode="HTML")
    return True


# ── Photo handler — приходит из base.py роутера до handle_tarot_photo ─────────

async def handle_pending_photo(message: Message, user_notion_id: str = "") -> bool:
    """Если есть pending await_photo — загружаем и пишем в Notion.
    Возвращает True, если фото обработано (и tarot-флоу пропустить).
    """
    uid = message.from_user.id
    pending = await get_pending(uid)
    # Reply-on-photo: если pending'а нет, но это reply на сообщение бота с page_type='client'
    if not pending and message.reply_to_message and message.reply_to_message.from_user and message.reply_to_message.from_user.is_bot:
        mp = await get_message_page(message.chat.id, message.reply_to_message.message_id)
        if mp and mp.get("page_type") == "client" and message.photo:
            photo = message.photo[-1]
            await save_pending(uid, {
                "step": "await_confirm",
                "client_id": mp["page_id"],
                "client_name": "клиент",
                "file_id": photo.file_id,
            })
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Да", callback_data=f"client_photo_confirm:{uid}"),
                InlineKeyboardButton(text="❌ Отмена", callback_data=f"client_photo_cancel:{uid}"),
            ]])
            await message.answer("Привязать это фото к клиенту?", reply_markup=kb)
            return True
        return False
    if not pending or pending.get("step") != "await_photo" or not message.photo:
        return False

    await _attach_photo(message, pending, user_notion_id=user_notion_id)
    return True


async def _attach_photo(
    message: Message,
    pending: dict,
    user_notion_id: str = "",
    file_id: Optional[str] = None,
) -> None:
    uid = message.from_user.id
    try:
        if file_id is None:
            file_id = message.photo[-1].file_id
        file = await message.bot.get_file(file_id)
        bio = await message.bot.download_file(file.file_path)
        url = await cloudinary_upload(
            bio.read(),
            filename=f"client-{pending['client_id'][:8]}.jpg",
            folder="arcana-clients",
        )
        if not url:
            await message.answer("Не удалось загрузить фото (Cloudinary не настроен).")
            await drop_pending(uid)
            return
        await update_page(pending["client_id"], {"Фото": {"url": url}})
        await drop_pending(uid)
        await _react(message, REACTION_DONE)
        await message.answer(
            f"✨ Фото добавлено к <b>{pending.get('client_name') or 'клиенту'}</b>.",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.exception("client photo upload failed: %s", e)
        await drop_pending(uid)
        await message.answer(f"Ошибка при сохранении фото: {e}")


# ── Callbacks ────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("client_photo_confirm:"))
async def cb_confirm(cb: CallbackQuery, user_notion_id: str = "") -> None:
    uid = int(cb.data.split(":", 1)[1])
    if cb.from_user.id != uid:
        return
    pending = await get_pending(uid)
    if not pending or pending.get("step") != "await_confirm":
        await cb.answer("Запрос устарел.")
        return
    await cb.answer("Загружаю...")
    await _attach_photo(cb.message, pending, user_notion_id=user_notion_id, file_id=pending.get("file_id"))


@router.callback_query(F.data.startswith("client_photo_cancel:"))
async def cb_cancel(cb: CallbackQuery, user_notion_id: str = "") -> None:
    uid = int(cb.data.split(":", 1)[1])
    if cb.from_user.id != uid:
        return
    await drop_pending(uid)
    await cb.answer("Отменено.")
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
