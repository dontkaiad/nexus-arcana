"""tests/test_cmd_finance_pg.py — /finance (cmd_finance) читает из PG nexus_budget.

Покрытие:
- cmd_finance зовёт PgNexusBudgetRepo.query_month(month, user_notion_id), НЕ finance_month;
- агрегирует расходы (by_cat/total/today) по BudgetEntry, доход пропускает;
- fail-closed: пустой user → не листит, query_month не вызывается.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.repos.pg_finance_repo import BudgetEntry


def _entries():
    return [
        BudgetEntry(id="1", description="Кофе",  amount=100.0, category="🍱 Кафе",   type_="💸 Расход", date="2026-06-19"),
        BudgetEntry(id="2", description="Метро", amount=50.0,  category="🍱 Кафе",   type_="💸 Расход", date="2026-06-10"),
        BudgetEntry(id="3", description="Хлеб",  amount=30.0,  category="🍜 Продукты", type_="💸 Расход", date="2026-06-19"),
        BudgetEntry(id="4", description="ЗП",    amount=1000.0, category="💰 Зарплата", type_="💰 Доход",  date="2026-06-19"),
    ]


@pytest.mark.asyncio
async def test_cmd_finance_reads_pg_nexus_budget(mock_message):
    from nexus.nexus_bot import cmd_finance
    from core.repos.pg_finance_repo import PgNexusBudgetRepo

    msg = mock_message("/finance")

    with patch.object(PgNexusBudgetRepo, "query_month",
                      AsyncMock(return_value=_entries())) as m_qm, \
         patch("core.classifier.today_moscow", MagicMock(return_value="2026-06-19")), \
         patch("nexus.handlers.finance._calc_free_remaining", AsyncMock(return_value=None)), \
         patch("nexus.handlers.finance._load_budget_data", AsyncMock(return_value={"доходы": []})), \
         patch("nexus.handlers.finance._get_limits", AsyncMock(return_value={})):
        await cmd_finance(msg, user_notion_id="u-1")

    # читали PG nexus_budget, user-scoped; Notion не звали
    m_qm.assert_awaited_once()
    assert m_qm.call_args.args[0] == "2026-06"           # month
    assert m_qm.call_args.kwargs["user_notion_id"] == "u-1"

    out = "\n".join(str(c.args[0]) for c in msg.answer.call_args_list)
    # total expense = 100+50+30 = 180 (доход 1000 пропущен)
    assert "180" in out
    # today (19-е) = 100+30 = 130
    assert "Сегодня" in out and "130" in out
    # доход Арканы/зарплаты не протёк в расходы
    assert "1 000" not in out and "1000" not in out


@pytest.mark.asyncio
async def test_cmd_finance_fail_closed_empty_user(mock_message):
    from nexus.nexus_bot import cmd_finance
    from core.repos.pg_finance_repo import PgNexusBudgetRepo

    msg = mock_message("/finance")

    with patch.object(PgNexusBudgetRepo, "query_month", AsyncMock()) as m_qm:
        await cmd_finance(msg, user_notion_id="")

    m_qm.assert_not_called()
    assert "не могу определить" in msg.answer.call_args.args[0].lower()
