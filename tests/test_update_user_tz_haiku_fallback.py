"""Regression: when core.location.resolve_offset can't match a city (unlisted,
or a case-declined form the dict doesn't have — see test_location_declension.py),
_update_user_tz fell back to asking Haiku for JUST a numeric offset. The offset
came back correct, but `matched_city` stayed None, so set_user_location never
wrote city_{tg_id} — the weather widget kept the stale city even though the tz
had genuinely changed.

Fix: the Haiku fallback now returns {offset, city, confident} in one call, so
an unlisted/niche city updates both tz_ and city_ in sync (per ADR-0016), and
a location Haiku itself can't resolve confidently triggers a clarification
reply instead of silently defaulting.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.location as loc


def _msg(uid=555):
    msg = MagicMock()
    msg.from_user.id = uid
    msg.chat.id = uid
    msg.answer = AsyncMock()
    return msg


@pytest.mark.asyncio
async def test_niche_city_syncs_both_tz_and_city_via_haiku():
    from nexus.handlers import tasks as tasks_mod

    loc.invalidate_tz_cache(555)
    msg = _msg(555)
    upsert = AsyncMock(return_value=("1", True))
    haiku = AsyncMock(return_value='{"offset":8,"city":"Ulaanbaatar","confident":true}')

    with patch.object(loc.PgMemoryRepo, "upsert", upsert), \
         patch.object(loc.PgMemoryRepo, "find_by_exact_key", AsyncMock(return_value=[])), \
         patch.object(loc, "_invalidate_weather_cache"), \
         patch.object(tasks_mod, "ask_claude", haiku):
        await tasks_mod._update_user_tz(msg, "я в улан-баторе", user_notion_id="u-9")

    written = {c.kwargs["key"]: c.kwargs["fact"] for c in upsert.call_args_list}
    assert written == {"tz_555": "8", "city_555": "Ulaanbaatar"}
    msg.answer.assert_awaited_once_with("🕐 Часовой пояс обновлён: UTC+8")


@pytest.mark.asyncio
async def test_unclear_location_asks_for_clarification_and_writes_nothing():
    from nexus.handlers import tasks as tasks_mod

    loc.invalidate_tz_cache(556)
    msg = _msg(556)
    upsert = AsyncMock(return_value=("1", True))
    haiku = AsyncMock(return_value='{"offset":null,"city":null,"confident":false}')

    with patch.object(loc.PgMemoryRepo, "upsert", upsert), \
         patch.object(loc.PgMemoryRepo, "find_by_exact_key", AsyncMock(return_value=[])), \
         patch.object(loc, "_invalidate_weather_cache"), \
         patch.object(tasks_mod, "ask_claude", haiku):
        await tasks_mod._update_user_tz(msg, "я где-то там", user_notion_id="u-9")

    upsert.assert_not_awaited()
    msg.answer.assert_awaited_once()
    assert "не поняла" in msg.answer.await_args.args[0].lower()


@pytest.mark.asyncio
async def test_haiku_infra_failure_degrades_gracefully_not_as_unclear():
    """A broken/unreachable Haiku call is an infra problem, not 'I don't
    understand your location' — must fall back to the old UTC+3 default,
    not surface the clarification message."""
    from nexus.handlers import tasks as tasks_mod

    loc.invalidate_tz_cache(557)
    msg = _msg(557)
    upsert = AsyncMock(return_value=("1", True))
    haiku = AsyncMock(side_effect=RuntimeError("network down"))

    with patch.object(loc.PgMemoryRepo, "upsert", upsert), \
         patch.object(loc.PgMemoryRepo, "find_by_exact_key", AsyncMock(return_value=[])), \
         patch.object(loc, "_invalidate_weather_cache"), \
         patch.object(tasks_mod, "ask_claude", haiku):
        await tasks_mod._update_user_tz(msg, "я в нарнии", user_notion_id="u-9")

    written = {c.kwargs["key"]: c.kwargs["fact"] for c in upsert.call_args_list}
    assert written == {"tz_557": "3"}   # старый дефолт МСК, city_ не трогаем (matched_city=None)
    msg.answer.assert_awaited_once_with("🕐 Часовой пояс обновлён: UTC+3")
