"""nexus/handlers/notes.py — заметки со smart-select тегов"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from core.claude_client import ask_claude
from core.notion_client import note_add, get_db_options, log_error, update_page, query_pages
from core.option_helper import find_or_prepare, confirm_keyboard, pick_keyboard, format_option

logger = logging.getLogger("nexus.notes")
MOSCOW_TZ = timezone(timedelta(hours=3))

# Pending: user_id → {text, selected, new, existing, date, user_notion_id, chosen}
_pending: Dict[int, dict] = {}

TAGS_SYSTEM = """Выбери теги для заметки из предложенного списка существующих.
Если ни один не подходит — предложи новые (максимум 2).
Ответь ТОЛЬКО JSON без markdown:
{"selected": ["тег1"], "new": ["новый_тег"], "needs_confirm": true/false}
- needs_confirm=true только если предлагаешь новые теги, которых нет в списке
- new: [] если все нужные теги есть в existing"""


async def handle_note(
    message: Message,
    text: str,
    db_notes_id: str,
    tags: str = "",
    user_notion_id: str = "",
) -> None:
    """Основной обработчик заметки со smart-select тегов."""
    date = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")
    existing = await get_db_options(db_notes_id, "Теги")

    # Если теги переданы из classifier — нормализовать через find_or_prepare
    if tags:
        tag_list = []
        for item in tags.split(","):
            item = item.strip()
            if not item:
                continue
            from core.option_helper import strip_emoji
            item_clean = strip_emoji(item).lower()
            if item_clean:
                tag_list.append(item_clean)

        if tag_list:
            confirmed = []   # is_new=False, существующие
            pending_new = [] # is_new=True, новые

            for tag in tag_list:
                value, is_new = await find_or_prepare(db_notes_id, "Теги", tag)
                if is_new:
                    pending_new.append(value)
                else:
                    confirmed.append(value)

            if pending_new:
                uid = message.from_user.id
                _pending[uid] = {
                    "text": text,
                    "selected": confirmed,
                    "new": pending_new,
                    "existing": existing,
                    "date": date,
                    "user_notion_id": user_notion_id,
                }
                new_str = " · ".join(f"#{t}" for t in pending_new)
                existing_str = ", ".join(existing) if existing else "нет"
                kb = confirm_keyboard(uid, pending_new, existing)
                await message.answer(
                    f"💡 Не нашёл в Notion тег(и): <b>{new_str}</b>\n"
                    f"Существующие: <i>{existing_str}</i>",
                    reply_markup=kb,
                )
                return

            # Все теги найдены — сохранить сразу
            if confirmed:
                await _save_note(message, text, confirmed, date, user_notion_id=user_notion_id)
                return

    # Иначе — спросить Claude
    prompt = f"Заметка: {text}\nСуществующие теги: {existing}"
    raw = await ask_claude(prompt, system=TAGS_SYSTEM, max_tokens=100)

    try:
        data = json.loads(raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip())
    except Exception:
        data = {"selected": ["мысль"], "new": [], "needs_confirm": False}

    selected: List[str] = data.get("selected", [])
    new_tags: List[str] = data.get("new", [])
    needs_confirm: bool = data.get("needs_confirm", False) and bool(new_tags)

    if not needs_confirm:
        final_tags = selected if selected else ["🧠 Мысль"]
        await _save_note(message, text, final_tags, date, user_notion_id=user_notion_id)
        return

    # Нужно подтверждение новых тегов
    uid = message.from_user.id
    _pending[uid] = {
        "text": text,
        "selected": selected,
        "new": new_tags,
        "existing": existing,
        "date": date,
        "user_notion_id": user_notion_id,
    }

    existing_str = ", ".join(existing) if existing else "нет"
    kb = confirm_keyboard(uid, new_tags, existing)
    await message.answer(
        f"💡 Не нашёл подходящих тегов среди существующих:\n"
        f"<i>{existing_str}</i>\n\n"
        f"Предлагаю новые: <b>{' · '.join('#' + t for t in new_tags)}</b>",
        reply_markup=kb,
    )


async def handle_note_callback(query: CallbackQuery) -> None:
    """Обрабатывает opt_add/opt_pick/opt_skip/opt_sel/opt_done колбэки."""
    await query.answer()
    data = query.data or ""
    uid = query.from_user.id
    pending = _pending.get(uid)

    if not pending:
        await query.message.edit_text("⏱ Сессия истекла, напиши заметку ещё раз.")
        return

    text = pending["text"]
    date = pending["date"]
    selected = pending["selected"]
    new_tags = pending["new"]
    existing = pending["existing"]
    user_notion_id = pending.get("user_notion_id", "")

    if data.startswith("opt_add:"):
        tags = selected + new_tags
        del _pending[uid]
        await _save_note(query.message, text, tags, date, edit=True, user_notion_id=user_notion_id)

    elif data.startswith("opt_pick:"):
        if not existing:
            del _pending[uid]
            await _save_note(query.message, text, selected or ["🧠 Мысль"], date, edit=True, user_notion_id=user_notion_id)
            return
        _pending[uid]["chosen"] = list(selected)
        await query.message.edit_text("Выбери теги (можно несколько):", reply_markup=pick_keyboard(uid, existing))

    elif data.startswith("opt_sel:"):
        parts = data.split(":", 2)
        tag = parts[2] if len(parts) == 3 else ""
        chosen = _pending[uid].setdefault("chosen", [])
        if tag and tag not in chosen:
            chosen.append(tag)
        await query.answer(f"✓ {tag}")

    elif data.startswith("opt_done:"):
        chosen = _pending.get(uid, {}).get("chosen", selected or ["🧠 Мысль"])
        del _pending[uid]
        await _save_note(query.message, text, chosen, date, edit=True, user_notion_id=user_notion_id)

    elif data.startswith("opt_skip:"):
        del _pending[uid]
        await _save_note(query.message, text, selected or [], date, edit=True, user_notion_id=user_notion_id)


async def _save_note(
    message: Message,
    text: str,
    tags: List[str],
    date: str,
    edit: bool = False,
    user_notion_id: str = "",
) -> None:
    result = await note_add(text=text, tags=tags, date=date, user_notion_id=user_notion_id)
    tags_str = ", ".join(tags) if tags else "нет"
    reply = f"💡 Заметка сохранена! Теги: {tags_str}" if result else "⚠️ Ошибка записи в Notion."
    if edit:
        await message.edit_text(reply)
    else:
        await message.answer(reply)


async def handle_edit_note(message: Message, data: dict, user_notion_id: str) -> None:
    from core.config import config
    from core.notion_client import db_query, get_db_options, match_select
    hint = (data.get("hint") or "последняя").strip()
    new_value = (data.get("new_value") or "").strip()
    if not new_value:
        await message.answer("❌ Не указан новый тег")
        return
    db_id = os.environ.get("NOTION_DB_NOTES") or config.nexus.db_notes
    if hint == "последняя":
        results = await db_query(db_id, sorts=[{"property": "Дата", "direction": "descending"}], page_size=1)
    else:
        results = await db_query(db_id, filter_obj={"property": "Заголовок", "title": {"contains": hint}}, page_size=1)
    if not results:
        await message.answer("❌ Заметка не найдена")
        return
    page_id = results[0]["id"]
    normalized = await match_select(db_id, "Теги", new_value)
    options = await get_db_options(db_id, "Теги")
    tag_name = normalized if normalized in options else format_option(new_value)
    from core.notion_client import get_notion
    notion = get_notion()
    await notion.pages.update(page_id=page_id, properties={"Теги": {"multi_select": [{"name": tag_name}]}})
    await message.answer(f"✏️ Тег обновлён: {tag_name}")


async def handle_note_search(
    message: Message,
    query_text: str,
    user_notion_id: str = "",
) -> None:
    """Поиск заметок по тексту заголовка. Выводит до 5 результатов."""
    from core.notion_client import notes_search

    results = await notes_search(query_text, user_notion_id=user_notion_id)
    if not results:
        await message.answer("Заметок не найдено")
        return

    lines = []
    for page in results[:5]:
        props = page["properties"]
        title_parts = props.get("Заголовок", {}).get("title", [])
        title = title_parts[0]["plain_text"] if title_parts else "—"
        tags_items = props.get("Теги", {}).get("multi_select", [])
        tags_str = ", ".join(t["name"] for t in tags_items)
        date = (props.get("Дата", {}).get("date") or {}).get("start", "")[:10]
        line = f"💡 {title}"
        if tags_str:
            line += f" [{tags_str}]"
        if date:
            line += f" · {date}"
        lines.append(line)

    await message.answer("\n".join(lines))
