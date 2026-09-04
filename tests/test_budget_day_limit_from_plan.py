"""tests/test_budget_day_limit_from_plan.py — budget_day_limit_from_plan (#141).

«Бюджет дня» = (Доход − Фикс − Разовые − Долги − Подушка − Цели) / дни_до_пэйдея.
Каждый термин вычитается напрямую из своего источника (Память / debts / cushion),
НЕ через сумму лимит_*.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


_PLAN_PATH = "core.budget.load_budget_data"
_PAYDAY_PATH = "core.budget._budget_payday"
_DAYS_PATH = "core.budget._period_days_remaining"


def _plan(income=0, fixed=0, one_time=0, saving=0, debt_monthly=0, cushion=0):
    p = {
        "доходы":      [{"name": "зп", "amount": income}] if income else [],
        "постоянные":  [{"name": "аренда", "amount": fixed}] if fixed else [],
        "лимиты":      ([{"name": "лимит_продукты", "amount": 9999}]
                        + ([{"name": "лимит_разовые", "amount": one_time}] if one_time else [])),
        "цели":        [{"name": "айфон", "target": 100000, "saving": saving}] if saving else [],
        "долги":       [{"name": "кредит", "amount": 50000, "monthly_payment": debt_monthly}]
                       if debt_monthly > 0 else [],
    }
    if cushion:
        p["подушка"] = {"balance": 0, "target": 300000, "planned_contribution": cushion}
    return p


@pytest.mark.asyncio
async def test_basic_income_minus_fixed():
    """Дискреционные лимиты (продукты и т.п.) в формулу НЕ входят — только Фикс."""
    from core.budget import budget_day_limit_from_plan

    with patch(_PLAN_PATH, AsyncMock(return_value=_plan(income=90000, fixed=30000))), \
         patch(_PAYDAY_PATH, AsyncMock(return_value=1)), \
         patch(_DAYS_PATH, return_value=10):
        result = await budget_day_limit_from_plan("user-x")

    assert result == (90000 - 30000) // 10


@pytest.mark.asyncio
async def test_no_income_returns_zero():
    from core.budget import budget_day_limit_from_plan
    with patch(_PLAN_PATH, AsyncMock(return_value=_plan(income=0))):
        assert await budget_day_limit_from_plan("user-x") == 0


@pytest.mark.asyncio
async def test_subtracts_debt_monthly():
    from core.budget import budget_day_limit_from_plan
    with patch(_PLAN_PATH, AsyncMock(return_value=_plan(income=60000, debt_monthly=5000))), \
         patch(_PAYDAY_PATH, AsyncMock(return_value=1)), \
         patch(_DAYS_PATH, return_value=30):
        assert await budget_day_limit_from_plan("user-x") == (60000 - 5000) // 30


@pytest.mark.asyncio
async def test_subtracts_goal_saving():
    from core.budget import budget_day_limit_from_plan
    with patch(_PLAN_PATH, AsyncMock(return_value=_plan(income=60000, saving=3000))), \
         patch(_PAYDAY_PATH, AsyncMock(return_value=1)), \
         patch(_DAYS_PATH, return_value=30):
        assert await budget_day_limit_from_plan("user-x") == (60000 - 3000) // 30


@pytest.mark.asyncio
async def test_divisor_uses_days():
    from core.budget import budget_day_limit_from_plan
    base = _plan(income=60000)
    with patch(_PLAN_PATH, AsyncMock(return_value=base)), \
         patch(_PAYDAY_PATH, AsyncMock(return_value=1)), patch(_DAYS_PATH, return_value=30):
        r30 = await budget_day_limit_from_plan("user-x")
    with patch(_PLAN_PATH, AsyncMock(return_value=base)), \
         patch(_PAYDAY_PATH, AsyncMock(return_value=1)), patch(_DAYS_PATH, return_value=10):
        r10 = await budget_day_limit_from_plan("user-x")
    assert r10 > r30


@pytest.mark.asyncio
async def test_negative_remainder_returns_zero():
    from core.budget import budget_day_limit_from_plan
    with patch(_PLAN_PATH, AsyncMock(return_value=_plan(income=10000, fixed=15000))), \
         patch(_PAYDAY_PATH, AsyncMock(return_value=1)), patch(_DAYS_PATH, return_value=10):
        assert await budget_day_limit_from_plan("user-x") == 0


# ── Явная формула термин-в-термин ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_real_case_kai_tight_month():
    """Доход 100000, фикс 40000, разовые 43650, долги 0, подушка 0, цели 0
    → остаток 16350, дни 26 → ≈ 629₽/день."""
    from core.budget import budget_day_limit_from_plan

    plan = _plan(income=100000, fixed=40000, one_time=43650)
    with patch(_PLAN_PATH, AsyncMock(return_value=plan)), \
         patch(_PAYDAY_PATH, AsyncMock(return_value=1)), \
         patch(_DAYS_PATH, return_value=26):
        result = await budget_day_limit_from_plan("u")

    assert result == 16350 // 26   # 628


@pytest.mark.asyncio
async def test_all_terms_actually_subtracted():
    """Активные подушка + цели + долги — каждый термин реально вычитается."""
    from core.budget import budget_day_limit_from_plan

    plan = _plan(income=120000, fixed=30000, one_time=10000,
                 debt_monthly=8000, saving=5000, cushion=24000)
    # 120000 − 30000 − 10000 − 8000 − 24000 − 5000 = 43000
    with patch(_PLAN_PATH, AsyncMock(return_value=plan)), \
         patch(_PAYDAY_PATH, AsyncMock(return_value=1)), \
         patch(_DAYS_PATH, return_value=10):
        result = await budget_day_limit_from_plan("u")

    assert result == 43000 // 10


@pytest.mark.asyncio
async def test_ignores_discretionary_and_fixed_limit_facts():
    """лимит_продукты/лимит_привычки/лимит_фикс в budget['лимиты'] не влияют —
    формула не зависит от суммы лимит_*."""
    from core.budget import budget_day_limit_from_plan

    plan = {
        "доходы":     [{"name": "зп", "amount": 100000}],
        "постоянные": [{"name": "аренда", "amount": 40000}],
        "цели": [], "долги": [],
        "лимиты": [
            {"name": "лимит_продукты", "amount": 10000},
            {"name": "лимит_привычки", "amount": 6000},
            {"name": "лимит_фикс",    "amount": 40000},
            {"name": "лимит_разовые", "amount": 43650},   # ← единственный, что учитывается
        ],
    }
    with patch(_PLAN_PATH, AsyncMock(return_value=plan)), \
         patch(_PAYDAY_PATH, AsyncMock(return_value=1)), \
         patch(_DAYS_PATH, return_value=10):
        result = await budget_day_limit_from_plan("u")

    # 100000 − 40000(фикс) − 43650(разовые) = 16350
    assert result == 16350 // 10


@pytest.mark.asyncio
async def test_no_one_time_limit_fact():
    """Старые данные без лимит_разовые → термин Разовые = 0."""
    from core.budget import budget_day_limit_from_plan

    plan = {
        "доходы":     [{"name": "зп", "amount": 90000}],
        "постоянные": [{"name": "аренда", "amount": 30000}],
        "цели": [], "долги": [],
        "лимиты": [{"name": "продукты", "amount": 10000}],
    }
    with patch(_PLAN_PATH, AsyncMock(return_value=plan)), \
         patch(_PAYDAY_PATH, AsyncMock(return_value=1)), \
         patch(_DAYS_PATH, return_value=10):
        result = await budget_day_limit_from_plan("u")

    assert result == (90000 - 30000) // 10


def test_is_parallel_limit_predicate():
    from core.budget import is_parallel_limit
    assert is_parallel_limit("лимит_фикс")
    assert is_parallel_limit("лимит_разовые")
    assert is_parallel_limit("разовые")
    assert not is_parallel_limit("лимит_продукты")
    assert not is_parallel_limit("привычки")


# ── Личный часовой пояс: граница периода считается по дню пользователя ──────

from datetime import datetime as _real_dt, timezone as _tzc


def _frozen_dt(instant_utc):
    class _DT(_real_dt):
        @classmethod
        def now(cls, tz=None):
            return instant_utc.astimezone(tz) if tz is not None else instant_utc.replace(tzinfo=None)
    return _DT


def test_period_days_remaining_uses_user_tz(monkeypatch):
    import core.budget as B
    instant = _real_dt(2026, 6, 15, 20, 0, tzinfo=_tzc.utc)
    monkeypatch.setattr(B, "datetime", _frozen_dt(instant))
    assert B._period_days_remaining(1, tz_offset=3) == 15
    assert B._period_days_remaining(1, tz_offset=5) == 14
    assert B._period_days_remaining(1) == 15


@pytest.mark.asyncio
async def test_passes_tz_through():
    from core.budget import budget_day_limit_from_plan
    captured = {}

    def fake_days(payday, tz_offset=3):
        captured["tz"] = tz_offset
        return 20

    with patch(_PLAN_PATH, AsyncMock(return_value=_plan(income=60000))), \
         patch(_PAYDAY_PATH, AsyncMock(return_value=1)), \
         patch(_DAYS_PATH, side_effect=fake_days):
        await budget_day_limit_from_plan("user-x", tz_offset=5)
    assert captured["tz"] == 5

    captured.clear()
    with patch(_PLAN_PATH, AsyncMock(return_value=_plan(income=60000))), \
         patch(_PAYDAY_PATH, AsyncMock(return_value=1)), \
         patch(_DAYS_PATH, side_effect=fake_days):
        await budget_day_limit_from_plan("user-x")
    assert captured["tz"] == 3
