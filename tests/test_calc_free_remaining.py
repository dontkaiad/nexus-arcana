"""tests/test_calc_free_remaining.py — «💳 Свободных» после траты считается
по BUDGET_SPEC, а не как «общий остаток».

    Остаток на жизнь = Доход − Фикс − платёж по активному долгу
    Свободных        = Остаток на жизнь − Σ(дискреционные траты периода)
    /день            = Свободных / дней до конца платёжного периода

Регрессия: раньше `_calc_free_remaining` = доход − все_расходы − цели по
КАЛЕНДАРНОМУ месяцу (не вычитал Фикс/долг, вычитал цели, окно не то).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


def _rec(amount, category=""):
    return SimpleNamespace(amount=amount, category=category)


async def _call(budget, income_recs, expense_recs, payday=1, tz=3):
    from nexus.handlers import finance
    with patch.object(finance, "_load_budget_data", AsyncMock(return_value=budget)), \
         patch.object(finance, "_get_payday", AsyncMock(return_value=payday)), \
         patch.object(finance._repo, "query_records",
                      AsyncMock(side_effect=[income_recs, expense_recs])) as q:
        res = await finance._calc_free_remaining("u", tz_offset=tz)
    return res, q


# ── (1) свежий период: Фикс + долг вычитаются, трат нет ─────────────────────

@pytest.mark.asyncio
async def test_fresh_period_subtracts_fixed_and_debt():
    budget = {
        "постоянные": [{"amount": 30000}, {"amount": 5000}],   # Фикс = 35000
        "долги": [{"name": "карта", "monthly_payment": 20000, "deadline": "март 2027"}],
        "цели": [{"name": "подушка", "saving": 10000}],         # НЕ должно вычитаться
        "доходы": [], "лимиты": [],
    }
    (res, _q) = await _call(budget, [_rec(120000, "💰 Доход")], [])
    assert res is not None
    free, days = res
    # 120000 − 35000 фикс − 20000 долг − 0 трат = 65000  (цели НЕ вычтены)
    assert free == 65000.0


# ── (2) декремент: трата 500₽ уменьшает Свободных ровно на 500 ─────────────

@pytest.mark.asyncio
async def test_expense_decrements_exactly():
    budget = {"постоянные": [{"amount": 10000}], "долги": [], "цели": [],
              "доходы": [], "лимиты": []}
    before, _ = await _call(budget, [_rec(50000)], [_rec(1000, "🍜 Продукты")])
    after, _ = await _call(budget, [_rec(50000)], [_rec(1000, "🍜 Продукты"),
                                                   _rec(500, "🍜 Продукты")])
    assert before[0] - after[0] == 500.0


# ── (3) окно и делитель — платёжный период, не календарный месяц ───────────

@pytest.mark.asyncio
async def test_uses_payday_period_not_calendar_month():
    budget = {"постоянные": [], "долги": [], "цели": [], "доходы": [], "лимиты": []}
    (_res, q) = await _call(budget, [_rec(10000)], [], payday=15, tz=3)
    # date_from обоих запросов = начало платёжного периода (payday=15), НЕ '<месяц>-01'
    income_call, expense_call = q.await_args_list
    df_income = income_call.kwargs["date_from"]
    df_expense = expense_call.kwargs["date_from"]
    assert df_income == df_expense
    assert df_income.endswith("-15") or df_income[8:10] == "15", df_income

    from core.budget import _period_days_remaining
    assert _res[1] == _period_days_remaining(15, 3)


# ── (4) Фикс/Разовые траты не двойно-считаются ────────────────────────────

@pytest.mark.asyncio
async def test_parallel_limit_expenses_excluded():
    budget = {"постоянные": [{"amount": 30000}], "долги": [], "цели": [],
              "доходы": [], "лимиты": []}
    # 30000 Фикс уже вычтен как терм; транзакция «🔒 Фикс 30000» не должна
    # вычитаться повторно. Обычная трата 2000 — вычитается.
    (res, _q) = await _call(
        budget, [_rec(100000)],
        [_rec(30000, "🔒 Фикс"), _rec(9000, "📦 Разовые"), _rec(2000, "🍜 Продукты")],
    )
    # 100000 − 30000 фикс(терм) − 0 долг − 2000 (только дискреционная) = 68000
    assert res[0] == 68000.0


# ── (5) нет дохода → None (guard сохранён) ────────────────────────────────

@pytest.mark.asyncio
async def test_no_income_returns_none():
    budget = {"постоянные": [{"amount": 5000}], "долги": [], "цели": [],
              "доходы": [], "лимиты": []}
    (res, _q) = await _call(budget, [], [])
    assert res is None
