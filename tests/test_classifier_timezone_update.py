"""tests/test_classifier_timezone_update.py — regression: process_item больше
не дублирует запись в память для type=timezone_update (#184).

Раньше process_item ЗВАЛ ОБА: _update_user_tz (который сам пишет город/пояс
через set_user_location) И отдельный save_memory(original_text) — второй
вызов заново парсил тот же сырой текст независимым Haiku-экстрактором без
контекста «это про локацию» и писал второй, несогласованный факт.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from core.classifier import process_item


def _msg():
    msg = MagicMock()
    msg.from_user.id = 1
    msg.chat.id = 1
    msg.answer = AsyncMock()
    return msg


def test_timezone_update_calls_only_update_user_tz():
    msg = _msg()
    with patch("nexus.handlers.tasks._update_user_tz", AsyncMock()) as upd_tz, \
         patch("core.memory.save_memory", AsyncMock()) as save_mem:
        result = asyncio.run(
            process_item({"type": "timezone_update", "text": "я в гае"}, "я в гае", msg, {})
        )
    upd_tz.assert_called_once()
    save_mem.assert_not_called()  # регрессия: раньше вызывался и дублировал запись
    assert result == ""
