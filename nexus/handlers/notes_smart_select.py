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

# Pending: user_id → {text, suggested_tags, existing_tags, date}
_pending: Dict[int, dict] = {}

TAGS_SYSTEM = """Выбери теги для заметки из предложенного списка существующих.
Если ни один не подходит — предложи новые (максимум 2).
Ответь ТОЛЬКО JSON без markdown:
{"selected": ["тег1"], "new": ["новый_тег"], "needs_confirm": true/false}
- needs_confirm=true только если предлагаешь новые теги, которых нет в списке
- new: [] если все нужные теги есть в existing"""


async def handle_note(message: Message, text: str, db_notes_id: str) -> None:
    """Основной обработчик заметки со smart-select тегов."""
    date = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")
    existing = await get_db_options(db_notes_id, "Теги")

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
        # Всё хорошо — сохраняем сразу
        tags = selected + new_tags
        await _save_note(message, text, tags, date)
        return

    # Нужно подтверждение новых тегов
    uid = message.from_user.id
    _pending[uid] = {"text": text, "selected": selected, "new": new_tags,
                     "existing": existing, "date": date}

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

    if data.startswith("note_new:"):
        tags = selected + new_tags
        del _pending[uid]
        await query.message.edit_text(f"💡 Сохраняю с новыми тегами: {' '.join(f'#{t}' for t in tags)}")
        await _save_note(query.message, text, tags, date, edit=True)

    elif data.startswith("note_pick:"):
        # Показываем кнопки с существующими тегами
        if not existing:
            del _pending[uid]
            await _save_note(query.message, text, selected or ["мысль"], date, edit=True)
            return
        buttons = [
            [InlineKeyboardButton(text=t, callback_data=f"note_sel:{uid}:{t}")]
            for t in existing
        ]
        buttons.append([InlineKeyboardButton(text="✅ Готово", callback_data=f"note_done:{uid}")])
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
        # Запоминаем выбор в pending
        _pending[uid]["chosen"] = list(selected)
        await query.message.edit_text("Выбери теги (можно несколько):", reply_markup=kb)

    elif data.startswith("note_sel:"):
        parts = data.split(":", 2)
        tag = parts[2] if len(parts) == 3 else ""
        chosen = _pending[uid].setdefault("chosen", [])
        if tag and tag not in chosen:
            chosen.append(tag)
        await query.answer(f"✓ {tag}")

    elif data.startswith("note_done:"):
        chosen = _pending.get(uid, {}).get("chosen", selected or ["мысль"])
        del _pending[uid]
        await query.message.edit_text(f"💡 Сохраняю: {' '.join(f'#{t}' for t in chosen)}")
        await _save_note(query.message, text, chosen, date, edit=True)

    elif data.startswith("note_skip:"):
        del _pending[uid]
        await _save_note(query.message, text, [], date, edit=True)


async def _save_note(message: Message, text: str, tags: List[str], date: str, edit: bool = False) -> None:
    result = await note_add(text=text, tags=tags, date=date)
    reply = f"💡 Заметка сохранена\n{' '.join(f'#{t}' for t in tags)}" if result else "⚠️ Ошибка записи в Notion."
    if edit:
        await message.edit_text(reply)
    else:
        await message.answer(reply)


# ──────────────────────────────────────────────────────────────────────────────
# РЕГИСТРАЦИЯ В nexus_bot.py
# ──────────────────────────────────────────────────────────────────────────────
# 1. Добавить импорт:
#    from nexus.handlers.notes import handle_note_callback
#
# 2. Добавить handler в dp (ПЕРЕД handle_unauthorized):
#
# @dp.callback_query(lambda c: c.data and c.data.startswith("note_"))
# async def on_note_callback(query: CallbackQuery) -> None:
#     await handle_note_callback(query)
#
# 3. В _process_item, блок kind == "note":
#    Заменить вызов note_add на:
#
#    from core.config import config
#    from nexus.handlers.notes import handle_note as _handle_note
#    await _handle_note(msg_obj, data.get("text", original_text), config.nexus.db_notes)
#    return ""   # ответ отправляет сам handler
#
#    (нужно передать msg объект — добавить msg как параметр в _process_item)
