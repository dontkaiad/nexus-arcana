"""issue #70 follow-up: при «я в Алании» Claude Haiku возвращал UTC+2
(устарел — Турция UTC+3 c 2016). Whitelist `_CITY_TZ` должен матчить
турецкие/закавказские/израильские города ДО fallback на Claude.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.parametrize("text,expected_tz", [
    ("я в Алании", 3),
    ("в Аланьи", 3),
    ("сейчас в Анталье", 3),
    ("я в Турции", 3),
    ("Стамбул", 3),
    ("Батуми", 4),
    ("я на Кипре, Ларнака", 2),
    ("Тель-Авив", 2),
])
@pytest.mark.asyncio
async def test_city_whitelist_resolves_tz_without_claude(text, expected_tz):
    """Whitelist срабатывает раньше Claude — ask_claude НЕ должен звониться."""
    from nexus.handlers import tasks as tasks_mod

    msg = MagicMock()
    msg.from_user.id = 999
    msg.answer = AsyncMock()

    ask = AsyncMock(side_effect=AssertionError("Claude не должен звониться для whitelist-города"))
    memset = AsyncMock()

    with patch.object(tasks_mod, "ask_claude", ask), \
         patch("core.notion_client.memory_set", memset):
        await tasks_mod._update_user_tz(msg, text)

    assert tasks_mod._user_tz_offset[999] == expected_tz
    sign = "+" if expected_tz >= 0 else ""
    msg.answer.assert_awaited_once_with(f"🕐 Часовой пояс обновлён: UTC{sign}{expected_tz}")
