"""arcana/handlers/client_photo.py — фото клиентов: /client_photo и reply-flow.

Два сценария:
  A) /client_photo → бот спрашивает имя → находит/создаёт клиента →
     просит фото → загружает в Cloudinary → пишет URL в PG 👥 Клиенты.photo_url.
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
from core.client_resolve import find_or_create_client

from arcana.pending_client_photo import (
    delete as drop_pending,
    get as get_pending,
    save as save_pending,
)

logger = logging.getLogger("arcana.client_photo")

router = Router()

REACTION_START = "📸"
REACTION_DONE = "💅"


async def _react(message: Message, emoji: str) -> None:
    try:
        from arcana.handlers.reactions import react
        await react(message, emoji)
    except Exception:
        pass


async def _pg_clients():
    from arcana.repos.pg_clients_repo import PgClientsRepo
    return PgClientsRepo()


@router.message(Command("client_photo"))
async def cmd_client_photo(message: Message, user_notion_id: str = "") -> None:
    uid = message.from_user.id
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
    page_id, _ = await find_or_create_client(name, user_notion_id=user_notion_id)
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


async def handle_pending_photo(message: Message, user_notion_id: str = "") -> bool:
    """Если есть pending await_photo — загружаем и пишем в PG."""
    uid = message.from_user.id
    pending = await get_pending(uid)
    if not pending and message.reply_to_message and message.reply_to_message.from_user and message.reply_to_message.from_user.is_bot:
        mp = await get_message_page(message.chat.id, message.reply_to_message.message_id)
        if mp and message.photo:
            import time as _time
            age = _time.time() - float(mp.get("created_at") or 0)
            page_type = mp.get("page_type")

            if page_type == "ritual":
                await attach_photo_to_ritual(message, mp["page_id"], silent=True)
                return True

            if page_type == "client":
                cap = (message.caption or "").strip()
                if cap:
                    await attach_photo_to_client_objects(message, mp["page_id"], cap, silent=True)
                    return True
                if age < 60:
                    await attach_photo_to_client(message, mp["page_id"], silent=True)
                    return True
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
        client_id = pending["client_id"]
        url = await cloudinary_upload(
            bio.read(),
            filename=f"client-{str(client_id)[:8]}.jpg",
            folder="arcana-clients",
        )
        if not url:
            await message.answer("Не удалось загрузить фото (Cloudinary не настроен).")
            await drop_pending(uid)
            return
        # Write to PG
        try:
            pg_id = int(client_id)
            await (await _pg_clients()).update_profile(pg_id, photo_url=url)
        except Exception as e:
            logger.warning("client photo PG write failed: %s", e)
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


async def attach_photo_to_client_objects(
    message: Message,
    client_id: str,
    note: str = "",
    *,
    silent: bool = False,
) -> bool:
    """Cloudinary upload + append `URL | note` в PG поле «object_photos»."""
    if not message.photo or not client_id:
        return False
    try:
        from core.client_object_photos import append as _append

        file_id = message.photo[-1].file_id
        file = await message.bot.get_file(file_id)
        bio = await message.bot.download_file(file.file_path)
        url = await cloudinary_upload(
            bio.read(),
            filename=f"obj-{str(client_id)[:8]}.jpg",
            folder="arcana-client-objects",
        )
        if not url:
            if not silent:
                await message.answer("Cloudinary не настроен — фото не прикреплено.")
            return False

        repo = await _pg_clients()
        pg_id = int(client_id)
        existing = await repo.get_object_photos(pg_id)
        new_raw, _items = _append(existing, url, note)
        await repo.update_profile(pg_id, object_photos=new_raw)
        await _react(message, REACTION_DONE)
        return True
    except Exception as e:
        logger.exception("attach_photo_to_client_objects failed: %s", e)
        if not silent:
            await message.answer(f"Ошибка при сохранении фото: {e}")
        return False


async def attach_photo_to_ritual(
    message: Message,
    ritual_id: str,
    *,
    silent: bool = False,
) -> bool:
    """Аналог attach_photo_to_client для ритуалов (PG photo_url)."""
    if not message.photo or not ritual_id:
        return False
    try:
        from arcana.repos.pg_rituals_repo import PgRitualsRepo
        from arcana.repos.rituals_tables import rituals as t_rituals
        from core.db import get_engine
        from sqlalchemy import select

        file_id = message.photo[-1].file_id
        file = await message.bot.get_file(file_id)
        bio = await message.bot.download_file(file.file_path)
        url = await cloudinary_upload(
            bio.read(),
            filename=f"ritual-{str(ritual_id)[:8]}.jpg",
            folder="arcana-rituals",
        )
        if not url:
            if not silent:
                await message.answer("Cloudinary не настроен — фото не прикреплено.")
            return False

        import asyncio

        def _write():
            with get_engine().begin() as conn:
                conn.execute(
                    t_rituals.update()
                    .where(t_rituals.c.id == int(ritual_id))
                    .values(photo_url=url)
                )

        await asyncio.to_thread(_write)
        await _react(message, REACTION_DONE)
        return True
    except Exception as e:
        logger.exception("attach_photo_to_ritual failed: %s", e)
        if not silent:
            await message.answer(f"Ошибка при сохранении фото: {e}")
        return False


async def attach_photo_to_client(
    message: Message,
    client_id: str,
    *,
    silent: bool = False,
) -> bool:
    """Прямая привязка: качаем фото из message.photo[-1], грузим в Cloudinary,
    пишем URL в PG 👥 Клиенты.photo_url."""
    if not message.photo or not client_id:
        return False
    try:
        file_id = message.photo[-1].file_id
        file = await message.bot.get_file(file_id)
        bio = await message.bot.download_file(file.file_path)
        url = await cloudinary_upload(
            bio.read(),
            filename=f"client-{str(client_id)[:8]}.jpg",
            folder="arcana-clients",
        )
        if not url:
            if not silent:
                await message.answer("Cloudinary не настроен — фото не прикреплено.")
            return False
        pg_id = int(client_id)
        await (await _pg_clients()).update_profile(pg_id, photo_url=url)
        await _react(message, REACTION_DONE)
        return True
    except Exception as e:
        logger.exception("attach_photo_to_client failed: %s", e)
        if not silent:
            await message.answer(f"Ошибка при сохранении фото: {e}")
        return False


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
