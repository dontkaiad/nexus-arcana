"""tests/test_tasks_finance_pg.py — финанс-чтения в tasks.py мигрированы на PG.

_check_yesterday_expenses читает nexus_budget через PgNexusBudgetRepo (НЕ Notion),
user-scoped. Поведение сохранено: True если вчера был расход, иначе False.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from core.repos.pg_finance_repo import BudgetEntry


def _yesterday(tz_offset=3):
    tz = timezone(timedelta(hours=tz_offset))
    return (datetime.now(tz) - timedelta(days=1)).strftime("%Y-%m-%d")


@pytest.mark.asyncio
async def test_yesterday_expenses_true_from_pg():
    from nexus.handlers.tasks import _check_yesterday_expenses
    from core.repos.pg_finance_repo import PgNexusBudgetRepo

    y = _yesterday()
    entries = [BudgetEntry(id="1", amount=500.0, type_="💸 Расход", category="🍜 Продукты", date=y)]

    with patch.object(PgNexusBudgetRepo, "query_month", AsyncMock(return_value=entries)) as m_qm:
        res = await _check_yesterday_expenses("u-1", tz_offset=3)

    assert res is True
    m_qm.assert_awaited_once()
    assert m_qm.call_args.kwargs["user_notion_id"] == "u-1"


@pytest.mark.asyncio
async def test_yesterday_expenses_false_when_none_yesterday():
    from nexus.handlers.tasks import _check_yesterday_expenses
    from core.repos.pg_finance_repo import PgNexusBudgetRepo

    # расход есть, но не вчера (другая дата того же месяца) → False
    other = _yesterday()[:8] + "01"
    entries = [BudgetEntry(id="2", amount=100.0, type_="💸 Расход", category="🍱 Кафе", date=other)]

    with patch.object(PgNexusBudgetRepo, "query_month", AsyncMock(return_value=entries)):
        res = await _check_yesterday_expenses("u-1", tz_offset=3)

    assert res is False
