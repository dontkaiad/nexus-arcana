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

@router.callback_query(F.data.startswith("mem_del:"))
async def cb_mem_del(call: CallbackQuery) -> None:
    await call.answer()
    page_id = call.data.split(":", 1)[1]
    try:
        await mem._archive_page(page_id)
        await call.message.edit_text("🗑 Запись удалена из памяти.")
    except Exception as e:
        logger.error("cb_mem_del: %s", e)
        await call.message.edit_text(f"⚠️ Ошибка удаления: {e}")


@router.callback_query(F.data == "mem_cancel")
async def cb_mem_cancel(call: CallbackQuery) -> None:
    await call.answer()
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
        await call.message.edit_text(f"🧠 Запомнила: <b>{ключ}</b> — {fact}")
    else:
        await call.message.edit_text("⚠️ Ошибка записи в Notion")


@router.callback_query(F.data.startswith("mem_auto_no:"))
async def cb_mem_auto_no(call: CallbackQuery) -> None:
    await call.answer()
    uid = int(call.data.split(":", 1)[1])
    _pending_auto.pop(uid, None)
    await call.message.edit_text("✗ Не сохраняю.")
