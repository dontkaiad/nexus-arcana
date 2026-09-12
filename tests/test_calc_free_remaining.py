"""tests/test_calc_free_remaining.py — единая величина «Свободных» (#237).

Раньше бот (`nexus.handlers.finance._calc_free_remaining`) и Mini App
(«Мой день», `_discretionary_free`) считали ДВЕ РАЗНЫЕ величины под одной
подписью — числа расходились (60к в боте vs 6к в приложении). Теперь обе
стороны зовут ОДНУ функцию — `core.budget.discretionary_free`:

    свободно = max(0, Σ дискреционных лимитов
                      − Σ трат по лимит-категориям с начала периода)

Bugfix (после #237): версия сразу после слияния формул по ошибке ещё и
вычитала реальные траты 📦 Разовые из этого же пула — а Разовые параллельный
счётчик со своим лимитом (`is_parallel_limit`), в дискреционный пул НЕ входят
ни бюджетом, ни тратой. Крупная разовая трата обнуляла «Свободно», хотя к
продуктам/привычкам/etc она отношения не имеет.

Этот файл проверяет: (1) бот делегирует в неё без искажений, (2) саму
формулу — лимиты, декремент тратами, guard без лимитов, (3) 📦 Разовые НЕ
трогают дискреционный пул.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from core.budget import discretionary_free


def _entry(amount, category=""):
    return SimpleNamespace(amount=amount, category=category)


async def _call(limits, entries, payday=1, tz=3):
    with patch("core.budget.get_limits", AsyncMock(return_value=limits)), \
         patch("core.budget._budget_payday", AsyncMock(return_value=payday)), \
         patch("core.repos.pg_finance_repo.PgNexusBudgetRepo.query",
               AsyncMock(return_value=entries)) as q:
        res = await discretionary_free("u", tz_offset=tz)
    return res, q


# ── (1) бот делегирует в core.budget.discretionary_free без искажений ──────

@pytest.mark.asyncio
async def test_bot_delegates_to_shared_formula():
    from nexus.handlers import finance
    with patch.object(finance, "_discretionary_free", AsyncMock(return_value=(4200.0, 9))) as df:
        res = await finance._calc_free_remaining("u", tz_offset=5)
    df.assert_awaited_once_with("u", 5)
    assert res == (4200.0, 9)


# ── (2) лимиты минус траты по лимит-категориям ──────────────────────────────

@pytest.mark.asyncio
async def test_limits_minus_category_spend():
    limits = {"продукты": 10000, "привычки": 5000}
    entries = [_entry(3000, "🍜 Продукты")]
    (res, _q) = await _call(limits, entries)
    assert res is not None
    free, _days = res
    assert free == 12000.0  # 15000 лимитов − 3000 потрачено


# ── (3) декремент: трата 500₽ уменьшает «свободно» ровно на 500 ────────────

@pytest.mark.asyncio
async def test_expense_decrements_exactly():
    limits = {"продукты": 10000}
    before, _ = await _call(limits, [_entry(1000, "🍜 Продукты")])
    after, _ = await _call(limits, [_entry(1000, "🍜 Продукты"), _entry(500, "🍜 Продукты")])
    assert before[0] - after[0] == 500.0


# ── (4) 📦 Разовые НЕ трогают дискреционный пул (bugfix после #237) ────────

@pytest.mark.asyncio
async def test_one_off_expense_not_subtracted():
    """📦 Разовые — параллельный счётчик со своим лимитом, не дискреционная
    трата. Крупная разовая трата не должна обнулять «Свободно»."""
    limits = {"продукты": 10000}
    without_one_off, _ = await _call(limits, [])
    with_one_off, _ = await _call(limits, [_entry(4000, "📦 Разовые")])
    assert without_one_off[0] == with_one_off[0] == 10000.0


@pytest.mark.asyncio
async def test_fixed_category_not_double_counted():
    """🔒 Фикс — свой лимит на весь период, не дискреционный расход и не
    вычитается здесь (в отличие от 📦 Разовые, которые теперь вычитаются)."""
    limits = {"продукты": 10000}
    (res, _q) = await _call(limits, [_entry(30000, "🔒 Фикс"), _entry(2000, "🍜 Продукты")])
    assert res[0] == 8000.0  # только 2000 (Продукты) вычтено, Фикс — нет


# ── (5) нет настроенных лимитов → None (нечего считать) ────────────────────

@pytest.mark.asyncio
async def test_no_limits_returns_none():
    (res, _q) = await _call({}, [])
    assert res is None


# ── (6) окно запроса — платёжный период, не календарный месяц ──────────────

@pytest.mark.asyncio
async def test_uses_payday_period_not_calendar_month():
    (_res, q) = await _call({"продукты": 5000}, [], payday=15, tz=3)
    date_from = q.await_args.kwargs["date_from"]
    assert date_from[8:10] == "15", date_from

    from core.budget import _period_days_remaining
    assert _res[1] == _period_days_remaining(15, 3)
