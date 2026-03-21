"""core/memory.py — общая логика долгосрочной памяти (Nexus + Arcana)."""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Dict, List, Optional, Tuple

from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from core.claude_client import ask_claude
from core.layout import maybe_convert
from core.notion_client import (
    db_query, page_create, update_page, get_notion,
    _title, _text, _select, _relation,
)

logger = logging.getLogger("core.memory")

DB_ID_ENV = "NOTION_DB_MEMORY"

# Точные значения категорий из Notion (Select)
CATEGORIES: List[str] = [
    "🧠 СДВГ", "👥 Люди", "🏥 Здоровье", "🛒 Предпочтения",
    "💼 Работа", "🏠 Быт", "🔄 Паттерн", "💡 Инсайт", "🔮 Практика",
]
_CATEGORIES_STR = " / ".join(CATEGORIES)

# ── Системный промпт для Haiku ─────────────────────────────────────────────────

_PARSE_SYSTEM = (
    "Ты парсишь факт для сохранения в долгосрочную память.\n"
    "Отвечай ТОЛЬКО валидным JSON без пояснений, без markdown:\n"
    '{"fact": "краткий факт одной строкой",\n'
    ' "category": "одна из категорий ниже",\n'
    ' "связь": "имя человека/кота/объекта или пустая строка",\n'
    ' "ключ": "snake_case_тег"}\n'
    "\n"
    f"Допустимые категории: {_CATEGORIES_STR}\n"
    "\n"
    "Примеры:\n"
    '  "запомни что маша не ест мясо" → {"fact":"маша не ест мясо","category":"👥 Люди","связь":"маша","ключ":"маша_диета"}\n'
    '  "у меня аллергия на пыль" → {"fact":"аллергия на пыль","category":"🏥 Здоровье","связь":"","ключ":"аллергия"}\n'
    '  "батон весит 4 кг" → {"fact":"батон весит 4 кг","category":"🏠 Быт","связь":"батон","ключ":"батон"}\n'
    '  "я не ем сахар" → {"fact":"не ем сахар","category":"🛒 Предпочтения","связь":"","ключ":"диета_сахар"}\n'
    '  "маша это моя подруга" → {"fact":"маша — подруга","category":"👥 Люди","связь":"маша","ключ":"маша"}\n'
    '  "кот боится пылесоса" → {"fact":"боится пылесоса","category":"🏠 Быт","связь":"кот","ключ":"кот_страх"}\n'
    '  "у меня дислексия" → {"fact":"дислексия","category":"🧠 СДВГ","связь":"","ключ":"дислексия"}'
)

_STRIP_RE = re.compile(r"^\s*запомни\s+(что\s+)?", re.IGNORECASE)


# ── Notion helpers ──────────────────────────────────────────────────────────────

def _get_db_id() -> Optional[str]:
    return os.environ.get(DB_ID_ENV)


def _build_props(
    fact: str,
    category: str,
    связь: str,
    ключ: str,
    bot_label: str,
    user_notion_id: str = "",
) -> dict:
    """Строит dict properties для page_create / update_page."""
    props: dict = {
        "Текст":     _title(fact),           # Title
        "Ключ":      _text(ключ),            # Text
        "Бот":       _select(bot_label),     # Select: "☀️ Nexus" / "🌒 Arcana"
        "Источник":  _select("📝 Вручную"),  # Select
        "Актуально": {"checkbox": True},
    }
    if category and category in CATEGORIES:
        props["Категория"] = _select(category)
    if связь:
        props["Связь"] = _text(связь)        # Text
    if user_notion_id:
        props["🪪 Пользователи"] = _relation(user_notion_id)
    return props


def _page_fact(page: dict) -> str:
    parts = page.get("properties", {}).get("Текст", {}).get("title", [])
    return parts[0]["plain_text"] if parts else "—"


def _page_key(page: dict) -> str:
    parts = page.get("properties", {}).get("Ключ", {}).get("rich_text", [])
    return parts[0]["plain_text"] if parts else "—"


# ── Парсинг факта через Haiku ──────────────────────────────────────────────────

async def _parse_fact(text: str) -> Tuple[str, str, str, str]:
    """
    Возвращает (fact, category, связь, ключ).
    Fallback если Claude вернул невалидный JSON или пустые поля.
    """
    try:
        raw = await ask_claude(
            text,
            system=_PARSE_SYSTEM,
            max_tokens=200,
            model="claude-haiku-4-5-20251001",
        )
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        parsed = json.loads(raw)
        fact     = (parsed.get("fact")     or "").strip()
        category = (parsed.get("category") or "").strip()
        связь    = (parsed.get("связь")    or "").strip()
        ключ     = (parsed.get("ключ")     or "").strip()
        if fact and ключ:
            if category not in CATEGORIES:
                category = "💡 Инсайт"
            return fact, category, связь, ключ
    except Exception as e:
        logger.error("memory _parse_fact error: %s", e)

    # Fallback: убираем "запомни что" из начала
    fact = _STRIP_RE.sub("", text).strip() or text
    return fact, "💡 Инсайт", "", "факт"


# ── Поиск страниц ──────────────────────────────────────────────────────────────

async def _find_pages(query: str, page_size: int = 5) -> List[dict]:
    db_id = _get_db_id()
    if not db_id or not query.strip():
        return []
    filter_obj = {
        "or": [
            {"property": "Текст", "title":     {"contains": query}},
            {"property": "Ключ",  "rich_text": {"contains": query}},
            {"property": "Связь", "rich_text": {"contains": query}},
        ]
    }
    try:
        return await db_query(db_id, filter_obj=filter_obj, page_size=page_size)
    except Exception as e:
        logger.error("memory _find_pages: %s", e)
        return []


async def _archive_page(page_id: str) -> None:
    notion = get_notion()
    await notion._client.pages.update(page_id=page_id, archived=True)
    logger.info("memory: archived page %s", page_id[:8])


# ── Public API ──────────────────────────────────────────────────────────────────

async def save_memory(
    message: Message,
    text: str,
    user_notion_id: str,
    bot_label: str,
) -> None:
    """Распарсить текст через Haiku и записать факт в Notion."""
    text = maybe_convert(text.strip())
    logger.info("memory save: text=%r bot=%s", text[:60], bot_label)

    db_id = _get_db_id()
    if not db_id:
        await message.answer("⚠️ NOTION_DB_MEMORY не задан")
        return

    fact, category, связь, ключ = await _parse_fact(text)
    props = _build_props(fact, category, связь, ключ, bot_label, user_notion_id)

    logger.info("memory save: writing to Notion %s (key=%s cat=%s)", fact, ключ, category)
    try:
        result = await page_create(db_id, props)
        if result:
            logger.info("memory save: created page id=%s", result)
            await message.answer(f"🧠 Запомнила: <b>{ключ}</b> — {fact}")
        else:
            logger.error("memory save: Notion error page_create returned None")
            await message.answer("⚠️ Ошибка записи в Notion")
    except Exception as e:
        logger.error("memory save: Notion error %s", e)
        await message.answer(f"⚠️ Ошибка записи: {e}")


async def search_memory(
    message: Message,
    query: str,
    user_notion_id: str,
    del_prefix: str = "mem_del",
) -> None:
    """Поиск по памяти. del_prefix — prefix для callback кнопки удаления."""
    db_id = _get_db_id()
    if not db_id:
        await message.answer("⚠️ NOTION_DB_MEMORY не задан")
        return

    query = query.strip()
    pages: List[dict] = []
    if query:
        pages = await _find_pages(query, page_size=10)
    else:
        try:
            filter_obj = {"property": "Актуально", "checkbox": {"equals": True}}
            pages = await db_query(
                db_id,
                filter_obj=filter_obj,
                sorts=[{"timestamp": "created_time", "direction": "descending"}],
                page_size=10,
            )
        except Exception as e:
            logger.error("memory search: %s", e)

    if not pages:
        suffix = f" по «{query}»" if query else ""
        await message.answer(f"🧠 Ничего не нашла в памяти{suffix}")
        return

    lines = []
    buttons = []
    for page in pages:
        pid  = page["id"]
        fact = _page_fact(page)
        key  = _page_key(page)
        lines.append(f"• <b>{key}</b> — {fact}")
        buttons.append([InlineKeyboardButton(
            text=f"🗑 {key}: {fact[:30]}",
            callback_data=f"{del_prefix}:{pid}",
        )])

    await message.answer(
        f"🧠 <b>Память</b> (найдено {len(pages)}):\n\n" + "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


async def deactivate_memory(
    message: Message,
    hint: str,
    user_notion_id: str,
) -> None:
    """Пометить запись памяти как неактуальную (Актуально = False)."""
    pages = await _find_pages(hint)
    if not pages:
        await message.answer(f"🧠 Не нашла запись по «{hint}»")
        return
    try:
        await update_page(pages[0]["id"], {"Актуально": {"checkbox": False}})
        await message.answer(f"🧠 Помечено как неактуальное: <b>{_page_fact(pages[0])}</b>")
    except Exception as e:
        logger.error("memory deactivate: %s", e)
        await message.answer("⚠️ Ошибка обновления")


async def delete_memory(
    message: Message,
    hint: str,
    user_notion_id: str,
    del_prefix: str = "mem_del",
    cancel_cb: str = "mem_cancel",
) -> None:
    """Удалить (архивировать) запись памяти. При нескольких совпадениях — кнопки выбора."""
    pages = await _find_pages(hint)
    if not pages:
        await message.answer(f"🧠 Не нашла запись по «{hint}»")
        return

    if len(pages) == 1:
        await _archive_page(pages[0]["id"])
        await message.answer(f"🗑 Удалено из памяти: <b>{_page_fact(pages[0])}</b>")
        return

    buttons = []
    for page in pages[:5]:
        fact = _page_fact(page)
        key  = _page_key(page)
        buttons.append([InlineKeyboardButton(
            text=f"🗑 {key}: {fact[:40]}",
            callback_data=f"{del_prefix}:{page['id']}",
        )])
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data=cancel_cb)])
    await message.answer(
        "🧠 Нашла несколько записей. Какую удалить?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


async def auto_suggest_memory(
    message: Message,
    text: str,
    user_notion_id: str,
    bot_label: str,
    pending_store: Dict[int, dict],
    yes_prefix: str = "mem_auto_yes",
    no_prefix: str  = "mem_auto_no",
) -> None:
    """Предложить сохранить факт в память (inline да/нет). pending_store — dict из хендлера."""
    uid = message.from_user.id
    pending_store[uid] = {"text": text, "user_notion_id": user_notion_id}
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🧠 Да, запомнить", callback_data=f"{yes_prefix}:{uid}"),
        InlineKeyboardButton(text="✗ Нет",            callback_data=f"{no_prefix}:{uid}"),
    ]])
    await message.answer(
        f"💡 Сохранить в память?\n<i>{text[:100]}</i>",
        reply_markup=kb,
    )
