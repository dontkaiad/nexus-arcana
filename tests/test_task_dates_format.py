"""tests/test_task_dates_format.py — единый формат дат задачи (issue #169).

Дедлайн (⏰) и напоминание (🔔) — две независимые даты; /tasks и дейли-пинг
разводят их визуально через ОДИН общий хелпер `_format_task_dates`.

Покрытие:
- только дедлайн / только напоминание / оба разных дня / date-only / repeat;
- оба UI (`cmd_tasks` и `_build_today_digest`) зовут общий хелпер (один источник).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import nexus.handlers.tasks as tasks_mod
from nexus.handlers.tasks import _format_task_dates
from nexus.repos.pg_tasks_repo import Task


# ── Формат самого хелпера ─────────────────────────────────────────────────────

def test_deadline_only():
    out = _format_task_dates("2026-06-26T09:00", "", False, "")
    assert out == "⏰ 26.06 09:00"
    assert "🔔" not in out


def test_reminder_only():
    out = _format_task_dates("", "2026-06-23T19:30", False, "")
    assert out == "🔔 23.06 19:30"
    assert "⏰" not in out


def test_both_different_days():
    # дедлайн 26.06, напомнить 23.06 — обе части с правильными датами
    out = _format_task_dates("2026-06-26T10:00", "2026-06-23T19:00", False, "")
    assert out == "⏰ 26.06 10:00 · 🔔 23.06 19:00"


def test_deadline_date_only_no_time():
    out = _format_task_dates("2026-06-26", "", False, "")
    assert out == "⏰ 26.06"
    assert ":" not in out


def test_repeat_shows_label_no_dates():
    out = _format_task_dates("2026-06-26T10:00", "2026-06-23T19:00", True, "ежедневно")
    assert out == "🔄 ежедневно"
    assert "⏰" not in out and "🔔" not in out and "26.06" not in out


def test_empty_when_no_dates():
    assert _format_task_dates("", "", False, "") == ""


# ── Оба UI зовут общий хелпер (единый источник) ───────────────────────────────

@pytest.mark.asyncio
async def test_cmd_tasks_uses_shared_formatter(mock_message, monkeypatch):
    """`/tasks` строит строку дат через общий `_format_task_dates`."""
    from nexus.nexus_bot import cmd_tasks

    calls = []

    def _spy(deadline_raw, reminder_raw, is_repeat=False, repeat_label=""):
        calls.append((deadline_raw, reminder_raw, is_repeat, repeat_label))
        return "FMT"

    monkeypatch.setattr(tasks_mod, "_format_task_dates", _spy)

    tasks = [Task(id="1", title="X", priority="🟡 Важно",
                  category="🏥 Здоровье", deadline=date.today().isoformat(),
                  repeat="Нет")]
    msg = mock_message("/tasks")
    with patch("nexus.repos.tasks_repo._repo.active",
               AsyncMock(return_value=tasks)), \
         patch.object(tasks_mod, "_get_user_tz", AsyncMock(return_value=3)), \
         patch("nexus.handlers.streaks.get_streak",
               MagicMock(return_value=None)):
        await cmd_tasks(msg, user_notion_id="u-1")

    assert calls, "cmd_tasks не вызвал общий _format_task_dates"
    out = "\n".join(str(c.args[0]) for c in msg.answer.call_args_list)
    assert "FMT" in out


@pytest.mark.asyncio
async def test_daily_ping_uses_shared_formatter(monkeypatch):
    """Дейли-пинг (`_build_today_digest` → `_fmt`) зовёт тот же хелпер."""
    calls = []

    def _spy(deadline_raw, reminder_raw, is_repeat=False, repeat_label=""):
        calls.append((deadline_raw, reminder_raw, is_repeat, repeat_label))
        return "FMT"

    monkeypatch.setattr(tasks_mod, "_format_task_dates", _spy)

    today_msk = datetime.now(timezone(timedelta(hours=3))).strftime("%Y-%m-%d")
    task = Task(id="t1", title="X", priority="🟡 Важно",
                category="🏥 Здоровье", repeat="Нет",
                reminder=f"{today_msk}T16:00:00+00:00")
    with patch.object(tasks_mod, "_get_user_tz", AsyncMock(return_value=3)), \
         patch.object(tasks_mod._repo, "active", AsyncMock(return_value=[task])), \
         patch.object(tasks_mod, "ask_claude", AsyncMock(return_value="")), \
         patch("nexus.handlers.streaks.get_streak", return_value=None), \
         patch("nexus.handlers.finance._calc_free_remaining",
               AsyncMock(return_value=None)):
        text = await tasks_mod._build_today_digest(999_002, user_notion_id="u-1")

    assert calls, "дейли-пинг не вызвал общий _format_task_dates"
    assert "FMT" in text
