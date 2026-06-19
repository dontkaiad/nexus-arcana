"""core/subtasks_handler.py — общий handler для кнопки «📋 Подзадачи».

Сёстры: Nexus (задачи) и Arcana (работы) одинаково разбиваются на чеклист
из 🗒️ Списки с relation на родительскую задачу/работу. Раньше callback жил
в nexus/handlers/tasks.py и в Arcana не работал. Теперь — общий handler,
оба бота создают свой router через ``make_subtasks_router()``.

Callback data: ``task_subtask_{rel_type}_{page_id}``
- rel_type ∈ {"task", "work"} — какое relation писать
- page_id — полный Notion page_id с дефисами (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx).
  Старый формат (усечённый id_prefix без дефисов) поддерживается для compat,
  но если не удаётся разрешить — выдаём ошибку и NOT создаём сироту (fixes #109).
"""
from __future__ import annotations

import logging
import re

from aiogram import F, Router
from aiogram.types import CallbackQuery

logger = logging.getLogger("core.subtasks_handler")

# Полный Notion UUID: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
_FULL_UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE,
)


async def task_subtask_cb(call: CallbackQuery) -> None:
    from core.list_manager import pending_set as list_pending_set
    from core.user_manager import get_user_notion_id

    parts = call.data.split("_", 3)
    rel_type = parts[2] if len(parts) > 2 else "task"
    raw_id = parts[3] if len(parts) > 3 else ""

    task_name = "Подзадачи"
    if call.message and call.message.text:
        for line in call.message.text.split("\n"):
            if line.startswith("📌"):
                task_name = line.replace("📌", "").strip()
                break

    # Resolve full page_id for the relation.
    # New buttons store the full UUID directly. Legacy truncated callbacks used
    # to be resolved by scanning Notion; Notion is gone, so a non-UUID id no
    # longer resolves — NEVER fall through to a partial id (orphan subtasks).
    task_id = None
    if _FULL_UUID_RE.match(raw_id):
        task_id = raw_id
    else:
        logger.error(
            "task_subtask: could not resolve id for raw_id=%r rel=%s",
            raw_id, rel_type,
        )

    if not task_id:
        await call.message.answer(
            "⚠️ Не удалось найти задачу для привязки подзадач. Попробуй ещё раз."
        )
        await call.answer()
        return

    user_notion_id = await get_user_notion_id(call.from_user.id) or ""
    bot_name = "arcana" if rel_type == "work" else "nexus"
    list_pending_set(call.from_user.id, {
        "action": "subtask_items",
        "task_id": task_id,
        "task_name": task_name,
        "rel_type": rel_type,
        "user_notion_id": user_notion_id,
        "bot": bot_name,
    })

    try:
        await call.message.edit_reply_markup()
    except Exception:
        pass
    await call.message.answer(
        f"📋 Разбиваю «{task_name}» на подзадачи\n"
        f"Напиши пункты (каждый с новой строки или через запятую):",
        parse_mode="HTML",
    )
    await call.answer()


def make_subtasks_router() -> Router:
    """Создаёт свежий router instance для конкретного бота.

    Aiogram запрещает подключать один Router к нескольким Dispatcher'ам,
    поэтому Nexus и Arcana должны вызвать factory каждый сам.
    """
    r = Router()
    r.callback_query.register(task_subtask_cb, F.data.startswith("task_subtask_"))
    return r
