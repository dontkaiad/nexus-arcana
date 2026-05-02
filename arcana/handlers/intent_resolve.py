"""arcana/handlers/intent_resolve.py — разрешение неоднозначных интентов.

Кейсы:
1. «сделать ритуал на Маше» без глагола времени и структуры —
   запланировать (Работа) или уже провела (Ритуал).
2. «сделать X» где X не похож на практику — это бытовая задача (Nexus)
   или общая работа практики? (по CLAUDE.md бытовое → редирект в Nexus).
"""
from __future__ import annotations

import hashlib
import logging
import re as _re
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


# ── Practice vs Nexus redirect ───────────────────────────────────────────────

_PRACTICE_KEYWORDS = _re.compile(
    r"\b("
    r"ритуал\w*|расклад\w*|разложит\w*|разложу|сеанс\w*|приворот\w*|"
    r"очищен\w+|очистит\w*|защит\w+|гадан\w+|погадат\w*|таро|"
    r"гримуар\w*|свеч\w+|колод\w+|заговор\w*|руни\w+|"
    r"финансов\w+|любовн\w+|защитн\w+|отворот\w*|клиент\w*|"
    r"триплет\w*|дно\s+колоды|карты"
    r")\b",
    _re.IGNORECASE,
)


def looks_like_practice(text: str) -> bool:
    """Текст содержит хотя бы один маркер эзотерической практики."""
    return bool(_PRACTICE_KEYWORDS.search(text or ""))


def _practice_kb(slug: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="✨ Практика (Аркана)",
            callback_data=f"intent_practice:{slug}",
        ),
        InlineKeyboardButton(
            text="☀️ Общая (в Nexus)",
            callback_data=f"intent_nexus:{slug}",
        ),
    ]])


async def ask_practice_or_nexus(
    message: Message, text: str, user_notion_id: str,
) -> None:
    """Текст похож на бытовую задачу — переспросить куда записать."""
    from arcana.pending_tarot import save_pending
    slug = _slug(text)
    await save_pending(message.from_user.id, {
        "type": "practice_or_nexus_pending",
        "slug": slug,
        "text": text,
        "user_notion_id": user_notion_id,
    })
    await message.answer(
        "❓ Это про практику или общая задача?",
        reply_markup=_practice_kb(slug),
    )


async def send_nexus_redirect(message: Message, original_text: str) -> None:
    """Отправить сообщение-редирект в Nexus и НИЧЕГО не сохранять."""
    from core.utils import react
    await message.answer(
        "☀️ Это похоже на бытовую задачу — её лучше записать в Nexus.\n\n"
        "Открой @nexus_kailark_bot и напиши там:\n"
        f"«{original_text}»\n\n"
        "Аркана работает только с практикой: расклады, ритуалы, клиенты."
    )
    await react(message, "😈")


@router.callback_query(F.data.startswith("intent_practice:"))
async def cb_intent_practice(call: CallbackQuery) -> None:
    await call.answer()
    slug = call.data.split(":", 1)[1]
    from arcana.pending_tarot import get_pending, delete_pending
    pending = await get_pending(call.from_user.id) or {}
    if pending.get("slug") != slug or pending.get("type") != "practice_or_nexus_pending":
        return
    text = pending.get("text") or ""
    user_notion_id = pending.get("user_notion_id") or ""
    await delete_pending(call.from_user.id)
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    from arcana.handlers.works import handle_add_work
    await handle_add_work(call.message, text, user_notion_id)


@router.callback_query(F.data.startswith("intent_nexus:"))
async def cb_intent_nexus(call: CallbackQuery) -> None:
    await call.answer()
    slug = call.data.split(":", 1)[1]
    from arcana.pending_tarot import get_pending, delete_pending
    pending = await get_pending(call.from_user.id) or {}
    if pending.get("slug") != slug or pending.get("type") != "practice_or_nexus_pending":
        return
    text = pending.get("text") or ""
    await delete_pending(call.from_user.id)
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await send_nexus_redirect(call.message, text)


# ── Pending: уточнение к старой работе vs новое сообщение ───────────────────

def _clarify_kb(slug: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="✏️ Уточнение",
            callback_data=f"clarify_keep:{slug}",
        ),
        InlineKeyboardButton(
            text="🆕 Новое",
            callback_data=f"clarify_new:{slug}",
        ),
    ]])


async def ask_clarify_or_new(message: Message, text: str, title: str) -> None:
    """У пользователя висит pending-работа, новый текст не похож ни на
    дедлайн-уточнение, ни на явный новый intent — переспросить."""
    from arcana.pending_tarot import save_pending
    slug = _slug(text)
    await save_pending(message.from_user.id, {
        "type": "clarify_or_new_pending",
        "slug": slug,
        "text": text,
    })
    await message.answer(
        f"❓ У тебя есть незавершённая работа «{title}».\n"
        f"Это уточнение к ней или новое сообщение?",
        reply_markup=_clarify_kb(slug),
    )


@router.callback_query(F.data.startswith("clarify_keep:"))
async def cb_clarify_keep(call: CallbackQuery) -> None:
    """«✏️ Уточнение» — отдаём текст в work_preview clarification."""
    await call.answer()
    slug = call.data.split(":", 1)[1]
    from arcana.pending_tarot import get_pending, delete_pending
    pending = await get_pending(call.from_user.id) or {}
    if pending.get("slug") != slug or pending.get("type") != "clarify_or_new_pending":
        return
    text = pending.get("text") or ""
    await delete_pending(call.from_user.id)
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    # Эмулируем «уточнение» — подменяем text сообщения и зовём handler
    call.message.text = text
    call.message.from_user = call.from_user
    from arcana.handlers.work_preview import handle_work_clarification
    await handle_work_clarification(call.message)


@router.callback_query(F.data.startswith("clarify_new:"))
async def cb_clarify_new(call: CallbackQuery) -> None:
    """«🆕 Новое» — дропаем work-pending, шлём текст в обычный route."""
    await call.answer()
    slug = call.data.split(":", 1)[1]
    from arcana.pending_tarot import get_pending, delete_pending
    pending = await get_pending(call.from_user.id) or {}
    if pending.get("slug") != slug or pending.get("type") != "clarify_or_new_pending":
        return
    text = pending.get("text") or ""
    await delete_pending(call.from_user.id)
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    from arcana.handlers.work_preview import drop_pending
    drop_pending(call.from_user.id)
    call.message.text = text
    call.message.from_user = call.from_user
    from arcana.handlers.base import route_message
    await route_message(call.message, user_notion_id="")


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
