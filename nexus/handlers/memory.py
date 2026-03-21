"""nexus/handlers/memory.py — управление долгосрочной памятью бота."""
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

logger = logging.getLogger("nexus.memory")
router = Router()

BOT_LABEL = "☀️ Nexus"
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
    """Ищет страницы памяти по тексту (Текст или Ключ содержит query)."""
    db_id = _db_id()
    if not db_id or not query.strip():
        return []
    filter_obj = {
        "or": [
            {"property": "Текст", "title":      {"contains": query}},
            {"property": "Ключ",  "rich_text":  {"contains": query}},
        ]
    }
    try:
        return await db_query(db_id, filter_obj=filter_obj, page_size=page_size)
    except Exception as e:
        logger.error("memory _find_pages: %s", e)
        return []


def _page_title(page: dict) -> str:
    parts = page.get("properties", {}).get("Текст", {}).get("title", [])
    return parts[0]["plain_text"] if parts else "—"


def _page_key(page: dict) -> str:
    parts = page.get("properties", {}).get("Ключ", {}).get("rich_text", [])
    return parts[0]["plain_text"] if parts else "—"


# ── Системный промпт для парсинга факта ────────────────────────────────────────

_PARSE_SYSTEM = (
    "Ты парсишь факт для сохранения в память. "
    "Отвечай ТОЛЬКО валидным JSON без пояснений:\n"
    '{"key": "короткий ключ (имя или тема, snake_case)", '
    '"fact": "краткий факт одной строкой", '
    '"category": "Люди|Животные|Предпочтения|Здоровье|Места|Факты|Другое"}\n'
    "Примеры:\n"
    "  'запомни что маша не ест мясо' → {\"key\":\"маша\",\"fact\":\"не ест мясо\",\"category\":\"Люди\"}\n"
    "  'батон весит 4 кг' → {\"key\":\"батон\",\"fact\":\"весит 4 кг\",\"category\":\"Факты\"}\n"
    "  'у меня аллергия на пыль' → {\"key\":\"аллергия\",\"fact\":\"аллергия на пыль\",\"category\":\"Здоровье\"}\n"
    "  'кот боится пылесоса' → {\"key\":\"кот\",\"fact\":\"боится пылесоса\",\"category\":\"Животные\"}"
)


async def _parse_key_fact(text: str) -> tuple[str, str, str]:
    """Возвращает (key, fact, category). При ошибке — fallback из слов текста."""
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
        logger.error("memory _parse_key_fact: %s", e)

    # Fallback: key из слов 1-3 (без "запомни"), fact = весь текст
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
    """Сохранить факт в память. data = {"text": "..."}"""
    from core.layout import maybe_convert
    text = maybe_convert(data.get("text", message.text or "").strip())
    logger.info("handle_memory_save: text=%r", text[:60])

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
        logger.error("memory_save: exception: %s", e)
        await message.answer(f"⚠️ Ошибка записи: {e}")


async def handle_memory_search(
    message: Message,
    data: dict,
    user_notion_id: str = "",
) -> None:
    """Поиск в памяти. data = {"query": "..."}"""
    query = (data.get("query") or data.get("text") or "").strip()
    logger.info("handle_memory_search: query=%r", query)

    db_id = _db_id()
    if not db_id:
        await message.answer("⚠️ NOTION_DB_MEMORY не задан")
        return

    pages = await _find_pages(query, page_size=10) if query else []
    if not query:
        # Показать последние 10 активных
        try:
            filter_obj = {"property": "Актуально", "checkbox": {"equals": True}}
            pages = await db_query(
                db_id,
                filter_obj=filter_obj,
                sorts=[{"timestamp": "created_time", "direction": "descending"}],
                page_size=10,
            )
        except Exception as e:
            logger.error("handle_memory_search: %s", e)
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
            callback_data=f"mem_del:{pid}",
        )])

    header = f"🧠 <b>Память</b> (найдено {len(pages)}):\n\n"
    await message.answer(
        header + "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


async def handle_memory_deactivate(
    message: Message,
    data: dict,
    user_notion_id: str = "",
) -> None:
    """Пометить запись памяти как неактуальную (Актуально=False)."""
    hint = (data.get("hint") or data.get("text") or "").strip()
    logger.info("handle_memory_deactivate: hint=%r", hint)

    pages = await _find_pages(hint)
    if not pages:
        await message.answer(f"🧠 Не нашла запись по «{hint}»")
        return

    page = pages[0]
    try:
        await update_page(page["id"], {"Актуально": {"checkbox": False}})
        fact = _page_title(page)
        await message.answer(f"🧠 Помечено как неактуальное: <b>{fact}</b>")
    except Exception as e:
        logger.error("handle_memory_deactivate: %s", e)
        await message.answer("⚠️ Ошибка обновления")


async def handle_memory_delete(
    message: Message,
    data: dict,
    user_notion_id: str = "",
) -> None:
    """Удалить (архивировать) запись памяти."""
    hint = (data.get("hint") or data.get("text") or "").strip()
    logger.info("handle_memory_delete: hint=%r", hint)

    pages = await _find_pages(hint)
    if not pages:
        await message.answer(f"🧠 Не нашла запись по «{hint}»")
        return

    if len(pages) == 1:
        page = pages[0]
        await _archive_page(page["id"])
        fact = _page_title(page)
        await message.answer(f"🗑 Удалено из памяти: <b>{fact}</b>")
    else:
        # Несколько — показать кнопки выбора
        buttons = []
        for page in pages[:5]:
            fact = _page_title(page)
            key  = _page_key(page)
            buttons.append([InlineKeyboardButton(
                text=f"🗑 {key}: {fact[:40]}",
                callback_data=f"mem_del:{page['id']}",
            )])
        buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="mem_cancel")])
        await message.answer(
            "🧠 Нашла несколько записей. Какую удалить?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )


async def handle_memory_auto_suggest(
    message: Message,
    text: str,
    user_notion_id: str = "",
) -> None:
    """Предложить сохранить факт в память (inline-кнопки да/нет)."""
    uid = message.from_user.id
    _pending_auto[uid] = {"text": text, "user_notion_id": user_notion_id}
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🧠 Да, запомнить", callback_data=f"mem_auto_yes:{uid}"),
        InlineKeyboardButton(text="✗ Нет",            callback_data=f"mem_auto_no:{uid}"),
    ]])
    await message.answer(
        f"💡 Сохранить в память?\n<i>{text[:100]}</i>",
        reply_markup=kb,
    )


# ── Archive helper ──────────────────────────────────────────────────────────────

async def _archive_page(page_id: str) -> None:
    notion = get_notion()
    await notion._client.pages.update(page_id=page_id, archived=True)
    logger.info("memory: archived page %s", page_id[:8])


# ── Callbacks ───────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("mem_del:"))
async def cb_mem_del(call: CallbackQuery) -> None:
    """Удалить запись памяти по кнопке."""
    await call.answer()
    page_id = call.data.split(":", 1)[1]
    try:
        await _archive_page(page_id)
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
    """Подтвердить авто-сохранение в память."""
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


@router.callback_query(F.data.startswith("mem_auto_no:"))
async def cb_mem_auto_no(call: CallbackQuery) -> None:
    """Отказ от авто-сохранения."""
    await call.answer()
    uid = int(call.data.split(":", 1)[1])
    _pending_auto.pop(uid, None)
    await call.message.edit_text("✗ Не сохраняю.")
