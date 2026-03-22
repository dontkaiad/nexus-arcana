"""core/memory.py — общая логика долгосрочной памяти (Nexus + Arcana)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Dict, List, Optional, Set, Tuple

from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from core.claude_client import ask_claude
from core.layout import maybe_convert
from core.notion_client import (
    db_query, page_create, update_page, get_notion,
    _title, _text, _select, _relation,
)

logger = logging.getLogger("core.memory")

DB_ID_ENV = "NOTION_DB_MEMORY"

# Последние результаты поиска по памяти: uid → list of pages
# Используется для "неактуально" / "удали все" без повторного поиска
_last_memory_results: Dict[int, List[dict]] = {}

# Мульти-выбор удаления: страницы показанные в UI и выбранные юзером
_mem_delete_pages: Dict[int, List[dict]] = {}
_mem_selected: Dict[int, Set[str]] = {}  # uid → set of page_id

# Точные значения категорий из Notion (Select)
CATEGORIES: List[str] = [
    "🧠 СДВГ", "👥 Люди", "🏥 Здоровье", "🛒 Предпочтения",
    "💼 Работа", "🏠 Быт", "🔄 Паттерн", "💡 Инсайт", "🔮 Практика", "🐾 Коты",
    "💰 Лимит",
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
    '  "кот боится пылесоса" → {"fact":"боится пылесоса","category":"🐾 Коты","связь":"кот","ключ":"кот_страх"}\n'
    '  "у меня дислексия" → {"fact":"дислексия","category":"🧠 СДВГ","связь":"","ключ":"дислексия"}\n'
    '  "royal canin indoor 2кг" → {"fact":"royal canin indoor 2кг","category":"🐾 Коты","связь":"коты","ключ":"royal_canin"}\n'
    '  "батон весит 4 кг" → {"fact":"батон весит 4 кг","category":"🐾 Коты","связь":"батон","ключ":"батон"}\n'
    '  "алуна не ест курицу" → {"fact":"алуна не ест курицу","category":"🐾 Коты","связь":"алуна","ключ":"алуна_еда"}\n'
    '  "лимит на сигареты 3000р в месяц" → {"fact":"лимит: 🚬 Привычки — 3000₽/мес","category":"💰 Лимит","связь":"привычки","ключ":"лимит_привычки"}\n'
    '  "поставь лимит на кафе 5000р" → {"fact":"лимит: 🍱 Кафе/Доставка — 5000₽/мес","category":"💰 Лимит","связь":"кафе","ключ":"лимит_кафе"}\n'
    '  "лимит на продукты 8000р" → {"fact":"лимит: 🍜 Продукты — 8000₽/мес","category":"💰 Лимит","связь":"продукты","ключ":"лимит_продукты"}'
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


def _page_category(page: dict) -> str:
    sel = page.get("properties", {}).get("Категория", {}).get("select")
    return sel["name"] if sel else ""


def _page_date(page: dict) -> str:
    return (page.get("created_time") or "")[:10]


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

# Стоп-слова, которые не несут смысла при поиске
_SEARCH_STOP = {"про", "о", "об", "и", "не", "это", "что", "как", "из", "по",
                "для", "на", "в", "с", "к", "у", "за", "от"}


def _normalize_word(word: str) -> str:
    """
    Нормализация слова: убрать падежные окончания для поиска contains.
    Минимальная основа — 3 символа. Порядок суффиксов: длинные первыми.
    машу → маш, батона → батон, алуны → алун, кота → кот, маше → маш
    """
    # Порядок важен: длинные суффиксы первыми
    for suffix in ("ами", "ями", "ого", "его", "ому", "ему", "ой", "ей",
                   "ом", "ем", "ах", "ях", "ам", "ям", "ую", "юю",
                   "ов", "ев", "ёв", "ий", "ый", "ая", "яя",
                   "у", "ю", "а", "я", "е", "и", "ы", "о"):
        if word.endswith(suffix):
            stem = word[:-len(suffix)]
            if len(stem) >= 3:
                return stem
    return word


def _tokenize_hint(hint: str) -> List[str]:
    """Разбить hint на нормализованные токены, отфильтровав стоп-слова."""
    tokens = []
    for w in hint.lower().split():
        # убрать знаки препинания
        w = w.strip(".,!?;:«»\"'")
        if len(w) >= 2 and w not in _SEARCH_STOP:
            tokens.append(_normalize_word(w))
    return tokens


async def _find_pages(query: str, page_size: int = 5) -> List[dict]:
    """Точный поиск: query как одна строка в Текст/Ключ/Связь."""
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


async def _find_pages_by_hint(hint: str, page_size: int = 10) -> List[dict]:
    """
    Умный поиск по hint из нескольких слов.
    1. Токенизирует и нормализует падежи.
    2. Ищет первый токен (имя/объект) в поле Связь + Актуально=True.
    3. Если есть доп. токены — постфильтр: оставить страницы, где Текст
       содержит хотя бы один из них.
    4. Fallback: OR по всем токенам в Текст/Ключ/Связь.
    """
    db_id = _get_db_id()
    if not db_id or not hint.strip():
        return []

    tokens = _tokenize_hint(hint)
    logger.info("memory _find_pages_by_hint: hint=%r tokens=%s", hint, tokens)

    if not tokens:
        return await _find_pages(hint, page_size)

    first = tokens[0]
    rest  = tokens[1:]

    def _search_terms(tok: str) -> List[str]:
        """Оригинал + нормализованная основа (если отличается)."""
        stem = _normalize_word(tok)
        return [tok, stem] if stem != tok else [tok]

    def _or_clauses(terms: List[str]) -> List[dict]:
        clauses: List[dict] = []
        for t in terms:
            clauses += [
                {"property": "Связь", "rich_text": {"contains": t}},
                {"property": "Текст", "title":     {"contains": t}},
            ]
        return clauses

    try:
        # Шаг 1: первый токен (оригинал + основа) → Связь ИЛИ Текст
        pages = await db_query(db_id, filter_obj={
            "or": _or_clauses(_search_terms(first))
        }, page_size=page_size)

        # Шаг 2: постфильтр по остальным токенам
        if pages and rest:
            refined = [
                p for p in pages
                if any(tok in _page_fact(p).lower() for tok in rest)
            ]
            if refined:
                logger.info("memory _find_pages_by_hint: refined %d→%d pages", len(pages), len(refined))
                return refined

        if pages:
            return pages

        # Шаг 3: fallback — OR по всем токенам (оригинал + основа) во всех полях
        or_filters: List[dict] = []
        for tok in tokens:
            for t in _search_terms(tok):
                or_filters += [
                    {"property": "Текст", "title":     {"contains": t}},
                    {"property": "Ключ",  "rich_text": {"contains": t}},
                    {"property": "Связь", "rich_text": {"contains": t}},
                ]
        pages = await db_query(db_id, filter_obj={"or": or_filters}, page_size=page_size)
        return pages

    except Exception as e:
        logger.error("memory _find_pages_by_hint: %s", e)
        return []


async def _archive_page(page_id: str) -> None:
    notion = get_notion()
    await notion.pages.update(page_id=page_id, archived=True)
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
        # Лимиты: обновить существующую запись с тем же ключом если есть
        if category == "💰 Лимит" and ключ:
            existing = await db_query(db_id, filter_obj={"and": [
                {"property": "Ключ", "rich_text": {"contains": ключ}},
                {"property": "Категория", "select": {"equals": "💰 Лимит"}},
            ]}, page_size=1)
            if existing:
                await update_page(existing[0]["id"], props)
                logger.info("memory save: updated limit page id=%s", existing[0]["id"])
                await message.answer(f"🧠 Обновила лимит: {fact}")
                return

        result = await page_create(db_id, props)
        if result:
            logger.info("memory save: created page id=%s", result)
            cat_label = f" [{category}]" if category else ""
            await message.answer(f"🧠 Запомнила{cat_label}: {fact}")
        else:
            logger.error("memory save: Notion error page_create returned None")
            await message.answer("⚠️ Ошибка записи в Notion")
    except Exception as e:
        logger.error("memory save: Notion error %s", e)
        await message.answer(f"⚠️ Ошибка записи: {e}")


async def _search_finance(query: str, page_size: int = 5) -> List[dict]:
    """Поиск по базе финансов: поле Описание contains query."""
    db_id = os.environ.get("NOTION_DB_FINANCE")
    if not db_id or not query:
        return []
    try:
        return await db_query(db_id, filter_obj={
            "property": "Описание", "title": {"contains": query}
        }, page_size=page_size)
    except Exception as e:
        logger.error("memory search_finance: %s", e)
        return []


async def _search_tasks(query: str, page_size: int = 5) -> List[dict]:
    """Поиск по базе задач: Задача contains query, статус != Done."""
    db_id = os.environ.get("NOTION_DB_TASKS")
    if not db_id or not query:
        return []
    try:
        return await db_query(db_id, filter_obj={"and": [
            {"property": "Задача", "title": {"contains": query}},
            {"property": "Статус", "status": {"does_not_equal": "Done"}},
        ]}, page_size=page_size)
    except Exception as e:
        logger.error("memory search_tasks: %s", e)
        return []


async def search_memory(
    message: Message,
    query: str,
    user_notion_id: str,
    del_prefix: str = "mem_del",
) -> None:
    """Поиск по памяти + финансам + задачам параллельно."""
    db_id = _get_db_id()
    if not db_id:
        await message.answer("⚠️ NOTION_DB_MEMORY не задан")
        return

    query = query.strip()

    if query:
        mem_coro = _find_pages_by_hint(query, page_size=10)
        fin_coro = _search_finance(query, page_size=5)
        task_coro = _search_tasks(query, page_size=5)
        pages, fin_pages, task_pages = await asyncio.gather(mem_coro, fin_coro, task_coro)
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
            pages = []
        fin_pages, task_pages = [], []

    if not pages and not fin_pages and not task_pages:
        suffix = f" по «{query}»" if query else ""
        await message.answer(f"🧠 Ничего не нашла в памяти{suffix}")
        return

    uid = message.from_user.id
    _last_memory_results[uid] = pages
    _mem_delete_pages[uid] = pages
    _mem_selected[uid] = set()

    parts: List[str] = []

    # ── Память ──
    if pages:
        lines = []
        for page in pages:
            fact      = _page_fact(page)
            category  = _page_category(page)
            date      = _page_date(page)
            cat_emoji = category.split(" ")[0] if category else "💡"
            is_inactive = page["properties"].get("Актуально", {}).get("checkbox") is False
            inactive_mark = " <i>(неактуально)</i>" if is_inactive else ""
            line2 = f"<i>{category} · {date}</i>" if category else f"<i>{date}</i>"
            lines.append(f"{cat_emoji} {fact}{inactive_mark}\n{line2}")
        parts.append(f"🧠 <b>Память</b> (найдено {len(pages)}):\n\n" + "\n\n".join(lines))

    # ── Финансы ──
    if fin_pages:
        fin_lines = []
        for p in fin_pages:
            props = p.get("properties", {})
            desc_parts = props.get("Описание", {}).get("title", [])
            desc = desc_parts[0]["plain_text"] if desc_parts else "—"
            amount = props.get("Сумма", {}).get("number") or ""
            date = (props.get("Дата", {}).get("date") or {}).get("start", "")[:10]
            amount_str = f"{amount:g}₽" if amount else ""
            fin_lines.append(f"· {desc} {amount_str} · {date}".strip())
        parts.append("💰 <b>Финансы:</b>\n" + "\n".join(fin_lines))

    # ── Задачи ──
    if task_pages:
        task_lines = []
        for p in task_pages:
            props = p.get("properties", {})
            title_parts = props.get("Задача", {}).get("title", [])
            title = title_parts[0]["plain_text"] if title_parts else "—"
            deadline = (props.get("Дедлайн", {}).get("date") or {}).get("start", "")[:10]
            deadline_str = f" · до {deadline}" if deadline else ""
            task_lines.append(f"· {title}{deadline_str}")
        parts.append("✅ <b>Задачи:</b>\n" + "\n".join(task_lines))

    text = "\n\n".join(parts)
    kb = _build_delete_keyboard(uid, pages, reactivate_cb="mem_reactivate_selected") if pages else None
    await message.answer(text, reply_markup=kb)


async def deactivate_memory(
    message: Message,
    hint: str,
    user_notion_id: str,
) -> None:
    """Пометить запись памяти как неактуальную (Актуально = False).

    hint == ""    → деактивировать все из последних результатов поиска
    hint == "все" → то же
    hint == "2"   → деактивировать вторую запись из последних результатов
    иначе         → поиск по hint
    """
    uid = message.from_user.id
    last = _last_memory_results.get(uid, [])

    # Определяем с какими страницами работать
    if not hint or hint.lower() == "все":
        if not last:
            await message.answer("🧠 Сначала найди записи — например: «напомни про машу»")
            return
        pages = last
    elif hint.isdigit():
        if not last:
            await message.answer("🧠 Сначала найди записи — например: «напомни про машу»")
            return
        idx = int(hint) - 1
        if not (0 <= idx < len(last)):
            await message.answer(f"🧠 Записи №{hint} нет в результатах поиска (всего {len(last)})")
            return
        pages = [last[idx]]
    else:
        pages = await _find_pages_by_hint(hint) if hint else []
        if not pages:
            tokens = _tokenize_hint(hint)
            subject = tokens[0] if tokens else hint
            await message.answer(f"🧠 Не нашла записей о <b>{subject}</b>")
            return

    try:
        for page in pages:
            await update_page(page["id"], {"Актуально": {"checkbox": False}})
        facts = ", ".join(f"<b>{_page_fact(p)}</b>" for p in pages)
        await message.answer(f"🧠 Помечено как неактуальное: {facts}")
    except Exception as e:
        logger.error("memory deactivate: %s", e)
        await message.answer("⚠️ Ошибка обновления")


def _build_delete_keyboard(
    uid: int,
    pages: List[dict],
    toggle_prefix: str = "mem_toggle",
    selected_cb: str = "mem_deactivate_selected",
    selected_label: str = "☑️ Отметить неактуальными",
    all_cb: str = "mem_deactivate_all",
    all_label: str = "☑️ Отметить все неактуальными",
    cancel_label: str = "❌ Закрыть",
    reactivate_cb: str = "",
    reactivate_label: str = "↩️ Восстановить выбранные",
) -> InlineKeyboardMarkup:
    """Клавиатура чекбоксов для записей памяти.
    Чекбокс = выбор. Действие применяется кнопкой.

    Режим поиска:  toggle_prefix="mem_toggle",     selected_cb="mem_deactivate_selected"
    Режим удаления: toggle_prefix="mem_del_toggle", selected_cb="mem_delete_selected"
    """
    selected = _mem_selected.get(uid, set())
    n_selected = len(selected)
    buttons = []
    for page in pages:
        pid = page["id"]
        fact = _page_fact(page)
        is_inactive = page["properties"].get("Актуально", {}).get("checkbox") is False
        icon = "✅" if pid in selected else "☐"
        label = f"{icon} {fact[:40]}" + (" ·· неакт." if is_inactive else "")
        buttons.append([InlineKeyboardButton(
            text=label,
            callback_data=f"{toggle_prefix}:{pid}",
        )])
    n_inactive = sum(1 for p in pages if p["properties"].get("Актуально", {}).get("checkbox") is False)
    n_active = len(pages) - n_inactive

    if n_selected:
        # Кнопка деактивации выбранных — только если среди выбранных есть активные
        selected_active = any(
            p["properties"].get("Актуально", {}).get("checkbox") is not False
            for p in pages if p["id"] in selected
        )
        if selected_active:
            buttons.append([InlineKeyboardButton(
                text=f"{selected_label} ({n_selected})",
                callback_data=f"{selected_cb}:{uid}",
            )])
        if reactivate_cb:
            buttons.append([InlineKeyboardButton(
                text=f"{reactivate_label} ({n_selected})",
                callback_data=f"{reactivate_cb}:{uid}",
            )])
    if n_active:
        buttons.append([InlineKeyboardButton(
            text=f"{all_label} ({n_active})",
            callback_data=f"{all_cb}:{uid}",
        )])
    if reactivate_cb and n_inactive:
        buttons.append([InlineKeyboardButton(
            text=f"↩️ Восстановить все ({n_inactive})",
            callback_data=f"mem_reactivate_all:{uid}",
        )])
    buttons.append([InlineKeyboardButton(
        text=cancel_label,
        callback_data=f"mem_cancel:{uid}",
    )])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def delete_memory(
    message: Message,
    hint: str,
    user_notion_id: str,
    del_prefix: str = "mem_del",
    cancel_cb: str = "mem_cancel",
) -> None:
    """Удалить (архивировать) запись памяти.

    hint == ""    → удалить все из последних результатов (с подтверждением кнопками)
    hint == "все" → то же
    hint == "2"   → удалить вторую запись из последних результатов
    иначе         → поиск по hint
    """
    uid = message.from_user.id
    last = _last_memory_results.get(uid, [])

    if not hint or hint.lower() == "все":
        if not last:
            await message.answer("🧠 Сначала найди записи — например: «напомни про машу»")
            return
        pages = last
    elif hint.isdigit():
        if not last:
            await message.answer("🧠 Сначала найди записи — например: «напомни про машу»")
            return
        idx = int(hint) - 1
        if not (0 <= idx < len(last)):
            await message.answer(f"🧠 Записи №{hint} нет в результатах поиска (всего {len(last)})")
            return
        pages = [last[idx]]
    else:
        pages = await _find_pages_by_hint(hint) if hint else []
        if not pages:
            tokens = _tokenize_hint(hint)
            subject = tokens[0] if tokens else hint
            await message.answer(f"🧠 Не нашла записей о <b>{subject}</b>")
            return

    if len(pages) == 1:
        await _archive_page(pages[0]["id"])
        _last_memory_results[uid] = [p for p in last if p["id"] != pages[0]["id"]]
        await message.answer(f"🗑 Удалено из памяти: <b>{_page_fact(pages[0])}</b>")
        return

    # Мульти-выбор для нескольких записей
    shown = pages[:10]
    _mem_delete_pages[uid] = shown
    _mem_selected[uid] = set()
    await message.answer(
        "🧠 Выбери записи для удаления:",
        reply_markup=_build_delete_keyboard(
            uid, shown,
            toggle_prefix="mem_del_toggle",
            selected_cb="mem_delete_selected",
            selected_label="🗑️ Удалить выбранные",
            all_cb="mem_delete_all",
            all_label="🗑️ Удалить все",
            cancel_label="❌ Отмена",
        ),
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
