"""arcana/handlers/intent_resolve.py — разрешение неоднозначных интентов.

Сейчас один кейс: «сделать ритуал на Маше» без глагола времени и структуры —
надо переспросить, это запланировать (Работа) или уже провела (Ритуал).
"""
from __future__ import annotations

import hashlib
import logging
import time
from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

logger = logging.getLogger("arcana.intent_resolve")

router = Router()


def _slug(text: str) -> str:
    return hashlib.sha1(
        f"{text}{int(time.time())}".encode("utf-8")
    ).hexdigest()[:12]


def _disambig_kb(slug: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="📅 Запланировать (Работа)",
            callback_data=f"intent_planned:{slug}",
        ),
        InlineKeyboardButton(
            text="✅ Уже провела (Ритуал)",
            callback_data=f"intent_done:{slug}",
        ),
    ]])


async def ask_ritual_disambiguation(
    message: Message, text: str, user_notion_id: str,
) -> None:
    """Сохраняем text + user_notion_id в pending_tarot, шлём 2 кнопки."""
    from arcana.pending_tarot import save_pending
    slug = _slug(text)
    await save_pending(message.from_user.id, {
        "type": "intent_resolve_pending",
        "slug": slug,
        "text": text,
        "user_notion_id": user_notion_id,
    })
    await message.answer(
        "🤔 Это надо запланировать или уже сделала?",
        reply_markup=_disambig_kb(slug),
    )


async def _resume_with_handler(
    call: CallbackQuery, slug: str, handler_kind: str,
) -> None:
    from arcana.pending_tarot import get_pending, delete_pending
    pending = await get_pending(call.from_user.id) or {}
    if pending.get("slug") != slug or pending.get("type") != "intent_resolve_pending":
        return
    text = pending.get("text") or ""
    user_notion_id = pending.get("user_notion_id") or ""
    await delete_pending(call.from_user.id)
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    if handler_kind == "planned":
        from arcana.handlers.works import handle_add_work
        await handle_add_work(call.message, text, user_notion_id)
    elif handler_kind == "done":
        from arcana.handlers.rituals import handle_add_ritual
        await handle_add_ritual(call.message, text, user_notion_id)


@router.callback_query(F.data.startswith("intent_planned:"))
async def cb_intent_planned(call: CallbackQuery) -> None:
    await call.answer()
    slug = call.data.split(":", 1)[1]
    await _resume_with_handler(call, slug, "planned")


@router.callback_query(F.data.startswith("intent_done:"))
async def cb_intent_done(call: CallbackQuery) -> None:
    await call.answer()
    slug = call.data.split(":", 1)[1]
    await _resume_with_handler(call, slug, "done")
