"""tests/test_budget_day_limit_from_plan.py — budget_day_limit_from_plan (#141).

Проверяет:
- считается из плана (не 4166)
- при отсутствии дохода возвращает 0
- делитель — дни до пэйдея
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


_PLAN_PATH = "core.budget.load_budget_data"
_PAYDAY_PATH = "core.budget._budget_payday"
_DAYS_PATH = "core.budget._period_days_remaining"


def _plan(income=0, obligatory=0, limits=0, saving=0, debt_monthly=0):
    return {
        "доходы":      [{"name": "зп", "amount": income}] if income else [],
        "обязательные": [{"name": "аренда", "amount": obligatory}] if obligatory else [],
        "лимиты":      [{"name": "продукты", "amount": limits}] if limits else [],
        "цели":        [{"name": "подушка", "target": 100000, "saving": saving}] if saving else [],
        "долги":       [{"name": "кредит", "amount": 50000, "monthly_payment": debt_monthly}]
                       if debt_monthly > 0 else [],
    }


@pytest.mark.asyncio
async def test_budget_day_limit_from_plan_basic():
    """Считается из плана: (income - obligatory - limits) / days."""
    from core.budget import budget_day_limit_from_plan

    with patch(_PLAN_PATH, AsyncMock(return_value=_plan(income=90000, obligatory=30000, limits=10000))), \
         patch(_PAYDAY_PATH, AsyncMock(return_value=1)), \
         patch(_DAYS_PATH, return_value=10):
        result = await budget_day_limit_from_plan("user-x")

    assert result == (90000 - 30000 - 10000) // 10


@pytest.mark.asyncio
async def test_budget_day_limit_from_plan_no_income_returns_zero():
    """Без дохода возвращает 0."""
    from core.budget import budget_day_limit_from_plan

    with patch(_PLAN_PATH, AsyncMock(return_value=_plan(income=0))):
        result = await budget_day_limit_from_plan("user-x")

    assert result == 0


@pytest.mark.asyncio
async def test_budget_day_limit_from_plan_subtracts_debt_monthly():
    """Долг с monthly_payment вычитается из свободных."""
    from core.budget import budget_day_limit_from_plan

    with patch(_PLAN_PATH, AsyncMock(return_value=_plan(income=60000, debt_monthly=5000))), \
         patch(_PAYDAY_PATH, AsyncMock(return_value=1)), \
         patch(_DAYS_PATH, return_value=30):
        result = await budget_day_limit_from_plan("user-x")

    assert result == (60000 - 5000) // 30


@pytest.mark.asyncio
async def test_budget_day_limit_from_plan_subtracts_goal_saving():
    """saving из цели вычитается из свободных."""
    from core.budget import budget_day_limit_from_plan

    with patch(_PLAN_PATH, AsyncMock(return_value=_plan(income=60000, saving=3000))), \
         patch(_PAYDAY_PATH, AsyncMock(return_value=1)), \
         patch(_DAYS_PATH, return_value=30):
        result = await budget_day_limit_from_plan("user-x")

    assert result == (60000 - 3000) // 30


@pytest.mark.asyncio
async def test_budget_day_limit_from_plan_divisor_uses_days():
    """Больше дней до пэйдея → меньше дневной лимит."""
    from core.budget import budget_day_limit_from_plan

    base_plan = _plan(income=60000)
    with patch(_PLAN_PATH, AsyncMock(return_value=base_plan)), \
         patch(_PAYDAY_PATH, AsyncMock(return_value=1)), \
         patch(_DAYS_PATH, return_value=30):
        result_30 = await budget_day_limit_from_plan("user-x")

    with patch(_PLAN_PATH, AsyncMock(return_value=base_plan)), \
         patch(_PAYDAY_PATH, AsyncMock(return_value=1)), \
         patch(_DAYS_PATH, return_value=10):
        result_10 = await budget_day_limit_from_plan("user-x")

    assert result_10 > result_30


@pytest.mark.asyncio
async def test_budget_day_limit_from_plan_negative_free_returns_zero():
    """Расходы превышают доход → max(0, ...) → 0."""
    from core.budget import budget_day_limit_from_plan

    with patch(_PLAN_PATH, AsyncMock(return_value=_plan(income=10000, obligatory=15000))), \
         patch(_PAYDAY_PATH, AsyncMock(return_value=1)), \
         patch(_DAYS_PATH, return_value=10):
        result = await budget_day_limit_from_plan("user-x")

    assert result == 0
