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
from nexus.repos.pg_tasks_repo import Task as PgTask


FAKE_TG = 67686090
FAKE_NOTION = "user-notion-id-42"


@pytest.fixture
def client():
    app.dependency_overrides[current_user_id] = lambda: FAKE_TG
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _pg_task(*, title="Зарядка", repeat_select=None, repeat_time="",
             deadline="", reminder="", created="2026-04-01T08:00:00+00:00",
             status="Not started"):
    return PgTask(
        id=f"task-{title}",
        title=title,
        status=status,
        repeat=repeat_select or "Нет",
        repeat_time=repeat_time or "",
        deadline=deadline or "",
        reminder=reminder or "",
        created_at=created or "",
        priority="",
        category="",
        user_notion_id=FAKE_NOTION,
    )


def _patches(tasks):
    return [
        patch("miniapp.backend.routes.calendar._tasks_repo.active",
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
    t = _pg_task(**task_kwargs)
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


# ── _fetch_tasks_in_month: PG-native ─────────────────────────────────────

def test_fetch_tasks_in_month_pg_native_single_query():
    """_fetch_tasks_in_month — PG-native: один вызов .active(), без деdup.

    Бывшая Notion-версия делала 3 параллельных запроса (deadline / reminder /
    recurring) и дедуплицировала по page id. В PG-версии один вызов .active()
    возвращает все задачи юзера; дедуп не нужен — PG не возвращает дублей.
    """
    import asyncio
    tasks = [
        PgTask(id="a", title="A", user_notion_id="u1"),
        PgTask(id="b", title="B", user_notion_id="u1"),
        PgTask(id="c", title="C", user_notion_id="u1"),
    ]
    with patch("miniapp.backend.routes.calendar._tasks_repo.active",
               AsyncMock(return_value=tasks)) as mock_active:
        out = asyncio.get_event_loop().run_until_complete(_fetch_tasks_in_month("u1"))

    mock_active.assert_awaited_once_with("u1")
    assert [t.id for t in out] == ["a", "b", "c"]


def test_recurring_created_at_anchor_fallback(client):
    """Регресс created_at: повторяющаяся задача БЕЗ дедлайна и напоминания
    берёт anchor из created_at и появляется в правильные дни месяца.

    Баг: без anchor задача полностью пропадала из календаря.
    Фикс (calendar.py): anchor = to_local_date(task.created_at, tz_offset).

    Кейс: ежедневная, created=15 апреля → все 31 день мая заняты.
    """
    task = _pg_task(
        title="Утренняя рутина",
        repeat_select="Каждый день",
        created="2026-04-15T08:00:00+00:00",
        deadline="",
        reminder="",
    )
    r = _run(client, [task], month="2026-05")
    assert r.status_code == 200, r.text
    days = r.json()["days"]
    days_with = sorted(int(k) for k, v in days.items() if v["count"] > 0)
    assert days_with == list(range(1, 32)), \
        f"ожидались все 31 день мая, got {days_with}"
