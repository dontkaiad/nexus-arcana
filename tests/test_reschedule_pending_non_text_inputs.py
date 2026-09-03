"""tests/test_reschedule_pending_non_text_inputs.py — reply-бага, ещё раз.

Баг: после WIP/Failed/«Перенести» бот открывает reschedule-pending
(«когда напомнить снова?»). `handle_text` в nexus_bot.py проверял это
pending первым делом — но `handle_voice` (голосовые → Whisper → текст) и
caption-ветка `handle_photo` шли прямиком в `process_text`/classify(),
ни разу не заглянув в reschedule-pending. Ответ голосом или подписью к
фото на «когда напомнить?» создавал НОВУЮ задачу с сырым транскриптом в
заголовке вместо переноса напоминания существующей — параллельная
реализация одной и той же проверки в трёх местах, синхронизированная
только в одном.

Фикс: общая точка входа `tasks.maybe_handle_reschedule_pending()`,
вызывается из `handle_text`, `handle_voice` и caption-ветки `handle_photo`.

Privacy: synthetic uid/task_id, generic task title.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _base_msg(uid: int):
    m = MagicMock()
    m.from_user = MagicMock()
    m.from_user.id = uid
    m.chat = MagicMock()
    m.chat.id = uid
    m.message_id = 1
    m.date = datetime.now(timezone.utc)
    m.answer = AsyncMock(return_value=MagicMock(message_id=2))
    m.bot = AsyncMock()
    return m


@pytest.mark.asyncio
async def test_voice_reply_to_reschedule_prompt_reschedules_not_creates_task():
    """Голосовой ответ на «когда напомнить снова?» → перенос, а не новая задача."""
    from nexus import nexus_bot
    from nexus.handlers import tasks

    uid = 770_101
    tasks._pending_del(uid)
    tasks._pending_set(uid, {"task_id": "t-voice", "action": "reschedule", "title": "полить цветы"})

    msg = _base_msg(uid)
    msg.text = None
    msg.voice = MagicMock()
    msg.audio = None
    msg.bot.get_file = AsyncMock(return_value=MagicMock(file_path="x.ogg"))
    msg.bot.download_file = AsyncMock(return_value=MagicMock(read=lambda: b"fake-audio"))

    classify_mock = AsyncMock(return_value=[{"type": "task", "title": "завтра в 19"}])
    process_item_mock = AsyncMock(return_value="task created")
    # Будущая дата — иначе _is_future_dt (anti-loop guard) отсечёт перенос.
    _future = (datetime.now(timezone.utc).astimezone() + timedelta(days=10)).strftime("%Y-%m-%dT19:00")

    try:
        with patch.object(tasks, "_get_user_tz", AsyncMock(return_value=3)), \
             patch.object(tasks, "ask_claude", AsyncMock(return_value='{"reminder_time": "%s"}' % _future)), \
             patch.object(tasks, "_schedule_reminder", AsyncMock()) as sched, \
             patch.object(tasks, "_update_notion_on_reschedule", AsyncMock()) as notion_update, \
             patch.object(tasks, "react", AsyncMock()), \
             patch("nexus.nexus_bot.classify", classify_mock), \
             patch("nexus.nexus_bot.process_item", process_item_mock), \
             patch("core.voice.transcribe", AsyncMock(return_value="завтра в 19")), \
             patch("core.tg_send.send_long", AsyncMock()), \
             patch("nexus.handlers.utils.react", AsyncMock()):
            await nexus_bot.handle_voice(msg)

        classify_mock.assert_not_awaited()
        process_item_mock.assert_not_awaited()
        sched.assert_awaited_once()
        notion_update.assert_awaited_once()
        # pending очищен после успешного переноса — новая задача не создана
        assert tasks._pending_get(uid) is None
    finally:
        tasks._pending_del(uid)


@pytest.mark.asyncio
async def test_photo_caption_reply_to_reschedule_prompt_reschedules_not_creates_task():
    """Подпись к фото как ответ на «когда напомнить снова?» → перенос."""
    from nexus import nexus_bot
    from nexus.handlers import tasks

    uid = 770_102
    tasks._pending_del(uid)
    tasks._pending_set(uid, {"task_id": "t-photo", "action": "reschedule", "title": "полить цветы"})

    msg = _base_msg(uid)
    msg.text = None
    msg.caption = "завтра в 19"
    msg.photo = [MagicMock(file_id="p1")]
    msg.bot.get_file = AsyncMock(return_value=MagicMock(file_path="x.jpg"))
    msg.bot.download_file = AsyncMock(return_value=MagicMock(read=lambda: b"fake-image"))

    classify_mock = AsyncMock(return_value=[{"type": "task", "title": "завтра в 19"}])
    process_item_mock = AsyncMock(return_value="task created")
    # Будущая дата — иначе _is_future_dt (anti-loop guard) отсечёт перенос.
    _future = (datetime.now(timezone.utc).astimezone() + timedelta(days=10)).strftime("%Y-%m-%dT19:00")

    try:
        with patch.object(tasks, "_get_user_tz", AsyncMock(return_value=3)), \
             patch.object(tasks, "ask_claude", AsyncMock(return_value='{"reminder_time": "%s"}' % _future)), \
             patch.object(tasks, "_schedule_reminder", AsyncMock()) as sched, \
             patch.object(tasks, "_update_notion_on_reschedule", AsyncMock()) as notion_update, \
             patch.object(tasks, "react", AsyncMock()), \
             patch("nexus.nexus_bot.classify", classify_mock), \
             patch("nexus.nexus_bot.process_item", process_item_mock), \
             patch("core.vision.parse_receipt", AsyncMock(return_value=None)), \
             patch("nexus.handlers.utils.react", AsyncMock()):
            await nexus_bot.handle_photo(msg)

        classify_mock.assert_not_awaited()
        process_item_mock.assert_not_awaited()
        sched.assert_awaited_once()
        notion_update.assert_awaited_once()
        assert tasks._pending_get(uid) is None
    finally:
        tasks._pending_del(uid)
