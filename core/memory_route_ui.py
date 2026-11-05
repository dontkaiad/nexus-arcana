"""core/memory_route_ui.py — диалог маршрута «Запомни …» в боте: тёзки кнопками, «Создать карточку?» [Да/Нет], чипы «кто это».

Состояние лежит в pending_kv (SQLite, переживает рестарт), ключ (tg_id, "mem_route"). Общий для Nexus и Arcana:
каждый бот регистрирует `register(router, bot_label)` — callback-и `mr:*`. Тексты фактов в логах не пишем."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from aiogram import F
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from core import memory as mem
from core import pending_kv
from core.people import RELATIONS

logger = logging.getLogger("core.memory_route_ui")
KIND = "mem_route"
TTL = 3600


def question_text(ask: dict) -> str:
    if ask["kind"] == "choose_person":
        return f"Какой из них — «{ask.get('link') or ''}»?\n🧠 {ask['fact']}"
    return f"Не знаю «{ask.get('name') or ask.get('link') or ''}». Создать карточку?\n🧠 {ask['fact']}"


def keyboard(ask: dict) -> InlineKeyboardMarkup:
    if ask["kind"] == "choose_person":
        rows = [[InlineKeyboardButton(text=c["label"][:60], callback_data=f"mr:p:{c['id']}")] for c in ask["candidates"]]
        rows.append([InlineKeyboardButton(text="📌 В «Разное»", callback_data="mr:no")])
    else:
        rows = [[InlineKeyboardButton(text="✅ Да", callback_data="mr:yes"),
                 InlineKeyboardButton(text="✗ Нет", callback_data="mr:no")]]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def relation_keyboard() -> InlineKeyboardMarkup:
    chips = [InlineKeyboardButton(text=label, callback_data=f"mr:r:{key}") for key, label in RELATIONS.items()]
    rows = [chips[i:i + 3] for i in range(0, len(chips), 3)]
    rows.append([InlineKeyboardButton(text="Пропустить", callback_data="mr:skip")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def ask_in_chat(message: Message, result: dict, user_id: str) -> None:
    """Результат parse_and_store с kind choose_person/ask_person → вопрос с кнопками + pending."""
    ask = {**result["ask"], "user_id": user_id}
    pending_kv.save(message.from_user.id, KIND, ask, ttl=TTL)
    await message.answer(question_text(ask), reply_markup=keyboard(ask))


def _done_text(res: dict, verb: str) -> str:
    if res["kind"] != "memory":
        return "⚠️ Ошибка записи в базу"
    where = f" → {res['person_name']}" if res.get("person_name") else " → Разное"
    return f"🧠 {verb}{where}: {res['fact']}"


async def on_callback(call: CallbackQuery, verb: str = "Запомнил") -> None:
    await call.answer()
    parts = (call.data or "").split(":")
    uid = call.from_user.id
    st = pending_kv.get(uid, KIND, ttl=TTL)
    if not st:
        await call.message.edit_text("⏱ Сессия истекла, скажи «запомни …» ещё раз.")
        return
    action = parts[1] if len(parts) > 1 else ""
    user_id = st.get("user_id", "")
    if action == "r":                                   # чипы «кто это» после создания карточки
        pid, rel = st.get("person_id"), parts[2] if len(parts) > 2 else ""
        if pid and rel in RELATIONS:
            from core.repos import pg_people_repo as prepo
            await asyncio.to_thread(prepo.update_person, user_id, int(pid), {"relation": rel})
        pending_kv.delete(uid, KIND)
        await call.message.edit_text(f"👥 Карточка готова ({RELATIONS.get(rel, '—')}). Допишешь детали в Mini App → Люди.")
        return
    if action == "skip":
        pending_kv.delete(uid, KIND)
        await call.message.edit_text("👥 Карточка создана. Допишешь «кто это» в Mini App → Люди.")
        return
    if action == "p":
        res = await mem.finish_route(user_id, st, {"person_id": int(parts[2])})
    elif action == "yes":
        res = await mem.finish_route(user_id, st, {"create": True})
        if res["kind"] == "memory":
            pending_kv.save(uid, KIND, {**st, "kind": "rel", "person_id": res.get("person_id")}, ttl=TTL)
            await call.message.edit_text(_done_text(res, verb) + "\n\nКто это?", reply_markup=relation_keyboard())
            return
    else:                                               # mr:no → Разное
        res = await mem.finish_route(user_id, st, {"misc": True})
    pending_kv.delete(uid, KIND)
    await call.message.edit_text(_done_text(res, verb))


def register(router, verb: str = "Запомнил") -> None:
    @router.callback_query(F.data.startswith("mr:"))
    async def _cb_mem_route(call: CallbackQuery) -> None:
        await on_callback(call, verb)
