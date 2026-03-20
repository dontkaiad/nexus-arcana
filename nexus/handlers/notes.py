"""nexus/handlers/notes.py — заметки со smart-select тегов"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from core.claude_client import ask_claude
from core.notion_client import note_add, get_db_options, log_error

logger = logging.getLogger("nexus.notes")
MOSCOW_TZ = timezone(timedelta(hours=3))

# Pending: user_id → {text, suggested_tags, existing_tags, date, user_notion_id}
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
    from core.notion_client import match_select

    date = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")
    existing = await get_db_options(db_notes_id, "Теги")

    # Маппинг известных тегов на эмодзи (fallback если не добавился в classifier)
    TAGS_EMOJI = {
        "практика": "🔮",
        "таро": "🔮",
        "ритуал": "🕯️",
        "расходники": "🕯️",
        "идея": "💡",
        "рецепт": "🍳",
        "здоровье": "❤️",
        "финансы": "💰",
        "мысль": "🧠",
    }

    # Если теги переданы из classifier — нормализовать и сохранить
    if tags:
        tag_list = []
        for item in tags.split(","):
            item = item.strip()
            if not item:
                continue
            item_clean = item.lstrip("🔮🕯️💡🍳❤️💰🧠").strip().lower()
            if item_clean:
                tag_list.append(item_clean)

        if tag_list:
            normalized_tags = []
            unknown_tags = []
            for tag in tag_list:
                if tag in TAGS_EMOJI:
                    tag_with_emoji = f"{TAGS_EMOJI[tag]} {tag.capitalize()}"
                    normalized = await match_select(db_notes_id, "Теги", tag_with_emoji)
                else:
                    normalized = await match_select(db_notes_id, "Теги", tag)
                # match_select возвращает исходное значение если не нашёл
                options = await get_db_options(db_notes_id, "Теги")
                if normalized in options:
                    normalized_tags.append(normalized)
                else:
                    unknown_tags.append(tag)

            if unknown_tags:
                uid = message.from_user.id
                _pending[uid] = {
                    "text": text,
                    "selected": normalized_tags,
                    "new": unknown_tags,
                    "existing": existing,
                    "date": date,
                    "user_notion_id": user_notion_id,
                }
                new_str = " · ".join(f"#{t}" for t in unknown_tags)
                existing_str = ", ".join(existing) if existing else "нет"
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=f"✅ Добавить {new_str}", callback_data=f"note_new:{uid}")],
                    [InlineKeyboardButton(text="📋 Выбрать из существующих", callback_data=f"note_pick:{uid}")],
                    [InlineKeyboardButton(text="💾 Сохранить без новых тегов", callback_data=f"note_skip:{uid}")],
                ])
                await message.answer(
                    f"💡 Не нашёл в Notion тег(и): <b>{new_str}</b>\n"
                    f"Существующие: <i>{existing_str}</i>",
                    reply_markup=kb,
                )
                return

            # Все теги найдены — сохранить сразу
            if normalized_tags:
                await _save_note(message, text, normalized_tags, date, user_notion_id=user_notion_id)
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
        # Только существующие теги — new_tags не сохранять без подтверждения
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
    new_str = " · ".join(f"#{t}" for t in new_tags)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ Добавить {new_str}", callback_data=f"note_new:{uid}")],
        [InlineKeyboardButton(text="📋 Выбрать из существующих", callback_data=f"note_pick:{uid}")],
        [InlineKeyboardButton(text="💾 Сохранить без тегов", callback_data=f"note_skip:{uid}")],
    ])

    await message.answer(
        f"💡 Не нашёл подходящих тегов среди существующих:\n"
        f"<i>{existing_str}</i>\n\n"
        f"Предлагаю новые: <b>{new_str}</b>",
        reply_markup=kb,
    )


async def handle_note_callback(query: CallbackQuery) -> None:
    """Обрабатывает выбор пользователя по тегам."""
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

    if data.startswith("note_new:"):
        tags = selected + new_tags
        del _pending[uid]
        await _save_note(query.message, text, tags, date, edit=True, user_notion_id=user_notion_id)

    elif data.startswith("note_pick:"):
        if not existing:
            del _pending[uid]
            await _save_note(query.message, text, selected or ["мысль"], date, edit=True, user_notion_id=user_notion_id)
            return
        buttons = [
            [InlineKeyboardButton(text=t, callback_data=f"note_tag:{uid}:{t}")]
            for t in existing
        ]
        buttons.append([InlineKeyboardButton(text="✅ Готово", callback_data=f"note_done:{uid}")])
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
        _pending[uid]["chosen"] = list(selected)
        await query.message.edit_text("Выбери теги (можно несколько):", reply_markup=kb)

    elif data.startswith("note_tag:") or data.startswith("note_sel:"):
        # note_tag:{uid}:{tag} — выбор тега из списка
        parts = data.split(":", 2)
        tag = parts[2] if len(parts) == 3 else ""
        chosen = _pending[uid].setdefault("chosen", [])
        if tag and tag not in chosen:
            chosen.append(tag)
        await query.answer(f"✓ {tag}")

    elif data.startswith("note_done:"):
        chosen = _pending.get(uid, {}).get("chosen", selected or ["мысль"])
        del _pending[uid]
        await _save_note(query.message, text, chosen, date, edit=True, user_notion_id=user_notion_id)

    elif data.startswith("note_skip:"):
        del _pending[uid]
        await _save_note(query.message, text, [], date, edit=True, user_notion_id=user_notion_id)


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
