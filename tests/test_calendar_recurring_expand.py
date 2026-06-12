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

@pytest.mark.parametrize("repeat_time,select,expected", [
    # «Время повтора» every_Nd — приоритет над select
    pytest.param("21:00|every_2d", "Каждый день", 2, id="every-nd-priority-over-select"),
    # канонические select-значения
    pytest.param("", "Каждый день", 1, id="select-daily"),
    pytest.param("", "Каждую неделю", 7, id="select-weekly"),
    pytest.param("", "Каждый месяц", {"kind": "monthly"}, id="select-monthly"),
    # пусто / незнакомое → None
    pytest.param("", "", None, id="empty-select-none"),
    pytest.param("", "Что-то незнакомое", None, id="unknown-select-none"),
    # case-insensitive синонимы (RU + EN, пробелы)
    pytest.param("", "Ежедневно", 1, id="syn-ezhednevno"),
    pytest.param("", "ЕЖЕДНЕВНО", 1, id="syn-ezhednevno-upper"),
    pytest.param("", "  Ежедневно  ", 1, id="syn-ezhednevno-spaces"),
    pytest.param("", "ежедневно", 1, id="syn-ezhednevno-lower"),
    pytest.param("", "Через день", 2, id="syn-cherez-den"),
    pytest.param("", "Еженедельно", 7, id="syn-ezhenedelno"),
    pytest.param("", "Раз в две недели", 14, id="syn-raz-v-dve-nedeli"),
    pytest.param("", "Ежемесячно", {"kind": "monthly"}, id="syn-ezhemesyachno"),
    pytest.param("", "Daily", 1, id="syn-daily-en"),
    pytest.param("", "WEEKLY", 7, id="syn-weekly-en-upper"),
    pytest.param("", "Monthly", {"kind": "monthly"}, id="syn-monthly-en"),
])
def test_resolve_interval(repeat_time, select, expected):
    """_resolve_interval: every_Nd приоритет → select-маппинг → синонимы → None."""
    assert _resolve_interval(repeat_time, select) == expected


# ── /api/calendar (интеграция) ────────────────────────────────────────────

@pytest.mark.parametrize("task_kwargs,expected_days,title_substr", [
    # Повтор=Каждый день, нет ни Дедлайна ни Напоминания → anchor=created_time,
    # май 2026 — 31 день, все заполнены.
    pytest.param(
        dict(repeat_select="Каждый день", created="2026-04-15T08:00:00.000Z"),
        list(range(1, 32)), None,
        id="daily-expanded-from-created-time"),
    # Повтор=Каждую неделю, Напоминание = Пн 4 мая 2026 → 4, 11, 18, 25.
    pytest.param(
        dict(repeat_select="Каждую неделю", reminder="2026-05-04T10:00:00.000Z"),
        [4, 11, 18, 25], None,
        id="weekly-anchored-to-reminder"),
    # Повтор=Каждый месяц, anchor.day = 15 → одна occurrence в мае: 15.
    pytest.param(
        dict(repeat_select="Каждый месяц", reminder="2026-04-15T10:00:00.000Z"),
        [15], None,
        id="monthly-one-occurrence"),
    # Время повтора=21:00|every_2d — приоритет над select (Каждый день
    # игнорируется), шаг 2 от Напоминания 1 мая (12:00 UTC = 15:00 MSK).
    pytest.param(
        dict(repeat_select="Каждый день", repeat_time="21:00|every_2d",
             reminder="2026-05-01T12:00:00.000Z"),
        [1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21, 23, 25, 27, 29, 31], None,
        id="every-nd-priority-over-select"),
    # Реальные данные «менять лоток котам»: Повтор=Ежедневно,
    # Время повтора=16:00|every_2d, Напоминание=2026-05-04 (20:00 UTC =
    # 23:00 MSK = May 4), без Дедлайна. Шаг 2 от 4 мая → [2, 4, ..., 30].
    pytest.param(
        dict(title="менять лоток котам", repeat_select="Ежедневно",
             repeat_time="16:00|every_2d", reminder="2026-05-04T20:00:00.000Z"),
        [2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30], "лоток",
        id="real-case-kotlitter"),
    # Recurring без Напоминания и без Дедлайна, без every_Nd → fallback в
    # select-маппинг, anchor=created_time (apr 15 → май занимает все 31 день).
    pytest.param(
        dict(repeat_select="Ежедневно", repeat_time="",
             reminder=None, deadline=None, created="2026-04-15T10:00:00.000Z"),
        list(range(1, 32)), None,
        id="anchor-fallback-to-created-time"),
])
def test_recurring_expand(client, task_kwargs, expected_days, title_substr):
    """/api/calendar: recurring задача разворачивается ровно в ожидаемые дни месяца.

    Одна задача → каждый занятый день имеет count == 1. Если задан
    title_substr — title задачи присутствует в occurrences.
    """
    t = _task(**task_kwargs)
    r = _run(client, [t], month="2026-05")
    assert r.status_code == 200, r.text
    days = r.json()["days"]
    days_with = sorted(int(k) for k, v in days.items() if v["count"] > 0)
    assert days_with == expected_days
    counts = {int(k): v["count"] for k, v in days.items()}
    assert all(counts[d] == 1 for d in expected_days)
    if title_substr:
        assert any(title_substr in (task["title"] or "").lower()
                   for d in days.values() for task in d["tasks"])


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
