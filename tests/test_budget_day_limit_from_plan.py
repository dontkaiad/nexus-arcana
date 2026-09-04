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
        "постоянные":  [{"name": "аренда", "amount": obligatory}] if obligatory else [],
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


# ── Личный часовой пояс: граница периода считается по дню пользователя ──────

from datetime import datetime as _real_dt, timezone as _tzc


def _frozen_dt(instant_utc):
    class _DT(_real_dt):
        @classmethod
        def now(cls, tz=None):
            return instant_utc.astimezone(tz) if tz is not None else instant_utc.replace(tzinfo=None)
    return _DT


def test_period_days_remaining_uses_user_tz(monkeypatch):
    """Момент, когда у сервера (UTC+3) ещё 15 июня, а у юзера (UTC+5) уже 16-е →
    «дней до пэйдея» отличается на 1 (дата у него перевалила)."""
    import core.budget as B

    instant = _real_dt(2026, 6, 15, 20, 0, tzinfo=_tzc.utc)  # +3 → 23:00 15-го, +5 → 01:00 16-го
    monkeypatch.setattr(B, "datetime", _frozen_dt(instant))

    d_msk = B._period_days_remaining(1, tz_offset=3)
    d_user = B._period_days_remaining(1, tz_offset=5)

    assert d_msk == 15
    assert d_user == 14
    # регресс: дефолт == явный 3
    assert B._period_days_remaining(1) == d_msk


@pytest.mark.asyncio
async def test_budget_day_limit_from_plan_passes_tz_through():
    """tz_offset пробрасывается в _period_days_remaining."""
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


@pytest.mark.asyncio
async def test_budget_day_limit_from_plan_default_tz_is_3():
    from core.budget import budget_day_limit_from_plan

    captured = {}

    def fake_days(payday, tz_offset=3):
        captured["tz"] = tz_offset
        return 20

    with patch(_PLAN_PATH, AsyncMock(return_value=_plan(income=60000))), \
         patch(_PAYDAY_PATH, AsyncMock(return_value=1)), \
         patch(_DAYS_PATH, side_effect=fake_days):
        await budget_day_limit_from_plan("user-x")

    assert captured["tz"] == 3


# ── лимит_фикс / лимит_разовые НЕ входят в total_limits ────────────────────

@pytest.mark.asyncio
async def test_day_limit_excludes_fixed_and_one_time_limits():
    """budget["лимиты"] содержит лимит_фикс (задвоил бы постоянные) и
    лимит_разовые (отдельный бакет) — оба исключаются из формулы."""
    from core.budget import budget_day_limit_from_plan

    plan = {
        "доходы":     [{"name": "зп", "amount": 100000}],
        "постоянные": [{"name": "аренда", "amount": 40000}],
        "цели": [], "долги": [],
        "лимиты": [
            {"name": "лимит_продукты", "amount": 10000},
            {"name": "лимит_привычки", "amount": 6000},
            {"name": "лимит_фикс",    "amount": 40000},   # = постоянные, задвоение
            {"name": "лимит_разовые", "amount": 43650},   # отдельный бакет
        ],
    }
    with patch(_PLAN_PATH, AsyncMock(return_value=plan)), \
         patch(_PAYDAY_PATH, AsyncMock(return_value=1)), \
         patch(_DAYS_PATH, return_value=10):
        result = await budget_day_limit_from_plan("u")

    # free = 100000 − 40000(постоянные) − 16000(только дискреционные лимиты) = 44000
    assert result == 44000 // 10
    assert result > 0  # не ушло в 0 из-за задвоения


@pytest.mark.asyncio
async def test_day_limit_regression_no_parallel_limits():
    """Старые данные без лимит_фикс/лимит_разовые → как раньше."""
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

    assert result == (90000 - 30000 - 10000) // 10


def test_is_parallel_limit_predicate():
    from core.budget import is_parallel_limit
    assert is_parallel_limit("лимит_фикс")
    assert is_parallel_limit("лимит_разовые")
    assert is_parallel_limit("разовые")        # по display-имени
    assert not is_parallel_limit("лимит_продукты")
    assert not is_parallel_limit("привычки")


@pytest.mark.asyncio
async def test_budget_day_limit_from_plan_negative_free_returns_zero():
    """Расходы превышают доход → max(0, ...) → 0."""
    from core.budget import budget_day_limit_from_plan

    with patch(_PLAN_PATH, AsyncMock(return_value=_plan(income=10000, obligatory=15000))), \
         patch(_PAYDAY_PATH, AsyncMock(return_value=1)), \
         patch(_DAYS_PATH, return_value=10):
        result = await budget_day_limit_from_plan("user-x")

    assert result == 0
