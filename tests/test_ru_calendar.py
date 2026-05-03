"""tests/test_ru_calendar.py — производственный календарь РФ через xmlcalendar.ru."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Sample API payload (структура xmlcalendar.ru, май 2026) ─────────────────

_SAMPLE_2026 = {
    "year": 2026,
    "months": [
        {"month": 1, "days": "1,2,3,4,5,6,7,8,9+,10,11,17,18,24,25,31"},
        {"month": 5, "days": "1,2,3,8*,9,10,11+,16,17,23,24,30,31"},
        {"month": 12, "days": "5,6,12,13,19,20,26,27,31+"},
    ],
    "transitions": [
        {"from": "05.09", "to": "05.11"},
    ],
}


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Изолированный SQLite кэш для каждого теста."""
    db_path = tmp_path / "ru_calendar.db"
    sys.modules.pop("core.ru_calendar", None)
    import core.ru_calendar as rc
    monkeypatch.setattr(rc, "_DB_PATH", str(db_path))
    rc._init_db()
    return rc


# ── Парсер day-tokens ───────────────────────────────────────────────────────

def test_parse_day_token_short(fresh_db):
    rc = fresh_db
    assert rc._parse_day_token("8*") == (8, "short")


def test_parse_day_token_transition(fresh_db):
    rc = fresh_db
    assert rc._parse_day_token("11+") == (11, "transition_off")
    assert rc._parse_day_token("9+1") == (9, "transition_off")


def test_parse_day_token_plain(fresh_db):
    rc = fresh_db
    assert rc._parse_day_token("9") == (9, "off")


def test_parse_day_token_garbage(fresh_db):
    rc = fresh_db
    assert rc._parse_day_token("") == (0, "")
    assert rc._parse_day_token("xyz") == (0, "")


# ── Нормализация мая 2026 ──────────────────────────────────────────────────

def test_normalize_may_2026(fresh_db):
    rc = fresh_db
    n = rc.normalize(_SAMPLE_2026)
    assert n["year"] == 2026
    # 1, 9 — праздники с именами; 11 — transition_off
    assert "2026-05-01" in n["holiday_days"]
    assert n["holiday_days"]["2026-05-01"]["kind"] == "holiday"
    assert "Труда" in n["holiday_days"]["2026-05-01"]["name"]
    assert "2026-05-09" in n["holiday_days"]
    assert n["holiday_days"]["2026-05-09"]["name"] == "День Победы"
    assert "2026-05-11" in n["holiday_days"]
    assert n["holiday_days"]["2026-05-11"]["kind"] == "transition_off"
    assert "Перенос" in n["holiday_days"]["2026-05-11"]["name"]
    # 8 — сокращённый, имя содержит «канун»
    assert "2026-05-08" in n["short_days"]
    assert "победы" in n["short_days"]["2026-05-08"]["name"].lower() or \
           "канун" in n["short_days"]["2026-05-08"]["name"].lower()
    # Sat/Sun мая (2,3,9,10,16,17,23,24,30,31) все в days → working_weekends пуст
    may_working = [iso for iso in n["working_weekends"] if iso.startswith("2026-05")]
    assert may_working == []


# ── get_month_info ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_month_info_may_2026_via_remote(fresh_db):
    rc = fresh_db
    with patch.object(rc, "_fetch_remote", AsyncMock(return_value=_SAMPLE_2026)):
        info = await rc.get_month_info(2026, 5)
    # Только федеральные праздники + переносы. Sat/Sun — фронт красит по dayofweek.
    assert info["holiday_days"] == [1, 9, 11]
    assert info["short_days"] == [8]
    assert info["working_weekends"] == []
    by_day = {h["day"]: h for h in info["holidays_info"]}
    assert by_day[1]["kind"] == "holiday"
    assert by_day[8]["kind"] == "short"
    assert by_day[11]["kind"] == "transition_off"
    # Имена есть для известных праздников
    assert "Труда" in by_day[1]["name"]
    assert "Победы" in by_day[9]["name"]


# ── Кэш ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cache_hit_skips_remote(fresh_db):
    rc = fresh_db
    fetch_mock = AsyncMock(return_value=_SAMPLE_2026)
    with patch.object(rc, "_fetch_remote", fetch_mock):
        await rc.get_month_info(2026, 5)
        await rc.get_month_info(2026, 5)
        await rc.get_month_info(2026, 1)
    # Только один HTTP call за весь тест (год кэшируется целиком)
    assert fetch_mock.await_count == 1


# ── Fallback ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fallback_to_stale_cache_when_api_fails(fresh_db):
    rc = fresh_db
    # Положили кэш как «протухший» (более 30 дней назад).
    rc._cache_put(2026, _SAMPLE_2026)
    import sqlite3
    old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    con = sqlite3.connect(rc._DB_PATH)
    con.execute("UPDATE ru_calendar_cache SET fetched_at = ? WHERE year = ?", (old, 2026))
    con.commit(); con.close()

    with patch.object(rc, "_fetch_remote", AsyncMock(return_value=None)):
        info = await rc.get_month_info(2026, 5)
    # Stale-кэш всё ещё разворачивается корректно.
    assert 1 in info["holiday_days"]
    assert 9 in info["holiday_days"]
    assert info["short_days"] == [8]


@pytest.mark.asyncio
async def test_fallback_to_base_when_no_api_no_cache(fresh_db):
    rc = fresh_db
    with patch.object(rc, "_fetch_remote", AsyncMock(return_value=None)):
        info = await rc.get_month_info(2026, 5)
    # base — без переносов и без сокращённых.
    assert 1 in info["holiday_days"]
    assert 9 in info["holiday_days"]
    assert 11 not in info["holiday_days"]
    assert info["short_days"] == []


# ── transitions parsing ────────────────────────────────────────────────────

def test_transitions_parse_to_iso_md(fresh_db):
    rc = fresh_db
    out = rc._parse_transitions([{"from": "05.09", "to": "05.11"}])
    assert out == {"05-11": "Перенос с 9 мая"}
