"""arcana/handlers/grimoire.py — Гримуар: view layer + CRUD."""
from __future__ import annotations

import json
import logging
from typing import List, Optional

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from core.claude_client import ask_claude
from core.notion_client import (
    grimoire_add,
    grimoire_list_by_category,
    grimoire_search,
    rituals_all,
    log_error,
    _extract_text,
    _extract_select,
)

logger = logging.getLogger("arcana.grimoire")
router = Router()

GRIMOIRE_CATEGORIES = {
    "заговор":    "📿 Заговор",
    "рецепт":     "🧴 Рецепт",
    "комбинация": "✨ Комбинация",
    "заметка":    "📝 Заметка",
}

GRIMOIRE_THEMES = {
    "финансы":     "💰 Финансы",
    "деньги":      "💰 Финансы",
    "любовь":      "💕 Любовь",
    "защита":      "🛡️ Защита",
    "деструктив":  "💀 Деструктив",
    "привлечение": "🧲 Привлечение",
    "очищение":    "🌊 Очищение",
    "другое":      "🔮 Другое",
}

PARSE_GRIMOIRE_SYSTEM = (
    "Извлеки данные для записи в гримуар. Ответь ТОЛЬКО JSON без markdown:\n"
    '{"title": "название", "category": "заговор/рецепт/комбинация/заметка", '
    '"themes": ["финансы", "защита"], "text": "полный текст записи", '
    '"source": "откуда узнала или null"}'
)

_AWAIT_SEARCH_KEY = "grim_await_search"
_pending_search: dict = {}  # uid → True


def _menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🕯️ Ритуалы",   callback_data="grim_rituals"),
            InlineKeyboardButton(text="📿 Заговоры",   callback_data="grim_spells"),
        ],
        [
            InlineKeyboardButton(text="🧴 Рецепты",    callback_data="grim_recipes"),
            InlineKeyboardButton(text="✨ Комбинации", callback_data="grim_combos"),
        ],
        [
            InlineKeyboardButton(text="📦 Инвентарь",  callback_data="grim_inventory"),
            InlineKeyboardButton(text="📝 Заметки",    callback_data="grim_notes"),
        ],
        [
            InlineKeyboardButton(text="🔍 Поиск",      callback_data="grim_search"),
        ],
    ])


def _back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="grim_menu")],
    ])


def _parse_json_safe(raw: str) -> dict:
    try:
        return json.loads(raw.strip())
    except Exception:
        return {}


def _extract_multi_select(prop: dict) -> List[str]:
    return [opt["name"] for opt in prop.get("multi_select", [])]


def _extract_checkbox(prop: dict) -> bool:
    return bool(prop.get("checkbox", False))


def _format_grimoire_list(items: List[dict], title: str) -> str:
    if not items:
        return f"{title}\n\nПусто."
    lines = [f"<b>{title} ({len(items)})</b>\n"]
    for i, item in enumerate(items[:10], 1):
        p = item.get("properties", {})
        name = _extract_text(p.get("Название", {}))
        themes = _extract_multi_select(p.get("Тема", {}))
        verified = _extract_checkbox(p.get("Проверено", {}))
        theme_str = " · ".join(t.split(" ")[0] for t in themes) if themes else ""
        check = " · ✅" if verified else ""
        lines.append(f"{i}. {name}" + (f" · {theme_str}" if theme_str else "") + check)
    if len(items) > 10:
        lines.append(f"\n… ещё {len(items) - 10}")
    return "\n".join(lines)


def _format_ritual_list(items: List[dict]) -> str:
    if not items:
        return "<b>🕯️ Ритуалы</b>\n\nПусто."
    lines = [f"<b>🕯️ Ритуалы ({len(items)})</b>\n"]
    for i, item in enumerate(items[:10], 1):
        p = item.get("properties", {})
        name = _extract_text(p.get("Тема", {})) or _extract_text(p.get("Название", {})) or "—"
        result = _extract_select(p.get("Результат", {}))
        result_icon = {"✅ Сработало": "✅", "❌ Не сработало": "❌", "〰️ Частично": "〰️"}.get(result, "⏳")
        date_raw = item.get("properties", {}).get("Дата", {}).get("date") or {}
        date_str = (date_raw.get("start") or "")[:10]
        lines.append(f"{i}. {name}" + (f" · {date_str}" if date_str else "") + f" {result_icon}")
    if len(items) > 10:
        lines.append(f"\n… ещё {len(items) - 10}")
    return "\n".join(lines)


# ── Menu ──────────────────────────────────────────────────────────────────────

async def handle_grimoire_menu(message: Message, user_notion_id: str = "") -> None:
    await message.answer("📖 <b>Гримуар</b>", reply_markup=_menu_keyboard(), parse_mode="HTML")


# ── Callback handlers ─────────────────────────────────────────────────────────

async def _get_user_notion_id(callback: CallbackQuery) -> str:
    """Извлечь user_notion_id из middleware data (через bot_data fallback)."""
    # middleware прикрепляет user_notion_id к data при каждом апдейте;
    # для callback_query тоже проходит через middleware, значение доступно напрямую
    return ""  # будет перекрыто при регистрации через wrapper


@router.callback_query(F.data == "grim_menu")
async def cb_grim_menu(callback: CallbackQuery, user_notion_id: str = "") -> None:
    await callback.message.edit_text("📖 <b>Гримуар</b>", reply_markup=_menu_keyboard(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "grim_rituals")
async def cb_grim_rituals(callback: CallbackQuery, user_notion_id: str = "") -> None:
    await callback.answer()
    try:
        items = await rituals_all(user_notion_id)
        text = _format_ritual_list(items)
        await callback.message.edit_text(text, reply_markup=_back_keyboard(), parse_mode="HTML")
    except Exception as e:
        logger.exception("cb_grim_rituals: %s", e)
        await callback.message.edit_text("Ошибка загрузки ритуалов.", reply_markup=_back_keyboard())


@router.callback_query(F.data == "grim_spells")
async def cb_grim_spells(callback: CallbackQuery, user_notion_id: str = "") -> None:
    await callback.answer()
    try:
        items = await grimoire_list_by_category("📿 Заговор", user_notion_id)
        text = _format_grimoire_list(items, "📿 Заговоры")
        await callback.message.edit_text(text, reply_markup=_back_keyboard(), parse_mode="HTML")
    except Exception as e:
        logger.exception("cb_grim_spells: %s", e)
        await callback.message.edit_text("Ошибка загрузки.", reply_markup=_back_keyboard())


@router.callback_query(F.data == "grim_recipes")
async def cb_grim_recipes(callback: CallbackQuery, user_notion_id: str = "") -> None:
    await callback.answer()
    try:
        items = await grimoire_list_by_category("🧴 Рецепт", user_notion_id)
        text = _format_grimoire_list(items, "🧴 Рецепты")
        await callback.message.edit_text(text, reply_markup=_back_keyboard(), parse_mode="HTML")
    except Exception as e:
        logger.exception("cb_grim_recipes: %s", e)
        await callback.message.edit_text("Ошибка загрузки.", reply_markup=_back_keyboard())


@router.callback_query(F.data == "grim_combos")
async def cb_grim_combos(callback: CallbackQuery, user_notion_id: str = "") -> None:
    await callback.answer()
    try:
        items = await grimoire_list_by_category("✨ Комбинация", user_notion_id)
        text = _format_grimoire_list(items, "✨ Комбинации")
        await callback.message.edit_text(text, reply_markup=_back_keyboard(), parse_mode="HTML")
    except Exception as e:
        logger.exception("cb_grim_combos: %s", e)
        await callback.message.edit_text("Ошибка загрузки.", reply_markup=_back_keyboard())


@router.callback_query(F.data == "grim_notes")
async def cb_grim_notes(callback: CallbackQuery, user_notion_id: str = "") -> None:
    await callback.answer()
    try:
        items = await grimoire_list_by_category("📝 Заметка", user_notion_id)
        text = _format_grimoire_list(items, "📝 Заметки")
        await callback.message.edit_text(text, reply_markup=_back_keyboard(), parse_mode="HTML")
    except Exception as e:
        logger.exception("cb_grim_notes: %s", e)
        await callback.message.edit_text("Ошибка загрузки.", reply_markup=_back_keyboard())


@router.callback_query(F.data == "grim_inventory")
async def cb_grim_inventory(callback: CallbackQuery, user_notion_id: str = "") -> None:
    await callback.answer()
    try:
        from arcana.handlers.lists import _fetch_all_display_items, render_inv_screen, BOT_NAME
        all_items = await _fetch_all_display_items(None, BOT_NAME, user_notion_id)
        text, buttons = render_inv_screen(all_items)
        # replace back button with grimoire back
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="grim_menu")],
        ])
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        logger.exception("cb_grim_inventory: %s", e)
        await callback.message.edit_text("Ошибка загрузки инвентаря.", reply_markup=_back_keyboard())


@router.callback_query(F.data == "grim_search")
async def cb_grim_search(callback: CallbackQuery, user_notion_id: str = "") -> None:
    await callback.answer()
    uid = callback.from_user.id
    _pending_search[uid] = user_notion_id
    await callback.message.edit_text(
        "🔍 Введи поисковый запрос (слово, тема или категория):",
        reply_markup=_back_keyboard(),
    )


# ── Text handlers (add / search) ──────────────────────────────────────────────

async def handle_grimoire_add(message: Message, text: str, user_notion_id: str = "") -> None:
    """Записать новую запись в гримуар. «запиши в гримуар: ...»"""
    try:
        raw = await ask_claude(
            f"Текст: {text}",
            system=PARSE_GRIMOIRE_SYSTEM,
            max_tokens=400,
            model="haiku",
        )
        data = _parse_json_safe(raw)
        if not data.get("title"):
            await message.answer("Не смогла распознать запись. Попробуй написать подробнее.")
            return

        cat_key = (data.get("category") or "заметка").lower()
        category = GRIMOIRE_CATEGORIES.get(cat_key, "📝 Заметка")

        raw_themes = data.get("themes") or []
        themes = [GRIMOIRE_THEMES.get(t.lower(), t) for t in raw_themes if t]

        page_id = await grimoire_add(
            title=data["title"],
            category=category,
            themes=themes if themes else None,
            text=data.get("text") or "",
            source=data.get("source") or "",
            user_notion_id=user_notion_id,
        )
        if page_id:
            theme_str = (" · ".join(themes)) if themes else ""
            reply = f"📖 Записано в гримуар: {category} <b>{data['title']}</b>"
            if theme_str:
                reply += f" [{theme_str}]"
            await message.answer(reply, parse_mode="HTML")
        else:
            await message.answer("Не удалось записать в гримуар.")
    except Exception as e:
        logger.exception("handle_grimoire_add: %s", e)
        await log_error(str(e), context="handle_grimoire_add", bot_label="🌒 Arcana")
        await message.answer("Ошибка при записи в гримуар.")


async def handle_grimoire_search(message: Message, text: str, user_notion_id: str = "") -> None:
    """Поиск в гримуаре по тексту или теме."""
    try:
        # Определить тему из текста
        theme: Optional[str] = None
        query = text.strip()
        for key, val in GRIMOIRE_THEMES.items():
            if key in query.lower():
                theme = val
                query = query.lower().replace(key, "").strip()
                break

        items = await grimoire_search(
            query=query if len(query) >= 2 else "",
            theme=theme,
            user_notion_id=user_notion_id,
        )

        if not items:
            await message.answer("📖 Ничего не найдено в гримуаре.")
            return

        if len(items) == 1:
            # Показать полную запись
            p = items[0].get("properties", {})
            name = _extract_text(p.get("Название", {}))
            cat = _extract_select(p.get("Категория", {}))
            themes_list = _extract_multi_select(p.get("Тема", {}))
            verified = _extract_checkbox(p.get("Проверено", {}))
            body = _extract_text(p.get("Текст", {}))
            source = _extract_text(p.get("Источник", {}))

            reply = f"{cat} <b>{name}</b>\n"
            if themes_list:
                reply += " · ".join(themes_list) + "\n"
            if verified:
                reply += "✅ Проверено\n"
            if body:
                reply += f"\n📜 Текст:\n{body}\n"
            if source:
                reply += f"\n📚 Источник: {source}"
            await message.answer(reply.strip(), parse_mode="HTML")
        else:
            text_out = _format_grimoire_list(items, "🔍 Результаты поиска")
            await message.answer(text_out, parse_mode="HTML")

    except Exception as e:
        logger.exception("handle_grimoire_search: %s", e)
        await log_error(str(e), context="handle_grimoire_search", bot_label="🌒 Arcana")
        await message.answer("Ошибка поиска в гримуаре.")


async def check_pending_search(message: Message, text: str) -> bool:
    """Если юзер ожидает ввода поискового запроса — обработать и вернуть True."""
    uid = message.from_user.id
    if uid not in _pending_search:
        return False
    user_notion_id = _pending_search.pop(uid)
    await handle_grimoire_search(message, text, user_notion_id)
    return True
