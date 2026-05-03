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
from miniapp.backend.routes.calendar import _resolve_interval


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
