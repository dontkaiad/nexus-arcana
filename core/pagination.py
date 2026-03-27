"""core/pagination.py — универсальный хелпер пагинации для Nexus и Arcana."""
from __future__ import annotations

from typing import Any, Callable, List, Optional

PAGE_SIZE = 10

# Хранилище: uid → {items, page, title, formatter}
_pages: dict = {}


def register_pages(uid: int, items: List[Any], title: str, formatter: Callable) -> None:
    """Сохранить список для пагинации.
    formatter — функция item → строка для отображения.
    """
    _pages[uid] = {"items": items, "page": 0, "title": title, "formatter": formatter}


def has_pages(uid: int) -> bool:
    """Есть ли зарегистрированная пагинация для этого пользователя."""
    return uid in _pages


def get_page_text(uid: int) -> str:
    """Получить текст текущей страницы."""
    state = _pages.get(uid)
    if not state:
        return "❌ Сессия устарела"
    items = state["items"]
    page = state["page"]
    total = len(items)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    chunk = items[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    lines = [state["formatter"](it) for it in chunk]
    header = f"<b>{state['title']}</b> · {total} шт · стр {page + 1}/{total_pages}"
    return header + "\n" + "\n".join(lines)


def get_page_keyboard(uid: int):
    """InlineKeyboardMarkup для навигации."""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    state = _pages.get(uid)
    if not state:
        return None
    page = state["page"]
    total = len(state["items"])
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    btns = []
    row = []
    if page > 0:
        row.append(InlineKeyboardButton(text="← Назад", callback_data=f"page:{uid}:prev"))
    if page < total_pages - 1:
        row.append(InlineKeyboardButton(text="Ещё →", callback_data=f"page:{uid}:next"))
    if row:
        btns.append(row)
    btns.append([InlineKeyboardButton(text="✕ Закрыть", callback_data=f"page:{uid}:close")])
    return InlineKeyboardMarkup(inline_keyboard=btns)


async def handle_page_callback(query, bot=None) -> None:
    """Обработчик кнопок навигации. Подключить к F.data.startswith('page:')"""
    parts = (query.data or "").split(":")
    if len(parts) != 3:
        await query.answer()
        return
    uid = int(parts[1])
    action = parts[2]
    state = _pages.get(uid)
    if not state:
        await query.answer("Сессия устарела")
        return
    if action == "next":
        state["page"] += 1
    elif action == "prev":
        state["page"] -= 1
    elif action == "close":
        _pages.pop(uid, None)
        await query.message.edit_text("🔍 Закрыто.")
        await query.answer()
        return
    await query.message.edit_text(
        get_page_text(uid),
        reply_markup=get_page_keyboard(uid),
        parse_mode="HTML",
    )
    await query.answer()
