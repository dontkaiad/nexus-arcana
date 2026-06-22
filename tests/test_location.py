"""tests/test_location.py — единый источник правды для локации (#170).

Гарантии:
- resolve_offset: RU / EN-канон / UTC±X / неизвестный город;
- set_user_location пишет ОБА ключа (tz_ + city_) синхронно; offset=None → только city_;
- get_user_tz читает tz_ + TTL-кеш + инвалидация;
- мини-апп set_weather_city теперь обновляет И tz_ (был главный баг);
- текст-путь (_update_user_tz) пишет оба ключа;
- город вне справочника не ломает запись (graceful).

Репо-методы мокаются на границе PgMemoryRepo (PG Postgres-bound, не SQLite —
как в существующих tests/test_weather_resolver.py / test_shared.py).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.location as loc
from core.repos.pg_memory_repo import Memory


# ── resolve_offset ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,exp_offset,exp_city", [
    ("я в спб", 3, "спб"),
    ("Питер", 3, "питер"),
    ("Saint Petersburg", 3, "saint petersburg"),   # англоканон из мини-аппа
    ("Moscow", 3, "moscow"),
    ("Екатеринбург", 5, "екатеринбург"),
    ("UTC+5", 5, None),
    ("utc-3", -3, None),
])
def test_resolve_offset_known(text, exp_offset, exp_city):
    assert loc.resolve_offset(text) == (exp_offset, exp_city)


@pytest.mark.parametrize("text", ["", "я в нарнии", "просто текст без города"])
def test_resolve_offset_unknown(text):
    assert loc.resolve_offset(text) == (None, None)


# ── set_user_location: пишет ОБА поля ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_user_location_writes_both_keys():
    loc.invalidate_tz_cache(77)
    upsert = AsyncMock(return_value=("1", True))
    with patch.object(loc.PgMemoryRepo, "upsert", upsert):
        ret = await loc.set_user_location(77, offset=3, city="спб", user_notion_id="u-1")

    assert ret == 3
    written = {c.kwargs["key"]: c.kwargs["fact"] for c in upsert.call_args_list}
    assert written == {"tz_77": "3", "city_77": "спб"}
    # user_notion_id протянут (нужно weather, который фильтрует по нему)
    assert all(c.kwargs["user_notion_id"] == "u-1" for c in upsert.call_args_list)
    # кеш обновлён в своём процессе
    assert loc._tz_offsets[77] == 3


@pytest.mark.asyncio
async def test_set_user_location_offset_none_writes_only_city():
    loc.invalidate_tz_cache(78)
    upsert = AsyncMock(return_value=("1", True))
    with patch.object(loc.PgMemoryRepo, "upsert", upsert):
        ret = await loc.set_user_location(78, offset=None, city="нарния", user_notion_id="u-2")

    assert ret is None
    written = {c.kwargs["key"]: c.kwargs["fact"] for c in upsert.call_args_list}
    assert written == {"city_78": "нарния"}      # tz_ НЕ трогали
    assert 78 not in loc._tz_offsets             # tz-кеш не тронут


# ── get_user_tz: чтение + TTL + инвалидация ───────────────────────────────────

@pytest.mark.asyncio
async def test_get_user_tz_reads_and_caches():
    loc.invalidate_tz_cache(91)
    find = AsyncMock(return_value=[Memory(id="1", fact="7", key="tz_91")])
    with patch.object(loc.PgMemoryRepo, "find_by_exact_key", find):
        assert await loc.get_user_tz(91) == 7
        assert await loc.get_user_tz(91) == 7      # второй вызов — из TTL-кеша

    find.assert_awaited_once()                     # PG прочитан ровно раз
    assert find.await_args.args[0] == "tz_91"


@pytest.mark.asyncio
async def test_get_user_tz_default_when_absent():
    loc.invalidate_tz_cache(92)
    find = AsyncMock(return_value=[])
    with patch.object(loc.PgMemoryRepo, "find_by_exact_key", find):
        assert await loc.get_user_tz(92) == 3      # дефолт МСК


@pytest.mark.asyncio
async def test_invalidate_forces_reread():
    loc.invalidate_tz_cache(93)
    find = AsyncMock(return_value=[Memory(id="1", fact="4", key="tz_93")])
    with patch.object(loc.PgMemoryRepo, "find_by_exact_key", find):
        assert await loc.get_user_tz(93) == 4
        loc.invalidate_tz_cache(93)                # сброс → перечитать PG
        assert await loc.get_user_tz(93) == 4

    assert find.await_count == 2


# ── Мини-апп set_weather_city: теперь пишет tz_ (главный фикс) ─────────────────

@pytest.mark.asyncio
async def test_set_weather_city_updates_tz_and_city():
    from miniapp.backend.routes import weather

    loc.invalidate_tz_cache(42)
    upsert = AsyncMock(return_value=("1", True))
    with patch.object(loc.PgMemoryRepo, "upsert", upsert), \
         patch.object(weather, "get_user_notion_id", AsyncMock(return_value="notion-x")), \
         patch.object(weather, "sqlite3", MagicMock()):
        res = await weather.set_weather_city(tg_id=42, payload={"city": "Питер"})

    written = {c.kwargs["key"]: c.kwargs["fact"] for c in upsert.call_args_list}
    assert written == {"tz_42": "3", "city_42": "Питер"}   # ОБА поля
    assert res["tz"] == 3 and res["city"] == "Питер"


@pytest.mark.asyncio
async def test_set_weather_city_unknown_city_keeps_tz():
    from miniapp.backend.routes import weather

    loc.invalidate_tz_cache(43)
    upsert = AsyncMock(return_value=("1", True))
    with patch.object(loc.PgMemoryRepo, "upsert", upsert), \
         patch.object(weather, "get_user_notion_id", AsyncMock(return_value="notion-y")), \
         patch.object(weather, "sqlite3", MagicMock()):
        res = await weather.set_weather_city(tg_id=43, payload={"city": "Нарния"})

    written = {c.kwargs["key"]: c.kwargs["fact"] for c in upsert.call_args_list}
    assert written == {"city_43": "Нарния"}        # tz_ не тронут (graceful)
    assert res["tz"] is None


# ── Текст-путь боту (_update_user_tz) пишет ОБА поля ──────────────────────────

@pytest.mark.asyncio
async def test_bot_text_path_writes_both():
    from nexus.handlers import tasks as tasks_mod

    loc.invalidate_tz_cache(555)
    msg = MagicMock()
    msg.from_user.id = 555
    msg.answer = AsyncMock()

    upsert = AsyncMock(return_value=("1", True))
    with patch.object(loc.PgMemoryRepo, "upsert", upsert), \
         patch.object(loc.PgMemoryRepo, "find_by_exact_key", AsyncMock(return_value=[])), \
         patch.object(tasks_mod, "ask_claude",
                      AsyncMock(side_effect=AssertionError("whitelist-город — Claude не нужен"))):
        await tasks_mod._update_user_tz(msg, "я в спб", user_notion_id="u-9")

    written = {c.kwargs["key"]: c.kwargs["fact"] for c in upsert.call_args_list}
    assert written == {"tz_555": "3", "city_555": "спб"}
    assert loc._tz_offsets[555] == 3
    msg.answer.assert_awaited_once_with("🕐 Часовой пояс обновлён: UTC+3")
