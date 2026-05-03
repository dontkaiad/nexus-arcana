"""arcana/handlers/memory.py — тонкий слой Arcana (вся логика в core/memory.py)."""
from __future__ import annotations

import logging
import os
import re
from collections import defaultdict
from typing import Dict

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery

import core.memory as mem
from core.notion_client import page_create

logger = logging.getLogger("arcana.memory")
router = Router()

BOT_LABEL = "🌒 Arcana"

# Pending auto-suggest: uid → {"text": ..., "user_notion_id": ...}
_pending_auto: Dict[int, dict] = {}

# Auto-suggest счётчик повторений (in-memory как в Nexus tasks).
# Ключ: (uid, intent, нормализованный признак темы) → счётчик.
_autosuggest_counts: dict = defaultdict(lambda: defaultdict(int))
_AUTOSUGGEST_MIN_REPEATS = 3


def _norm_topic(text: str) -> str:
    """Грубый нормализатор «темы»: lowercase, токены ≥3 букв через пробел."""
    return " ".join(re.findall(r"\w{3,}", (text or "").lower()))


async def maybe_auto_suggest(
    message: Message,
    intent: str,
    text: str,
    user_notion_id: str = "",
) -> None:
    """Бамп счётчика повторений по (intent, тема). На 3-м повторении предлагает
    запомнить через handle_memory_auto_suggest. Триггерится только для
    session_done / client_info / ritual_done."""
    if intent not in ("session_done", "session", "client_info",
                       "ritual", "ritual_done"):
        return
    topic = _norm_topic(text)
    if len(topic) < 4:
        return
    uid = message.from_user.id
    bucket = _autosuggest_counts[uid]
    key = f"{intent}::{topic}"
    bucket[key] += 1
    if bucket[key] == _AUTOSUGGEST_MIN_REPEATS:
        try:
            await handle_memory_auto_suggest(message, text, user_notion_id)
        except Exception as e:
            logger.warning("maybe_auto_suggest failed: %s", e)


# ── Handlers (вызываются из arcana/handlers/base.py) ──────────────────────────

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
    await mem.search_memory(message, query, user_notion_id, del_prefix="arcmem_del")


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
        del_prefix="arcmem_del", cancel_cb="arcmem_cancel",
    )


async def handle_memory_auto_suggest(
    message: Message,
    text: str,
    user_notion_id: str = "",
) -> None:
    await mem.auto_suggest_memory(
        message, text, user_notion_id, BOT_LABEL, _pending_auto,
        yes_prefix="arcmem_auto_yes", no_prefix="arcmem_auto_no",
    )


# ── Callbacks ────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("arcmem_del:"))
async def cb_arcmem_del(call: CallbackQuery) -> None:
    await call.answer()
    page_id = call.data.split(":", 1)[1]
    try:
        await mem._archive_page(page_id)
        await call.message.edit_text("🗑 Запись удалена из памяти.")
    except Exception as e:
        logger.error("cb_arcmem_del: %s", e)
        await call.message.edit_text(f"⚠️ Ошибка удаления: {e}")


@router.callback_query(F.data == "arcmem_cancel")
async def cb_arcmem_cancel(call: CallbackQuery) -> None:
    await call.answer()
    await call.message.edit_text("❌ Отмена.")


@router.callback_query(F.data.startswith("arcmem_auto_yes:"))
async def cb_arcmem_auto_yes(call: CallbackQuery) -> None:
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


@router.callback_query(F.data.startswith("arcmem_auto_no:"))
async def cb_arcmem_auto_no(call: CallbackQuery) -> None:
    await call.answer()
    uid = int(call.data.split(":", 1)[1])
    _pending_auto.pop(uid, None)
    await call.message.edit_text("✗ Не сохраняю.")
