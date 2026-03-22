"""nexus/handlers/memory.py — тонкий слой Nexus (вся логика в core/memory.py)."""
from __future__ import annotations

import logging
import os
from typing import Dict

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery

import core.memory as mem
from core.notion_client import page_create

logger = logging.getLogger("nexus.memory")
router = Router()

BOT_LABEL = "☀️ Nexus"

# Pending auto-suggest: uid → {"text": ..., "user_notion_id": ...}
_pending_auto: Dict[int, dict] = {}


# ── Handlers (вызываются из process_item / nexus_bot) ─────────────────────────

async def handle_memory_save(
    message: Message,
    data: dict,
    user_notion_id: str = "",
) -> None:
    text = data.get("text", message.text or "")
    await mem.save_memory(message, text, user_notion_id, BOT_LABEL)


async def handle_memory_search(
    message: Message,
    data: dict,
    user_notion_id: str = "",
) -> None:
    query = (data.get("query") or data.get("text") or "").strip()
    await mem.search_memory(message, query, user_notion_id, del_prefix="mem_del")


async def handle_memory_deactivate(
    message: Message,
    data: dict,
    user_notion_id: str = "",
) -> None:
    hint = (data.get("hint") or data.get("text") or "").strip()
    await mem.deactivate_memory(message, hint, user_notion_id)


async def handle_memory_delete(
    message: Message,
    data: dict,
    user_notion_id: str = "",
) -> None:
    hint = (data.get("hint") or data.get("text") or "").strip()
    await mem.delete_memory(
        message, hint, user_notion_id,
        del_prefix="mem_del", cancel_cb="mem_cancel",
    )


async def handle_memory_auto_suggest(
    message: Message,
    text: str,
    user_notion_id: str = "",
) -> None:
    await mem.auto_suggest_memory(
        message, text, user_notion_id, BOT_LABEL, _pending_auto,
        yes_prefix="mem_auto_yes", no_prefix="mem_auto_no",
    )


async def suggest_memory(message: Message, text: str, user_notion_id: str = "") -> None:
    """Удобная обёртка для вызова из других хендлеров (tasks.py и т.д.)."""
    await mem.auto_suggest_memory(
        message, text, user_notion_id, BOT_LABEL, _pending_auto,
        yes_prefix="mem_auto_yes", no_prefix="mem_auto_no",
    )


# ── Callbacks ────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("mem_toggle:"))
async def cb_mem_toggle(call: CallbackQuery) -> None:
    await call.answer()
    uid = call.from_user.id
    page_id = call.data.split(":", 1)[1]
    selected = mem._mem_selected.setdefault(uid, set())
    if page_id in selected:
        selected.discard(page_id)
    else:
        selected.add(page_id)
    pages = mem._mem_delete_pages.get(uid, [])
    if not pages:
        await call.message.edit_text("⏱ Сессия истекла.")
        return
    await call.message.edit_reply_markup(reply_markup=mem._build_delete_keyboard(uid, pages))


@router.callback_query(F.data.startswith("mem_delete_selected:"))
async def cb_mem_delete_selected(call: CallbackQuery) -> None:
    await call.answer()
    uid = call.from_user.id
    selected = mem._mem_selected.pop(uid, set())
    pages = mem._mem_delete_pages.pop(uid, [])
    if not selected:
        await call.message.edit_text("☐ Ничего не выбрано.")
        return
    targets = [p for p in pages if p["id"] in selected]
    deleted = 0
    for page in targets:
        try:
            await mem._archive_page(page["id"])
            deleted += 1
        except Exception as e:
            logger.error("cb_mem_delete_selected: %s", e)
    n = deleted
    noun = "запись" if n == 1 else "записи" if n < 5 else "записей"
    await call.message.edit_text(f"🗑 Удалена {n} {noun} из памяти.")


@router.callback_query(F.data.startswith("mem_delete_all:"))
async def cb_mem_delete_all(call: CallbackQuery) -> None:
    await call.answer()
    uid = call.from_user.id
    mem._mem_selected.pop(uid, None)
    pages = mem._mem_delete_pages.pop(uid, [])
    if not pages:
        await call.message.edit_text("⏱ Сессия истекла.")
        return
    deleted = 0
    for page in pages:
        try:
            await mem._archive_page(page["id"])
            deleted += 1
        except Exception as e:
            logger.error("cb_mem_delete_all: %s", e)
    n = deleted
    noun = "запись" if n == 1 else "записи" if n < 5 else "записей"
    await call.message.edit_text(f"🗑 Удалена {n} {noun} из памяти.")


@router.callback_query(F.data.startswith("mem_cancel:"))
async def cb_mem_cancel(call: CallbackQuery) -> None:
    await call.answer()
    uid = call.from_user.id
    mem._mem_selected.pop(uid, None)
    mem._mem_delete_pages.pop(uid, None)
    await call.message.edit_text("❌ Отмена.")


@router.callback_query(F.data.startswith("mem_auto_yes:"))
async def cb_mem_auto_yes(call: CallbackQuery) -> None:
    await call.answer()
    uid = int(call.data.split(":", 1)[1])
    pending = _pending_auto.pop(uid, None)
    if not pending:
        await call.message.edit_text("⏱ Сессия истекла.")
        return
    fact, category, связь, ключ = await mem._parse_fact(pending["text"])
    db_id = os.environ.get("NOTION_DB_MEMORY")
    if not db_id:
        await call.message.edit_text("⚠️ NOTION_DB_MEMORY не задан")
        return
    props = mem._build_props(fact, category, связь, ключ, BOT_LABEL, pending.get("user_notion_id", ""))
    result = await page_create(db_id, props)
    if result:
        cat_label = f" [{category}]" if category else ""
        await call.message.edit_text(f"🧠 Запомнила{cat_label}: {fact}")
    else:
        await call.message.edit_text("⚠️ Ошибка записи в Notion")


@router.callback_query(F.data.startswith("mem_auto_no:"))
async def cb_mem_auto_no(call: CallbackQuery) -> None:
    await call.answer()
    uid = int(call.data.split(":", 1)[1])
    _pending_auto.pop(uid, None)
    await call.message.edit_text("✗ Не сохраняю.")
