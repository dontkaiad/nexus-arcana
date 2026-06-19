"""tests/test_cmd_tasks_pg.py — /tasks (cmd_tasks) читает задачи из PG (_repo.active).

Покрытие:
- cmd_tasks зовёт _repo.active(user_notion_id), НЕ query_pages;
- категоризация overdue / today / daily / other по PG Task-объектам;
- fail-closed: пустой user → не листит, active не вызывается.
"""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nexus.repos.pg_tasks_repo import Task


def _today():
    return date.today().isoformat()


def _yesterday():
    return (date.today() - timedelta(days=1)).isoformat()


def _tomorrow():
    return (date.today() + timedelta(days=1)).isoformat()


def _tasks():
    return [
        Task(id="1", title="Просроченная", priority="🔴 Срочно",
             category="💳 Прочее", deadline=_yesterday(), repeat="Нет"),
        Task(id="2", title="Сегодняшняя", priority="🟡 Важно",
             category="🏥 Здоровье", deadline=_today(), repeat="Нет"),
        Task(id="3", title="Зарядка", priority="🟡 Важно",
             category="💪 Спорт", deadline="", repeat="Ежедневно"),
        Task(id="4", title="Когда-нибудь", priority="⚪ Можно потом",
             category="📚 Хобби", deadline=_tomorrow(), repeat="Нет"),
    ]


@pytest.mark.asyncio
async def test_cmd_tasks_reads_pg_and_categorizes(mock_message):
    from nexus.nexus_bot import cmd_tasks

    msg = mock_message("/tasks")

    with patch("nexus.repos.tasks_repo._repo.active",
               AsyncMock(return_value=_tasks())) as m_active, \
         patch("core.notion_client.query_pages", AsyncMock()) as m_qp, \
         patch("nexus.handlers.streaks.get_streak",
               MagicMock(return_value={"streak": 2, "best": 4})):
        await cmd_tasks(msg, user_notion_id="u-1")

    # читали PG, не Notion
    m_active.assert_awaited_once_with(user_notion_id="u-1")
    m_qp.assert_not_called()

    # собираем весь вывод (может быть несколько answer-чанков)
    out = "\n".join(str(c.args[0]) for c in msg.answer.call_args_list)
    assert "🔥 ПРОСРОЧЕНО" in out
    assert "📅 СЕГОДНЯ" in out

    # категоризация: overdue → раньше СЕГОДНЯ; today/daily → секция СЕГОДНЯ;
    # future-несрочная → ВСЕ ЗАДАЧИ
    i_over = out.index("🔥 ПРОСРОЧЕНО")
    i_today = out.index("📅 СЕГОДНЯ")
    assert i_over < out.index("Просроченная")
    assert i_over < i_today
    assert out.index("Сегодняшняя") > i_today
    assert "Зарядка" in out          # daily, в секции СЕГОДНЯ
    assert "Когда-нибудь" in out     # other → ВСЕ ЗАДАЧИ


@pytest.mark.asyncio
async def test_cmd_tasks_fail_closed_empty_user(mock_message):
    from nexus.nexus_bot import cmd_tasks

    msg = mock_message("/tasks")

    with patch("nexus.repos.tasks_repo._repo.active", AsyncMock()) as m_active:
        await cmd_tasks(msg, user_notion_id="")

    m_active.assert_not_called()
    txt = msg.answer.call_args.args[0]
    assert "не могу определить" in txt.lower()
