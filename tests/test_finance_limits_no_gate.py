"""tests/test_finance_limits_no_gate.py — лимиты применяются на проде (#155).

budget.get_limits читает лимиты из PG (память, категория '💰 Лимит') и
ИГНОРИРУЕТ параметр mem_db. Раньше ~10 мест гейтили вызов на наличие
NOTION_DB_MEMORY (которой на проде нет) → лимиты не применялись. Гейты сняты.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_get_limits_reads_pg_ignoring_env(monkeypatch):
    monkeypatch.delenv("NOTION_DB_MEMORY", raising=False)
    from core import budget

    class _Mem:
        fact = "лимит: 🛒 Продукты — 15000₽/мес"
        related_to = "🛒 продукты"

    with patch("core.repos.memory_repo._repo.find_by_category",
               AsyncMock(return_value=[_Mem()])):
        limits = await budget.get_limits("")   # env не задан — всё равно читает PG
    assert any(v == 15000.0 for v in limits.values())


@pytest.mark.asyncio
async def test_calc_free_remaining_not_gated_by_env(monkeypatch):
    monkeypatch.delenv("NOTION_DB_MEMORY", raising=False)
    from nexus.handlers import finance
    budget = {"обязательные": [{"amount": 1000}], "цели": [{"saving": 0}],
              "доходы": [], "долги": [], "лимиты": []}
    # income → 5000, expenses → []  (раньше gate возвращал None ещё до этого)
    income = [SimpleNamespace(amount=5000)]
    with patch.object(finance, "_load_budget_data", AsyncMock(return_value=budget)), \
         patch.object(finance._repo, "query_records",
                      AsyncMock(side_effect=[income, []])):
        res = await finance._calc_free_remaining("u")
    assert res is not None   # дошли до расчёта — gate на NOTION_DB_MEMORY снят
    assert res[0] == 5000.0  # 5000 income − 0 expenses − 0 savings
