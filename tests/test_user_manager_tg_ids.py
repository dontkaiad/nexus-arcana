"""tests/test_user_manager_tg_ids.py — get_tg_ids_for_user (#306).

Контекст: Кай делит один user_id между двумя tg_id (#202). Функция нужна,
чтобы рассылать уведомления (напоминания Работ и т.п.) во ВСЕ её чаты, а не
только в тот, откуда пришло действие — см. core/reminder_scheduler.py.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.user_manager import get_tg_ids_for_user


@pytest.mark.asyncio
async def test_empty_user_id_returns_empty_list():
    assert await get_tg_ids_for_user("") == []
    assert await get_tg_ids_for_user(None) == []


@pytest.mark.asyncio
async def test_returns_all_tg_ids_sharing_user_id():
    from core.config import config

    users_by_tg = {
        111: {"user_id": "u-shared"},
        222: {"user_id": "u-shared"},
        333: {"user_id": "u-other"},
    }

    async def fake_get_user(tg_id):
        return users_by_tg.get(tg_id)

    with patch.object(config, "allowed_ids", [111, 222, 333]), \
         patch("core.user_manager.get_user", AsyncMock(side_effect=fake_get_user)):
        result = await get_tg_ids_for_user("u-shared")

    assert sorted(result) == [111, 222]


@pytest.mark.asyncio
async def test_unknown_user_id_returns_empty_list():
    from core.config import config

    async def fake_get_user(tg_id):
        return {"user_id": "someone-else"}

    with patch.object(config, "allowed_ids", [111]), \
         patch("core.user_manager.get_user", AsyncMock(side_effect=fake_get_user)):
        result = await get_tg_ids_for_user("u-does-not-exist")

    assert result == []


@pytest.mark.asyncio
async def test_skips_tg_ids_with_no_user_record():
    from core.config import config

    async def fake_get_user(tg_id):
        return None if tg_id == 111 else {"user_id": "u-shared"}

    with patch.object(config, "allowed_ids", [111, 222]), \
         patch("core.user_manager.get_user", AsyncMock(side_effect=fake_get_user)):
        result = await get_tg_ids_for_user("u-shared")

    assert result == [222]
