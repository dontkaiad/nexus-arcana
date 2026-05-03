"""core/subtasks_handler.py — общий handler для кнопки «📋 Подзадачи».

Сёстры: Nexus (задачи) и Arcana (работы) одинаково разбиваются на чеклист
из 🗒️ Списки с relation на родительскую задачу/работу. Раньше callback жил
в nexus/handlers/tasks.py и в Arcana не работал. Теперь — общий handler,
оба бота создают свой router через ``make_subtasks_router()``.

Callback data: ``task_subtask_{rel_type}_{id_prefix}``
- rel_type ∈ {"task", "work"} — где искать full id и какое relation писать
- id_prefix — первые ~24 hex-символа page_id (без дефисов)
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

logger = logging.getLogger("core.subtasks_handler")


async def task_subtask_cb(call: CallbackQuery) -> None:
    from core.list_manager import pending_set as list_pending_set
    from core.config import config
    from core.notion_client import db_query

    parts = call.data.split("_", 3)
    rel_type = parts[2] if len(parts) > 2 else "task"
    id_prefix = parts[3] if len(parts) > 3 else ""

    task_name = "Подзадачи"
    if call.message and call.message.text:
        for line in call.message.text.split("\n"):
            if line.startswith("📌"):
                task_name = line.replace("📌", "").strip()
                break

    task_id = id_prefix
    try:
        db_id = (
            config.arcana.db_works if rel_type == "work" else config.nexus.db_tasks
        )
        if db_id and id_prefix:
            pages = await db_query(db_id, page_size=20)
            for page in pages:
                pid = page.get("id", "").replace("-", "")
                if pid.startswith(id_prefix.replace("-", "")):
                    task_id = page["id"]
                    break
    except Exception as e:
        logger.warning("task_subtask: lookup error: %s", e)

    bot_name = "arcana" if rel_type == "work" else "nexus"
    list_pending_set(call.from_user.id, {
        "action": "subtask_items",
        "task_id": task_id,
        "task_name": task_name,
        "rel_type": rel_type,
        "user_notion_id": "",
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
