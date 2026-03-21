"""arcana/handlers/memory.py — управление долгосрочной памятью Арканы."""
from __future__ import annotations

import json
import logging
import os
from typing import Dict, Optional

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from core.claude_client import ask_claude
from core.notion_client import (
    db_query, page_create, update_page, get_notion,
    _title, _text, _select, _relation,
)
from core.layout import maybe_convert

logger = logging.getLogger("arcana.memory")
router = Router()

BOT_LABEL = "🌒 Arcana"
DB_ID_ENV  = "NOTION_DB_MEMORY"

# Pending auto-suggest: uid → {"text": ..., "user_notion_id": ...}
_pending_auto: Dict[int, dict] = {}

# ── Notion helpers ──────────────────────────────────────────────────────────────

def _db_id() -> Optional[str]:
    return os.environ.get(DB_ID_ENV)


def _memory_props(key: str, fact: str, category: str = "", user_notion_id: str = "") -> dict:
    props: dict = {
        "Текст":     _title(fact),
        "Ключ":      _text(key),
        "Бот":       _text(BOT_LABEL),
        "Актуально": {"checkbox": True},
    }
    if category:
        props["Категория"] = _select(category)
    if user_notion_id:
        props["Пользователь"] = _relation(user_notion_id)
    return props


async def _find_pages(query: str, page_size: int = 5) -> list:
    db_id = _db_id()
    if not db_id or not query.strip():
        return []
    filter_obj = {
        "or": [
            {"property": "Текст", "title":     {"contains": query}},
            {"property": "Ключ",  "rich_text": {"contains": query}},
        ]
    }
    try:
        return await db_query(db_id, filter_obj=filter_obj, page_size=page_size)
    except Exception as e:
        logger.error("arcana memory _find_pages: %s", e)
        return []


def _page_title(page: dict) -> str:
    parts = page.get("properties", {}).get("Текст", {}).get("title", [])
    return parts[0]["plain_text"] if parts else "—"


def _page_key(page: dict) -> str:
    parts = page.get("properties", {}).get("Ключ", {}).get("rich_text", [])
    return parts[0]["plain_text"] if parts else "—"


# ── Системный промпт ────────────────────────────────────────────────────────────

_PARSE_SYSTEM = (
    "Ты парсишь факт для сохранения в память. "
    "Отвечай ТОЛЬКО валидным JSON без пояснений:\n"
    '{"key": "короткий ключ (имя или тема, snake_case)", '
    '"fact": "краткий факт одной строкой", '
    '"category": "Клиенты|Ритуалы|Практики|Люди|Предпочтения|Факты|Другое"}\n'
    "Примеры:\n"
    "  'аня не любит свечи' → {\"key\":\"аня\",\"fact\":\"не любит свечи\",\"category\":\"Клиенты\"}\n"
    "  'ритуал новолуния раз в месяц' → {\"key\":\"ритуал_новолуния\",\"fact\":\"раз в месяц\",\"category\":\"Ритуалы\"}\n"
    "  'запомни что у маши аллергия на ладан' → {\"key\":\"маша\",\"fact\":\"аллергия на ладан\",\"category\":\"Клиенты\"}"
)


async def _parse_key_fact(text: str) -> tuple[str, str, str]:
    try:
        raw = await ask_claude(
            text,
            system=_PARSE_SYSTEM,
            max_tokens=150,
            model="claude-haiku-4-5-20251001",
        )
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        parsed = json.loads(raw)
        key      = (parsed.get("key")      or "").strip()
        fact     = (parsed.get("fact")     or "").strip()
        category = (parsed.get("category") or "").strip()
        if key and fact:
            return key, fact, category
    except Exception as e:
        logger.error("arcana memory _parse_key_fact: %s", e)

    words = text.lower().split()
    skip = {"запомни", "что", "как"}
    key = "_".join(w for w in words[:4] if w.isalpha() and w not in skip)[:30] or "факт"
    return key, text, "Другое"


# ── Handlers ───────────────────────────────────────────────────────────────────

async def handle_memory_save(
    message: Message,
    data: dict,
    user_notion_id: str = "",
) -> None:
    text = maybe_convert(data.get("text", message.text or "").strip())
    logger.info("arcana handle_memory_save: text=%r", text[:60])

    db_id = _db_id()
    if not db_id:
        await message.answer("⚠️ NOTION_DB_MEMORY не задан")
        return

    key, fact, category = await _parse_key_fact(text)
    props = _memory_props(key, fact, category, user_notion_id)

    logger.info("memory_save: writing to Notion db=%s props=%s", db_id, list(props.keys()))
    try:
        result = await page_create(db_id, props)
        if result:
            logger.info("memory_save: created page id=%s", result)
            await message.answer(f"🧠 Запомнила: <b>{key}</b> — {fact}")
        else:
            logger.error("memory_save: page_create returned None")
            await message.answer("⚠️ Ошибка записи в Notion (page_create вернул None)")
    except Exception as e:
        logger.error("arcana memory_save: exception: %s", e)
        await message.answer(f"⚠️ Ошибка записи: {e}")


async def handle_memory_search(
    message: Message,
    data: dict,
    user_notion_id: str = "",
) -> None:
    query = (data.get("query") or data.get("text") or "").strip()
    logger.info("arcana handle_memory_search: query=%r", query)

    db_id = _db_id()
    if not db_id:
        await message.answer("⚠️ NOTION_DB_MEMORY не задан")
        return

    pages = await _find_pages(query, page_size=10) if query else []
    if not query:
        try:
            filter_obj = {"property": "Актуально", "checkbox": {"equals": True}}
            pages = await db_query(
                db_id,
                filter_obj=filter_obj,
                sorts=[{"timestamp": "created_time", "direction": "descending"}],
                page_size=10,
            )
        except Exception as e:
            logger.error("arcana handle_memory_search: %s", e)
            pages = []

    if not pages:
        await message.answer("🧠 Ничего не нашла в памяти" + (f" по «{query}»" if query else ""))
        return

    lines = []
    buttons = []
    for page in pages:
        pid  = page["id"]
        fact = _page_title(page)
        key  = _page_key(page)
        lines.append(f"• <b>{key}</b> — {fact}")
        buttons.append([InlineKeyboardButton(
            text=f"🗑 {key}: {fact[:30]}",
            callback_data=f"arcmem_del:{pid}",
        )])

    header = f"🧠 <b>Память Арканы</b> (найдено {len(pages)}):\n\n"
    await message.answer(
        header + "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


async def handle_memory_deactivate(
    message: Message,
    data: dict,
    user_notion_id: str = "",
) -> None:
    hint = (data.get("hint") or data.get("text") or "").strip()
    logger.info("arcana handle_memory_deactivate: hint=%r", hint)

    pages = await _find_pages(hint)
    if not pages:
        await message.answer(f"🧠 Не нашла запись по «{hint}»")
        return

    try:
        await update_page(pages[0]["id"], {"Актуально": {"checkbox": False}})
        await message.answer(f"🧠 Помечено как неактуальное: <b>{_page_title(pages[0])}</b>")
    except Exception as e:
        logger.error("arcana handle_memory_deactivate: %s", e)
        await message.answer("⚠️ Ошибка обновления")


async def handle_memory_delete(
    message: Message,
    data: dict,
    user_notion_id: str = "",
) -> None:
    hint = (data.get("hint") or data.get("text") or "").strip()
    logger.info("arcana handle_memory_delete: hint=%r", hint)

    pages = await _find_pages(hint)
    if not pages:
        await message.answer(f"🧠 Не нашла запись по «{hint}»")
        return

    if len(pages) == 1:
        await _archive_page(pages[0]["id"])
        await message.answer(f"🗑 Удалено из памяти: <b>{_page_title(pages[0])}</b>")
    else:
        buttons = []
        for page in pages[:5]:
            fact = _page_title(page)
            key  = _page_key(page)
            buttons.append([InlineKeyboardButton(
                text=f"🗑 {key}: {fact[:40]}",
                callback_data=f"arcmem_del:{page['id']}",
            )])
        buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="arcmem_cancel")])
        await message.answer(
            "🧠 Нашла несколько записей. Какую удалить?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )


async def handle_memory_auto_suggest(
    message: Message,
    text: str,
    user_notion_id: str = "",
) -> None:
    uid = message.from_user.id
    _pending_auto[uid] = {"text": text, "user_notion_id": user_notion_id}
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🧠 Да, запомнить", callback_data=f"arcmem_auto_yes:{uid}"),
        InlineKeyboardButton(text="✗ Нет",            callback_data=f"arcmem_auto_no:{uid}"),
    ]])
    await message.answer(
        f"💡 Сохранить в память?\n<i>{text[:100]}</i>",
        reply_markup=kb,
    )


# ── Archive helper ──────────────────────────────────────────────────────────────

async def _archive_page(page_id: str) -> None:
    notion = get_notion()
    await notion._client.pages.update(page_id=page_id, archived=True)
    logger.info("arcana memory: archived page %s", page_id[:8])


# ── Callbacks ───────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("arcmem_del:"))
async def cb_arcmem_del(call: CallbackQuery) -> None:
    await call.answer()
    page_id = call.data.split(":", 1)[1]
    try:
        await _archive_page(page_id)
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
    text = pending["text"]
    user_notion_id = pending.get("user_notion_id", "")

    key, fact, category = await _parse_key_fact(text)
    db_id = _db_id()
    if not db_id:
        await call.message.edit_text("⚠️ NOTION_DB_MEMORY не задан")
        return
    props = _memory_props(key, fact, category, user_notion_id)
    result = await page_create(db_id, props)
    if result:
        await call.message.edit_text(f"🧠 Запомнила: <b>{key}</b> — {fact}")
    else:
        await call.message.edit_text("⚠️ Ошибка записи в Notion")


@router.callback_query(F.data.startswith("arcmem_auto_no:"))
async def cb_arcmem_auto_no(call: CallbackQuery) -> None:
    await call.answer()
    uid = int(call.data.split(":", 1)[1])
    _pending_auto.pop(uid, None)
    await call.message.edit_text("✗ Не сохраняю.")
