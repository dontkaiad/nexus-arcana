"""nexus/handlers/notes.py — заметки со smart-select тегов"""
from __future__ import annotations

import logging
import random
from datetime import datetime, timezone, timedelta
from typing import Dict, List

from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from nexus.handlers.utils import react

from core.claude_client import ask_claude
from core.option_helper import confirm_keyboard, pick_keyboard, format_option
from nexus.repos.notes_repo import NotesRepo

_repo = NotesRepo()

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
    import json
    date = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")
    existing = await _repo.get_all_tags()

    # Если теги переданы из classifier — нормализовать через find_or_prepare_tag
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
                value, is_new = await _repo.find_or_prepare_tag(tag)
                if is_new:
                    pending_new.append(value)
                else:
                    confirmed.append(value)

            if pending_new:
                uid = message.chat.id
                logger.info("handle_note: storing pending for uid=%s (chat.id), new_tags=%s", uid, pending_new)
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
                    f"💡 Не нашёл тег(и): <b>{new_str}</b>\n"
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
    raw = await ask_claude(prompt, system=TAGS_SYSTEM, max_tokens=100,
                           model="claude-haiku-4-5-20251001", temperature=0)

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
    uid = message.chat.id
    logger.info("handle_note: storing pending for uid=%s (chat.id), new_tags=%s", uid, new_tags)
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
    logger.info("handle_note_callback: uid=%s data=%s pending_keys=%s", uid, data, list(_pending.keys()))

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
        updated = [new_tag if t == old_tag else t for t in current_tags]
        await _repo.update_tags(page_id, updated)
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
    result = await _repo.add(text=text, tags=tags, date=date, user_notion_id=user_notion_id)
    tags_str = ", ".join(tags) if tags else "нет"
    reply = f"💡 Заметка сохранена! Теги: {tags_str}" if result else "⚠️ Ошибка записи."
    if result:
        await react(message, "✍️")
    if edit:
        await message.edit_text(reply)
    else:
        await message.answer(reply)


async def handle_edit_note(message: Message, data: dict, user_notion_id: str) -> None:
    hint = (data.get("hint") or "последняя").strip()
    new_value = (data.get("new_value") or "").strip()
    if not new_value:
        await message.answer("❌ Не указан новый тег")
        return
    note = await _repo.find_for_edit(hint, user_notion_id=user_notion_id)
    if not note:
        await message.answer("❌ Заметка не найдена")
        return
    tag_name = format_option(new_value)
    uid = message.from_user.id

    if len(note.tags) > 1:
        _pending[uid] = {"page_id": note.id, "current_tags": note.tags, "new_value": tag_name}
        buttons = [
            [InlineKeyboardButton(
                text=t,
                callback_data=f"note_replace:{uid}:{t}:{tag_name}"
            )]
            for t in note.tags
        ]
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
        await message.answer(
            f"Какой тег заменить на <b>{tag_name}</b>?",
            reply_markup=kb,
        )
        return

    await _repo.update_tags(note.id, [tag_name])
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
    notes_list = await _repo.find_older_than_days(user_notion_id=user_notion_id, days=7)
    if not notes_list:
        return

    n = len(notes_list)
    tip = random.choice(_ADHD_TIPS)
    text = (
        f"📝 Дайджест заметок за неделю\n\n"
        f"У тебя {n} не разобранных заметок\n\n{tip}\n\n"
        f"Если хочешь разобрать сейчас — /notes"
    )
    try:
        await bot.send_message(user_tg_id, text)
    except Exception as e:
        logger.error("send_notes_digest: send error tg_id=%s: %s", user_tg_id, e)


async def send_notes_digest_all(bot) -> None:
    """Отправить дайджест заметок пользователям с permissions.nexus."""
    from core.config import config
    from core.user_manager import get_user

    seen: set = set()
    for tg_id in config.allowed_ids:
        if tg_id in seen:
            continue
        seen.add(tg_id)
        try:
            user_data = await get_user(tg_id)
            if not user_data:
                continue
            if not user_data.get("permissions", {}).get("nexus", False):
                logger.info("send_notes_digest_all: skip tg_id=%s (no nexus permission)", tg_id)
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
    """Поиск заметок с пагинацией + дайджест неразобранных."""
    import asyncio as _asyncio
    from core.pagination import register_pages, get_page_text, get_page_keyboard

    if isinstance(data, dict):
        q = (data.get("query") or "").strip()
    else:
        q = (data or "").strip()

    if not q:
        combined = await _repo.list_recent(user_notion_id=user_notion_id, limit=50)
    else:
        tag_results, title_results = await _asyncio.gather(
            _repo.search_by_tag(q, user_notion_id),
            _repo.search_by_title(q, user_notion_id),
        )
        seen_ids: set = set()
        combined = []
        for note in tag_results + title_results:
            if note.id not in seen_ids:
                seen_ids.add(note.id)
                combined.append(note)

    if not combined:
        await message.answer("💡 Заметок не найдено")
        return

    # Считаем неразобранные (старше 7 дней) для дайджест-шапки
    digest_header = ""
    if not q:
        cutoff = (datetime.now(MOSCOW_TZ) - timedelta(days=7)).strftime("%Y-%m-%d")
        old_count = sum(1 for note in combined if note.date and note.date < cutoff)
        if old_count > 0:
            tip = random.choice(_ADHD_TIPS)
            digest_header = f"📬 Не разобрано: {old_count} шт.\n{tip}\n\n"

    def _parse(note) -> dict:
        return {
            "title": note.title,
            "tags": " ".join(note.tags),
            "cat": "",
            "date": note.date,
        }

    def _fmt(it: dict) -> str:
        meta_parts = []
        if it.get("cat"):
            meta_parts.append(it["cat"])
        if it.get("tags"):
            meta_parts.append(it["tags"])
        if it.get("date"):
            meta_parts.append(it["date"])
        meta = " · ".join(meta_parts)
        line = f"<i>💡 {it['title']}"
        if meta:
            line += f" · {meta}"
        line += "</i>"
        return line

    uid = message.from_user.id
    items = [_parse(note) for note in combined]
    header = f"🔍 {q}" if q else "📝 Заметки"
    if digest_header:
        header = digest_header + header
    register_pages(uid, items, header, _fmt)
    await message.answer(get_page_text(uid), reply_markup=get_page_keyboard(uid), parse_mode="HTML")


async def handle_note_delete(message: Message, data: dict, user_notion_id: str = "") -> None:
    """Удалить заметки из последнего дайджеста по ключевому слову."""
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

    deleted = 0
    for p in targets:
        if await _repo.archive(p["page_id"]):
            deleted += 1
        else:
            logger.error("handle_note_delete: archive failed page_id=%s", p["page_id"])

    deleted_ids = {p["page_id"] for p in targets}
    _last_digest_results[uid] = [p for p in digest_pages if p["page_id"] not in deleted_ids]

    hint_display = f" про «{hint}»" if hint else ""
    n = deleted
    suffix = "у" if n == 1 else "и" if n < 5 else ""
    await message.answer(f"🗑️ Удалил {n} заметк{suffix}{hint_display}")
