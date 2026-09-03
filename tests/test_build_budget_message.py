"""tests/test_build_budget_message.py

build_budget_message() — показ УЖЕ СОХРАНЁННОГО плана (/budget на принятом
периоде, без Sonnet). Регресс: «Распределяемые» и «Свободных» не учитывали
📦 Разовые (лимит_разовые) → заголовок расходился с суммой лимитов ниже.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nexus.handlers import finance

MOSCOW = finance.MOSCOW_TZ


def _rec(category: str, amount: float):
    return MagicMock(category=category, amount=amount)


def _bounds():
    today = datetime.now(MOSCOW).date()
    return ((today - timedelta(days=10)).strftime("%Y-%m-%d"),
            (today + timedelta(days=10)).strftime("%Y-%m-%d"))


async def _run(budget: dict, expenses: list) -> str:
    start, end = _bounds()
    with patch.object(finance, "_load_budget_data", AsyncMock(return_value=budget)), \
         patch.object(finance, "_get_payday", AsyncMock(return_value=1)), \
         patch.object(finance, "_period_bounds", lambda *a, **k: (start, end)), \
         patch.object(finance._repo, "query_records", AsyncMock(return_value=expenses)):
        return await finance.build_budget_message("u-1")


_DISC_LIMITS = [
    {"name": "лимит_транспорт", "amount": 1500},
    {"name": "лимит_импульсивный", "amount": 1000},
    {"name": "лимит_продукты", "amount": 6925},
    {"name": "лимит_привычки", "amount": 6925},
]


@pytest.mark.asyncio
async def test_distributable_subtracts_one_time_limit():
    budget = {
        "доходы": [{"name": "зп", "amount": 100000}],
        "постоянные": [{"name": "аренда", "amount": 40000}],
        "цели": [], "долги": [],
        "лимиты": _DISC_LIMITS + [{"name": "лимит_разовые", "amount": 43650}],
    }
    out = await _run(budget, [])
    # 100000 − 40000 − 43650 = 16 350 (совпадает с суммой дискреционных лимитов)
    assert "💳 Распределяемые: 16,350₽" in out
    assert "💳 Распределяемые: 60,000₽" not in out


@pytest.mark.asyncio
async def test_free_and_spent_use_discretionary_base_not_inflated_by_one_time():
    budget = {
        "доходы": [{"name": "зп", "amount": 100000}],
        "постоянные": [{"name": "аренда", "amount": 40000}],
        "цели": [], "долги": [],
        "лимиты": _DISC_LIMITS + [{"name": "лимит_разовые", "amount": 43650}],
    }
    # потратила 2000 продукты + 8000 по разовым
    out = await _run(budget, [_rec("🍜 Продукты", 2000), _rec("📦 Разовые", 8000)])

    # Потрачено / Свободных — только дискреционный пул (16 350), разовые не раздувают
    assert "📉 Потрачено: 2,000 / 16,350₽" in out
    assert "💳 Свободных: 14,350₽" in out
    # 📦 Разовые всё же видно отдельной строкой с прогрессом
    assert "📦 Разовые — 8,000 / 43,650₽" in out
    # 🔒 Фикс отдельной строкой в Лимитах не дублируем (есть секция Постоянные)
    assert "🔒 Фикс —" not in out


@pytest.mark.asyncio
async def test_fixed_limit_not_double_counted_in_distributable():
    budget = {
        "доходы": [{"name": "зп", "amount": 100000}],
        "постоянные": [{"name": "аренда", "amount": 40000}],
        "цели": [], "долги": [],
        "лимиты": _DISC_LIMITS + [
            {"name": "лимит_фикс", "amount": 40000},
            {"name": "лимит_разовые", "amount": 10000},
        ],
    }
    out = await _run(budget, [])
    # фикс НЕ вычитается второй раз: 100000 − 40000(постоянные) − 10000(разовые)
    assert "💳 Распределяемые: 50,000₽" in out


@pytest.mark.asyncio
async def test_regression_no_one_time_limit_behaves_as_before():
    budget = {
        "доходы": [{"name": "зп", "amount": 80000}],
        "постоянные": [{"name": "аренда", "amount": 20000}],
        "цели": [], "долги": [],
        "лимиты": _DISC_LIMITS,
    }
    out = await _run(budget, [_rec("🍜 Продукты", 1000)])
    assert "💳 Распределяемые: 60,000₽" in out  # 80000 − 20000, разовых нет
    disc_total = 1500 + 1000 + 6925 + 6925
    assert f"📉 Потрачено: 1,000 / {disc_total:,}₽" in out


@pytest.mark.asyncio
async def test_no_crash_on_empty_limits():
    budget = {
        "доходы": [{"name": "зп", "amount": 50000}],
        "постоянные": [{"name": "аренда", "amount": 10000}],
        "цели": [], "долги": [], "лимиты": [],
    }
    out = await _run(budget, [])
    assert "💳 Распределяемые: 40,000₽" in out
