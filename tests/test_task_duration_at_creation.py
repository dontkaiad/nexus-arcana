"""Regression: длительность, указанная ПРИ СОЗДАНИИ задачи ("...длительность
2 часа"), молча терялась — схема classifier.py для type=task не имела поля
duration вообще, а `_do_save_task` никогда не писал `duration_min` (только
последующий `_apply_edit`, отдельной командой). Кай создала задачу с явной
длительностью — карточка подтверждения и итоговое сообщение её не показали,
и в БД `duration_min` остался NULL.

Privacy: синтетические title/uid.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nexus.handlers import tasks as T


@pytest.mark.asyncio
async def test_handle_task_parsed_converts_duration_text_to_minutes():
    """data["duration"]="2 часа" (как отдаёт classifier) → data["duration_min"]=120,
    которое доезжает до pending (карточка "Всё верно?" — есть deadline, нет
    reminder/repeat, значит save случится позже по кнопке "✅ Сохранить")."""
    msg = MagicMock()
    msg.from_user = MagicMock(id=777)
    msg.chat = MagicMock(id=777)
    msg.answer = AsyncMock(return_value=MagicMock(message_id=1))
    data = {
        "title": "встреча с Анубис", "category": "👥 Люди", "priority": "Важно",
        "deadline": "2026-09-15T17:30", "duration": "2 часа", "repeat": "Нет",
    }

    with patch.object(T, "_get_user_tz", AsyncMock(return_value=3)), \
         patch.object(T, "_pending_set") as pset, \
         patch.object(T, "_show_task_confirm", AsyncMock()), \
         patch.object(T, "_has_remind_word", lambda *a, **k: False):
        await T.handle_task_parsed(
            msg, data, original_text="встреча с Анубис завтра в 17:30 длительность 2 часа",
        )

    pset.assert_called_once()
    saved_data = pset.call_args.args[1]
    assert saved_data["duration_min"] == 120


@pytest.mark.asyncio
async def test_handle_task_parsed_no_duration_mentioned_stays_unset():
    msg = MagicMock()
    msg.from_user = MagicMock(id=778)
    msg.chat = MagicMock(id=778)
    msg.answer = AsyncMock(return_value=MagicMock(message_id=1))
    data = {"title": "купить корм", "category": "🐾 Коты", "priority": "Важно",
            "deadline": "2026-09-15", "repeat": "Нет"}

    with patch.object(T, "_get_user_tz", AsyncMock(return_value=3)), \
         patch.object(T, "_pending_set") as pset, \
         patch.object(T, "_show_task_confirm", AsyncMock()), \
         patch.object(T, "_has_remind_word", lambda *a, **k: False):
        await T.handle_task_parsed(msg, data, original_text="купить корм завтра")

    saved_data = pset.call_args.args[1]
    assert "duration_min" not in saved_data or saved_data.get("duration_min") is None


@pytest.mark.asyncio
async def test_show_task_confirm_preview_displays_duration():
    msg = MagicMock()
    msg.chat = MagicMock(id=779)
    msg.bot.edit_message_text = AsyncMock(side_effect=Exception("no msg_id"))
    msg.answer = AsyncMock()
    pending = {"title": "встреча с Анубис", "category": "👥 Люди",
               "priority": "Важно", "deadline": "2026-09-15T17:30",
               "duration_min": 120}

    await T._show_task_confirm(msg, pending, uid=779)

    sent = msg.answer.await_args.args[0]
    assert "Длительность" in sent
    assert "2 ч" in sent


@pytest.mark.asyncio
async def test_do_save_task_writes_duration_min_prop_and_shows_it():
    data = {
        "title": "встреча с Анубис", "priority": "Важно", "category": "👥 Люди",
        "deadline": "2026-09-15T17:30", "duration_min": 120,
        "user_id": "u1", "_tz_offset": 3,
    }
    msg = MagicMock()
    msg.chat = MagicMock(id=780)
    msg.answer = AsyncMock()

    repo = MagicMock()
    repo.create = AsyncMock(return_value="page1")

    with patch.object(T, "_get_user_tz", AsyncMock(return_value=3)), \
         patch("nexus.repos.pg_tasks_repo._ensure_lookups", lambda: None), \
         patch("nexus.repos.pg_tasks_repo._match_code", lambda *a, **k: a[1]), \
         patch.object(T, "_repo", repo), \
         patch.object(T, "_schedule_deadline_check", AsyncMock()), \
         patch.object(T, "last_record_set", lambda *a, **k: None), \
         patch.object(T, "_last_task_set", lambda *a, **k: None):
        await T._do_save_task(msg, data, chat_id=780, uid=780)

    _, props = repo.create.await_args.args
    assert props["Длительность"] == {"number": 120}

    sent = msg.answer.await_args.args[0]
    assert "Длительность" in sent
    assert "2 ч" in sent
