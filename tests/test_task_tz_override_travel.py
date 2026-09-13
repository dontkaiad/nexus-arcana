"""Явная зона/город в тексте побеждает сохранённый tz_{uid} для ОДНОГО
разбора (#26x) — Кай путешествует, пишет ДО прилёта про время в целевом
поясе ("13:00 мск"), пока сама физически ещё в другом (UTC+5). Раньше это
молча парсилось по старому сохранённому offset — 13:00 мск превращалось
в 11:00 мск (реальный кейс: задача "забрать воду", note="с 13 до 18",
deadline ушёл на 2 часа раньше целевого).

Privacy: синтетический uid/title, generic текст.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nexus.handlers import tasks as T


@pytest.mark.asyncio
async def test_refinement_deadline_respects_explicit_zone_in_text_over_stored():
    """Хранимый tz=5 (напр. Гай), но в тексте явно "мск" (UTC+3) — Haiku-промпту
    должно уйти "Сейчас: ... (мск)", а не старый offset/захардкоженный "МСК"."""
    pending = {"title": "забрать воду", "category": "💳 Прочее",
               "priority": "Можно потом"}
    uid = 4343
    msg = MagicMock()
    msg.chat = MagicMock(id=uid)
    msg.answer = AsyncMock()

    combined = '{"deadline": "2026-09-14", "reminder_time": "2026-09-14T13:00", "not_refinement": false}'
    seen_system = {}

    async def _fake_ask_claude(text, system="", **kw):
        seen_system["system"] = system
        return combined

    with patch.object(T, "_get_user_tz", AsyncMock(return_value=5)), \
         patch.object(T, "ask_claude", _fake_ask_claude), \
         patch.object(T, "_pending_set") as pset, \
         patch.object(T, "_show_task_confirm", AsyncMock()), \
         patch.object(T, "_parse_relative_time", lambda *a, **k: None):
        await T._handle_task_refinement(
            msg, "с 13 до 18 мск", pending, uid,
        )

    assert "(мск)" in seen_system["system"]
    assert "UTC+5" not in seen_system["system"]
    assert pending["_tz_offset"] == 3  # override победил сохранённый (5)
    pset.assert_called()


@pytest.mark.asyncio
async def test_refinement_deadline_falls_back_to_stored_tz_without_zone_mention():
    """Без явной зоны в тексте — как раньше, используется сохранённый tz."""
    pending = {"title": "забрать воду", "category": "💳 Прочее",
               "priority": "Можно потом"}
    uid = 4344
    msg = MagicMock()
    msg.chat = MagicMock(id=uid)
    msg.answer = AsyncMock()

    combined = '{"deadline": "2026-09-14", "not_refinement": false}'
    with patch.object(T, "_get_user_tz", AsyncMock(return_value=5)), \
         patch.object(T, "ask_claude", AsyncMock(return_value=combined)), \
         patch.object(T, "_pending_set") as pset, \
         patch.object(T, "_show_task_confirm", AsyncMock()), \
         patch.object(T, "_parse_relative_time", lambda *a, **k: None):
        await T._handle_task_refinement(msg, "дедлайн завтра", pending, uid)

    assert pending["_tz_offset"] == 5
    pset.assert_called()


@pytest.mark.asyncio
async def test_do_save_task_uses_pending_tz_override_not_stored():
    """_do_save_task должен использовать data["_tz_offset"], а не заново
    читать сохранённый tz — иначе override из парсинга откатывается на
    последнем шаге (реальный баг: время конвертировалось по СТАРОМУ tz
    при финальном крепеже, даже если парсилось по новому)."""
    data = {
        "title": "забрать воду", "priority": "Можно потом",
        "category": "💳 Прочее", "deadline": "2026-09-14",
        "reminder_time": "2026-09-14T13:00",
        "user_id": "u1", "_tz_offset": 3,
    }
    msg = MagicMock()
    msg.chat = MagicMock(id=555)
    msg.answer = AsyncMock()

    repo = MagicMock()
    repo.create = AsyncMock(return_value="page1")
    repo.set_repeat_fields = AsyncMock()

    with patch.object(T, "_get_user_tz", AsyncMock(return_value=5)) as stored_tz, \
         patch("nexus.repos.pg_tasks_repo._ensure_lookups", lambda: None), \
         patch("nexus.repos.pg_tasks_repo._match_code", lambda *a, **k: a[1]), \
         patch.object(T, "_repo", repo), \
         patch.object(T, "_schedule_reminder", AsyncMock()), \
         patch.object(T, "_schedule_deadline_check", AsyncMock()), \
         patch.object(T, "last_record_set", lambda *a, **k: None), \
         patch.object(T, "_last_task_set", lambda *a, **k: None):
        await T._do_save_task(msg, data, chat_id=555, uid=444)

    stored_tz.assert_not_awaited()  # override уже был в data — повторный fetch не нужен
    _, props = repo.create.await_args.args
    # 13:00 крепится с offset=3 (override), а НЕ с сохранённого 5 —
    # суффикс должен быть +03:00, не +05:00.
    reminder_prop = props["Напоминание"]
    assert "+03:00" in str(reminder_prop)
    assert "+05:00" not in str(reminder_prop)
