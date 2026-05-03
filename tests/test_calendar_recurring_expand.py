"""tests/test_calendar_recurring_expand.py — _resolve_interval + recurring expand.

Кейсы:
1. Повтор=«Каждый день», anchor=created_time → все дни месяца от anchor.
2. Повтор=«Каждую неделю», anchor=Напоминание (Пн) → 4-5 occurrences.
3. Повтор=«Каждый месяц», anchor.day=15 → ровно одна occurrence.
4. «Время повтора» = '21:00|every_2d' → приоритет, шаг 2.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id
from miniapp.backend.routes.calendar import (
    _fetch_tasks_in_month,
    _resolve_interval,
)


FAKE_TG = 67686090
FAKE_NOTION = "user-notion-id-42"


@pytest.fixture
def client():
    app.dependency_overrides[current_user_id] = lambda: FAKE_TG
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _task(*, title="Зарядка", repeat_select=None, repeat_time="",
          deadline=None, reminder=None, created="2026-04-01T08:00:00.000Z",
          status="Not started"):
    return {
        "id": f"task-{title}",
        "created_time": created,
        "properties": {
            "Задача": {"title": [{"plain_text": title}]},
            "Статус": {"status": {"name": status}},
            "Приоритет": {"select": None},
            "Категория": {"select": None},
            "Повтор": {"select": {"name": repeat_select}} if repeat_select else {"select": None},
            "Время повтора": {"rich_text": [{"plain_text": repeat_time}] if repeat_time else []},
            "Дедлайн": {"date": {"start": deadline}} if deadline else {"date": None},
            "Напоминание": {"date": {"start": reminder}} if reminder else {"date": None},
            "🪪 Пользователи": {"relation": [{"id": FAKE_NOTION}]},
        },
    }


def _patches(tasks):
    return [
        patch("miniapp.backend.routes.calendar.query_pages",
              AsyncMock(return_value=tasks)),
        patch("miniapp.backend.routes.calendar.get_user_notion_id",
              AsyncMock(return_value=FAKE_NOTION)),
        patch("miniapp.backend._helpers.get_user_tz",
              AsyncMock(return_value=3)),
    ]


def _run(client, tasks, month="2026-05"):
    pp = _patches(tasks)
    for p in pp:
        p.start()
    try:
        return client.get(f"/api/calendar?month={month}")
    finally:
        for p in pp:
            p.stop()


# ── _resolve_interval (юнит) ──────────────────────────────────────────────

def test_resolve_interval_every_nd_priority():
    assert _resolve_interval("21:00|every_2d", "Каждый день") == 2


def test_resolve_interval_select_daily():
    assert _resolve_interval("", "Каждый день") == 1


def test_resolve_interval_select_weekly():
    assert _resolve_interval("", "Каждую неделю") == 7


def test_resolve_interval_select_monthly():
    assert _resolve_interval("", "Каждый месяц") == {"kind": "monthly"}


def test_resolve_interval_none():
    assert _resolve_interval("", "") is None
    assert _resolve_interval("", "Что-то незнакомое") is None


@pytest.mark.parametrize("raw,expected", [
    ("Ежедневно", 1),
    ("ЕЖЕДНЕВНО", 1),
    ("  Ежедневно  ", 1),
    ("ежедневно", 1),
    ("Через день", 2),
    ("Еженедельно", 7),
    ("Раз в две недели", 14),
    ("Ежемесячно", {"kind": "monthly"}),
    ("Daily", 1),
    ("WEEKLY", 7),
    ("Monthly", {"kind": "monthly"}),
])
def test_resolve_interval_case_insensitive_synonyms(raw, expected):
    assert _resolve_interval("", raw) == expected


# ── /api/calendar (интеграция) ────────────────────────────────────────────

def test_daily_repeat_expanded_from_created_time(client):
    """Повтор=Каждый день, нет ни Дедлайна ни Напоминания → anchor=created_time."""
    t = _task(repeat_select="Каждый день", created="2026-04-15T08:00:00.000Z")
    r = _run(client, [t], month="2026-05")
    assert r.status_code == 200, r.text
    days = r.json()["days"]
    counts = {int(k): v["count"] for k, v in days.items()}
    # май 2026 — 31 день, все заполнены
    assert all(counts[d] == 1 for d in range(1, 32))


def test_weekly_repeat_anchored_to_reminder(client):
    """Повтор=Каждую неделю, Напоминание = Пн 4 мая 2026 → 4, 11, 18, 25."""
    t = _task(repeat_select="Каждую неделю", reminder="2026-05-04T10:00:00.000Z")
    r = _run(client, [t], month="2026-05")
    assert r.status_code == 200, r.text
    days = r.json()["days"]
    days_with = sorted(int(k) for k, v in days.items() if v["count"] > 0)
    assert days_with == [4, 11, 18, 25]


def test_monthly_repeat_one_occurrence(client):
    """Повтор=Каждый месяц, anchor.day = 15 → одна occurrence в мае: 15."""
    t = _task(repeat_select="Каждый месяц", reminder="2026-04-15T10:00:00.000Z")
    r = _run(client, [t], month="2026-05")
    assert r.status_code == 200, r.text
    days = r.json()["days"]
    days_with = [int(k) for k, v in days.items() if v["count"] > 0]
    assert days_with == [15]


def test_every_nd_priority_over_select(client):
    """Время повтора=21:00|every_2d — приоритет, шаг 2 от Напоминания 1 мая."""
    t = _task(
        repeat_select="Каждый день",                # должен быть проигнорирован
        repeat_time="21:00|every_2d",
        reminder="2026-05-01T12:00:00.000Z",  # 15:00 MSK = May 1
    )
    r = _run(client, [t], month="2026-05")
    assert r.status_code == 200, r.text
    days = r.json()["days"]
    days_with = sorted(int(k) for k, v in days.items() if v["count"] > 0)
    assert days_with == [1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21, 23, 25, 27, 29, 31]


def test_real_case_kotlitter(client):
    """Реальные данные «менять лоток котам»:
    Повтор=Ежедневно, Время повтора=16:00|every_2d, Напоминание=2026-05-04, без Дедлайна.
    Ожидаем шаг 2 от 4 мая, occurrences = [2, 4, ..., 30]."""
    t = _task(
        title="менять лоток котам",
        repeat_select="Ежедневно",
        repeat_time="16:00|every_2d",
        reminder="2026-05-04T20:00:00.000Z",  # 23:00 MSK = May 4
    )
    r = _run(client, [t], month="2026-05")
    assert r.status_code == 200, r.text
    days = r.json()["days"]
    days_with = sorted(int(k) for k, v in days.items() if v["count"] > 0)
    assert days_with == [2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30]
    # Title тоже на месте
    assert any("лоток" in (t["title"] or "").lower()
               for d in days.values() for t in d["tasks"])


def test_anchor_fallback_to_created_time(client):
    """Recurring без Напоминания и без Дедлайна → anchor=created_time."""
    t = _task(
        repeat_select="Ежедневно",
        repeat_time="",       # без every_Nd → fallback в select-маппинг
        reminder=None,
        deadline=None,
        created="2026-04-15T10:00:00.000Z",  # apr 15 → may занимает все 31 день
    )
    r = _run(client, [t], month="2026-05")
    assert r.status_code == 200, r.text
    days = r.json()["days"]
    counts = {int(k): v["count"] for k, v in days.items()}
    assert all(counts[d] == 1 for d in range(1, 32))


# ── _fetch_tasks_in_month: 3-query merge + dedup ──────────────────────────

def test_fetch_tasks_in_month_merges_three_queries_and_dedups():
    """Регрессия бага 3-level nesting: фильтр разбит на 3 параллельных query
    с merge по page id (без дублей)."""
    page_a = {"id": "page-a", "properties": {}}
    page_b = {"id": "page-b", "properties": {}}
    page_c = {"id": "page-c", "properties": {}}
    # page_a в двух query (Дедлайн + Напоминание) — должна остаться один раз.
    deadline_pages = [page_a]
    reminder_pages = [page_a, page_b]
    recurring_pages = [page_c]

    call_log = []
    async def _fake(db_id, filters=None, **kwargs):
        call_log.append(filters)
        # порядок вызовов: deadline → reminder → recurring
        idx = len(call_log) - 1
        return [deadline_pages, reminder_pages, recurring_pages][idx]

    with patch("miniapp.backend.routes.calendar.query_pages",
               new=AsyncMock(side_effect=_fake)):
        import asyncio
        out = asyncio.get_event_loop().run_until_complete(
            _fetch_tasks_in_month("user-x", "2026-05-01", "2026-05-31")
        )
    ids = sorted(p["id"] for p in out)
    assert ids == ["page-a", "page-b", "page-c"]
    # Каждый из 3 фильтров — простой `and` БЕЗ вложенного `or`.
    for f in call_log:
        assert "and" in f
        for item in f["and"]:
            assert "or" not in item, f"nested or found: {item}"
            # Не должно быть второго `and` (3-level nesting).
            assert "and" not in item, f"nested and found: {item}"
