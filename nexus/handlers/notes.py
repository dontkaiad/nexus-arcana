"""nexus/handlers/notes.py — заметки со smart-select тегов"""
from __future__ import annotations

import json
import logging
import os
import random
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from nexus.handlers.utils import react

from core.claude_client import ask_claude
from core.notion_client import note_add, get_db_options, log_error, update_page, query_pages
from core.option_helper import find_or_prepare, confirm_keyboard, pick_keyboard, format_option

logger = logging.getLogger("nexus.notes")
MOSCOW_TZ = timezone(timedelta(hours=3))

# Pending: user_id → {text, selected, new, existing, date, user_notion_id, chosen}
_pending: Dict[int, dict] = {}

# Последний дайджест: user_id → [{"page_id": ..., "title": ..., "tags": [...]}]
_last_digest_results: Dict[int, List[dict]] = {}

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
    """Обрабатывает opt_add/opt_pick/opt_skip/opt_sel/opt_done и note_replace колбэки."""
    await query.answer()
    data = query.data or ""
    uid = query.from_user.id

    # ── note_replace:{uid}:{old_tag}:{new_value} ─────────────────────────────
    if data.startswith("note_replace:"):
        parts = data.split(":", 3)
        if len(parts) != 4:
            return
        old_tag = parts[2]
        new_tag = parts[3]
        pending = _pending.pop(uid, {})
        page_id = pending.get("page_id")
        current_tags = pending.get("current_tags", [])
        if not page_id:
            await query.message.edit_text("⏱ Сессия истекла, попробуй ещё раз.")
            return
        # Заменить old_tag на new_tag, остальные оставить
        updated = [new_tag if t == old_tag else t for t in current_tags]
        from core.notion_client import get_notion
        notion = get_notion()
        await notion.pages.update(
            page_id=page_id,
            properties={"Теги": {"multi_select": [{"name": t} for t in updated]}},
        )
        await query.message.edit_text(f"✏️ Тег <b>{old_tag}</b> → <b>{new_tag}</b>")
        return

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
    if result:
        await react(message, "📝")
    if edit:
        await message.edit_text(reply)
    else:
        await message.answer(reply)


async def handle_edit_note(message: Message, data: dict, user_notion_id: str) -> None:
    from core.notion_client import db_query, get_notion
    hint = (data.get("hint") or "последняя").strip()
    new_value = (data.get("new_value") or "").strip()
    if not new_value:
        await message.answer("❌ Не указан новый тег")
        return
    db_id = os.environ.get("NOTION_DB_NOTES")
    if not db_id:
        await message.answer("❌ NOTION_DB_NOTES не задан")
        return
    if hint == "последняя":
        results = await db_query(db_id, sorts=[{"property": "Дата", "direction": "descending"}], page_size=1)
    else:
        results = await db_query(db_id, filter_obj={"property": "Заголовок", "title": {"contains": hint}}, page_size=1)
    if not results:
        await message.answer("❌ Заметка не найдена")
        return
    page = results[0]
    page_id = page["id"]
    current_tags = [
        t["name"]
        for t in (page["properties"].get("Теги") or {}).get("multi_select", [])
    ]
    tag_name = format_option(new_value)
    uid = message.from_user.id

    if len(current_tags) > 1:
        # Спросить, какой тег заменить
        _pending[uid] = {"page_id": page_id, "current_tags": current_tags, "new_value": tag_name}
        buttons = [
            [InlineKeyboardButton(
                text=t,
                callback_data=f"note_replace:{uid}:{t}:{tag_name}"
            )]
            for t in current_tags
        ]
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
        await message.answer(
            f"Какой тег заменить на <b>{tag_name}</b>?",
            reply_markup=kb,
        )
        return

    # 0 или 1 тег — заменяем сразу (или просто выставляем новый)
    notion = get_notion()
    await notion.pages.update(
        page_id=page_id,
        properties={"Теги": {"multi_select": [{"name": tag_name}]}},
    )
    await message.answer(f"✏️ Тег обновлён: {tag_name}")


_ADHD_TIPS = [
    "💡 Совет: разбери хотя бы одну — мозгу нравится закрывать гештальты",
    "🧠 Трюк: поставь таймер на 5 минут и разбери сколько успеешь",
    "🎯 Факт: решение «удалить или оставить» — тоже действие, и оно считается",
    "⚡ Лайфхак: начни с самой короткой заметки — дофамин от быстрой победы",
    "🌀 Напоминание: незакрытые заметки занимают RAM в голове, даже если ты о них не думаешь",
    "✨ Мотивация: будущий ты скажет спасибо за каждую разобранную заметку",
    "🔥 Приём: не надо идеально — просто реши: задача, удалить или оставить",
]


async def send_notes_digest(bot, user_tg_id: int, user_notion_id: str) -> None:
    """Напоминание о неразобранных заметках (>7 дней)."""
    from core.notion_client import db_query

    db_id = os.environ.get("NOTION_DB_NOTES")
    if not db_id:
        return

    cutoff = (datetime.now(MOSCOW_TZ) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    filter_obj: dict = {"property": "Дата", "date": {"before": cutoff}}
    if user_notion_id:
        filter_obj = {
            "and": [
                filter_obj,
                {"property": "🪪 Пользователи", "relation": {"contains": user_notion_id}},
            ]
        }

    try:
        pages = await db_query(
            db_id,
            filter_obj=filter_obj,
            sorts=[{"property": "Дата", "direction": "ascending"}],
            page_size=50,
        )
    except Exception as e:
        logger.error("send_notes_digest: query error: %s", e)
        return

    if not pages:
        return

    n = len(pages)
    tip = random.choice(_ADHD_TIPS)
    text = f"📝 У тебя {n} не разобранных заметок\n\n{tip}\n\nЕсли хочешь разобрать сейчас — /notes"

    try:
        await bot.send_message(user_tg_id, text)
    except Exception as e:
        logger.error("send_notes_digest: send error tg_id=%s: %s", user_tg_id, e)


async def send_notes_digest_all(bot) -> None:
    """Отправить дайджест заметок всем разрешённым пользователям."""
    from core.config import config
    from core.user_manager import get_user

    for tg_id in config.allowed_ids:
        try:
            user_data = await get_user(tg_id)
            if not user_data:
                continue
            user_notion_id = user_data.get("notion_page_id", "")
            await send_notes_digest(bot, tg_id, user_notion_id)
        except Exception as e:
            logger.error("send_notes_digest_all: tg_id=%s error: %s", tg_id, e)


async def handle_note_search(
    message: Message,
    data,  # dict с "query" или строка (legacy)
    user_notion_id: str = "",
) -> None:
    """Поиск заметок с пагинацией + дайджест неразобранных (объединение notes + notes_digest)."""
    import asyncio
    from core.notion_client import db_query
    from core.pagination import register_pages, get_page_text, get_page_keyboard

    # Поддержка старого вызова со строкой и нового с dict
    if isinstance(data, dict):
        q = (data.get("query") or "").strip()
    else:
        q = (data or "").strip()

    db_id = os.environ.get("NOTION_DB_NOTES")
    if not db_id:
        await message.answer("❌ NOTION_DB_NOTES не задан")
        return

    if not q:
        combined = await db_query(
            db_id,
            sorts=[{"property": "Дата", "direction": "descending"}],
            page_size=50,
        )
    else:
        tag_filter   = {"property": "Теги",      "multi_select": {"contains": q}}
        title_filter = {"property": "Заголовок", "title":        {"contains": q}}
        tag_results, title_results = await asyncio.gather(
            db_query(db_id, filter_obj=tag_filter,   page_size=30),
            db_query(db_id, filter_obj=title_filter, page_size=30),
        )
        seen: set = set()
        combined = []
        for page in tag_results + title_results:
            pid = page["id"]
            if pid not in seen:
                seen.add(pid)
                combined.append(page)

    if not combined:
        await message.answer("💡 Заметок не найдено")
        return

    # Считаем неразобранные (старше 7 дней) для дайджест-шапки
    digest_header = ""
    if not q:
        cutoff = (datetime.now(MOSCOW_TZ) - timedelta(days=7)).strftime("%Y-%m-%d")
        old_count = sum(
            1 for p in combined
            if ((p["properties"].get("Дата", {}).get("date") or {}).get("start", "")[:10] or "9999") < cutoff
        )
        if old_count > 0:
            tip = random.choice(_ADHD_TIPS)
            digest_header = f"📬 Не разобрано: {old_count} шт.\n{tip}\n\n"

    # Преобразовать Notion-страницы в простые dict для formatter
    def _parse(item: dict) -> dict:
        props = item["properties"]
        title_parts = props.get("Заголовок", {}).get("title", [])
        title = title_parts[0]["plain_text"] if title_parts else "—"
        tags_items = props.get("Теги", {}).get("multi_select", [])
        tags_str = " ".join(f"#{t['name']}" for t in tags_items)
        # Категория (select property, если есть)
        cat = (props.get("Категория", {}).get("select") or {}).get("name", "")
        date = (props.get("Дата", {}).get("date") or {}).get("start", "")[:10]
        return {"title": title, "tags": tags_str, "cat": cat, "date": date}

    def _fmt(it: dict) -> str:
        line = f"💡 {it['title']}"
        meta_parts = []
        if it.get("cat"):
            meta_parts.append(it["cat"])
        if it.get("tags"):
            meta_parts.append(it["tags"])
        if it.get("date"):
            meta_parts.append(it["date"])
        if meta_parts:
            line += "\n   " + " · ".join(meta_parts)
        return line

    uid = message.from_user.id
    items = [_parse(p) for p in combined]
    header = f"🔍 {q}" if q else "📝 Заметки"
    if digest_header:
        header = digest_header + header
    register_pages(uid, items, header, _fmt)
    await message.answer(get_page_text(uid), reply_markup=get_page_keyboard(uid))


async def handle_note_delete(message: Message, data: dict, user_notion_id: str = "") -> None:
    """Удалить заметки из последнего дайджеста по ключевому слову."""
    from core.notion_client import get_notion

    uid = message.from_user.id
    hint = (data.get("hint") or "").strip().lower()
    delete_all = data.get("delete_all", False)

    digest_pages = _last_digest_results.get(uid, [])

    if not digest_pages:
        await message.answer("❌ Нет свежего дайджеста — отправь запрос ещё раз после дайджеста")
        return

    if delete_all and not hint:
        targets = digest_pages
    elif hint:
        targets = [
            p for p in digest_pages
            if hint in p["title"].lower() or any(hint in t.lower() for t in p["tags"])
        ]
    else:
        targets = []

    if not targets:
        hint_display = f" про «{hint}»" if hint else ""
        await message.answer(f"❌ Не нашёл заметок{hint_display} в последнем дайджесте")
        return

    notion = get_notion()
    deleted = 0
    for p in targets:
        try:
            await notion.pages.update(page_id=p["page_id"], archived=True)
            deleted += 1
        except Exception as e:
            logger.error("handle_note_delete: page_id=%s error=%s", p["page_id"], e)

    # Убрать удалённые из кэша
    deleted_ids = {p["page_id"] for p in targets}
    _last_digest_results[uid] = [p for p in digest_pages if p["page_id"] not in deleted_ids]

    hint_display = f" про «{hint}»" if hint else ""
    n = deleted
    suffix = "у" if n == 1 else "и" if n < 5 else ""
    await message.answer(f"🗑️ Удалил {n} заметк{suffix}{hint_display}")
