"""nexus/handlers/delete.py — generic /delete (PG soft-archive)."""
from __future__ import annotations

import logging
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram import Router, F
from core.deleter import (
    parse_delete_intent, select_records, archive_records, format_record,
    GATED_DOMAINS,
)

router = Router()
logger = logging.getLogger("nexus.delete")

# target (domain) → human label
NEXUS_DOMAINS = {
    "finance": "💰 Финансы",
    "tasks":   "✅ Задачи",
    "notes":   "💡 Заметки",
}

PARSE_TARGET_SYSTEM = """Определи что удаляем в боте Nexus. Ответь ТОЛЬКО одним словом:
finance  — финансы, расходы, доходы, операции
tasks    — задачи
notes    — заметки
unknown  — непонятно"""

# uid → (domain, [ids], scope) — domain хранится ВМЕСТЕ с ids (проверка на confirm)
_pending: dict[int, tuple] = {}


async def handle_delete(message: Message, text: str, user_notion_id: str = "") -> None:
    from core.claude_client import ask_claude

    # fail-closed: без пользователя ничего не выбираем/не удаляем
    if not user_notion_id:
        await message.answer("⚠️ Не могу определить пользователя — удаление отменено.")
        return

    target = (await ask_claude(text, system=PARSE_TARGET_SYSTEM, max_tokens=10, temperature=0)).strip().lower()
    if target not in NEXUS_DOMAINS:
        await message.answer("⚠️ Уточни что удалить: финансы, задачи или заметки.")
        return

    if target in GATED_DOMAINS:
        await message.answer("🚫 Удаление финансов пока недоступно.")
        return

    label = NEXUS_DOMAINS[target]
    intent = await parse_delete_intent(text)
    scope = intent["scope"]

    records = await select_records(
        target, scope,
        date=intent.get("date"), month=intent.get("month"),
        count=int(intent.get("count") or 1),
        user_notion_id=user_notion_id,
    )
    if not records:
        await message.answer("📭 Записей не найдено.")
        return

    previews = [format_record(r) for r in records[:10]]
    preview_text = "\n".join(f"• {p}" for p in previews if p)
    if len(records) > 10:
        preview_text += f"\n... и ещё {len(records) - 10}"

    uid = message.from_user.id
    _pending[uid] = (target, [r["id"] for r in records], scope)

    from core.utils import cancel_button
    confirm_cb = f"del_confirm_nexus:{target}"
    if scope == "all":
        prompt = f"⚠️ Удалить ВСЕ {len(records)} записей · {label}?\n\n{preview_text}"
        btn = f"⚠️ Да, удалить ВСЕ ({len(records)})"
    else:
        prompt = f"{label} — найдено {len(records)} записей:\n\n{preview_text}\n\nУдалить?"
        btn = f"🗑 Да, удалить ({len(records)})"
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        cancel_button(btn, confirm_cb),
        InlineKeyboardButton(text="❌ Отмена", callback_data="del_cancel"),
    ]])
    await message.answer(prompt, reply_markup=kb)


@router.callback_query(F.data.startswith("del_confirm_nexus"))
async def confirm_delete(call: CallbackQuery) -> None:
    uid = call.from_user.id
    pending = _pending.pop(uid, None)
    if not pending:
        await call.answer("Ничего не найдено.")
        return
    domain, ids, _scope = pending
    # guard: домен в кнопке должен совпасть с тем, что в _pending (защита от
    # рассинхрона при concurrent/stale pending — иначе удалили бы не тот домен)
    cb_domain = call.data.split(":", 1)[1] if ":" in call.data else ""
    if cb_domain != domain:
        await call.message.edit_text("⚠️ Сессия устарела — повтори /delete.")
        await call.answer()
        return

    deleted = await archive_records(domain, ids)

    # tasks: снять scheduler-джобы напоминаний/дедлайнов
    if domain == "tasks":
        try:
            from nexus.handlers.tasks import _remove_task_jobs
            for tid in ids:
                _remove_task_jobs(tid)
        except Exception as e:
            logger.warning("delete: remove task jobs failed: %s", e)

    await call.message.edit_text(f"✅ Удалено (в архив): {deleted}")
    await call.answer()


@router.callback_query(F.data == "del_cancel")
async def cancel_delete(call: CallbackQuery) -> None:
    _pending.pop(call.from_user.id, None)
    await call.message.edit_text("❌ Отмена. Ничего не удалено.")
    await call.answer()
