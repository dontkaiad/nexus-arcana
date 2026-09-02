"""tests/test_budget_cushion.py — подушка как отдельный трекер + совет по экономии.

Подушка больше НЕ цель_-факт: отдельная таблица cushion, инкрементный баланс,
секция в плане, авто-кредит на payday, совет куда направить экономию периода.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── classifier: "подушка ..." роутится в cushion_command ────────────────────

@pytest.mark.parametrize("text", [
    "положила в подушку 5000",
    "добавь в подушку 5к",
    "закинула в подушку 3000",
    "подушка 300000",
    "измени подушку на 300к",
    "финансовая подушка цель 300000",
])
@pytest.mark.asyncio
async def test_classify_routes_cushion_commands(text):
    from core.classifier import classify
    res = await classify(text)
    assert res == [{"type": "cushion_command", "text": text}], text


@pytest.mark.asyncio
async def test_classify_cushion_needs_a_number():
    """«что там в подушке» без числа — не команда пополнения/цели."""
    from core.classifier import _CUSHION_CMD_RE
    import re
    assert _CUSHION_CMD_RE.search("сколько в подушке")
    assert not re.search(r"\d", "сколько в подушке")


def test_classify_cushion_before_goal_and_memory():
    from core.classifier import _CUSHION_CMD_RE, _GOAL_CMD_RE, _MEMORY_SAVE_RE
    t = "подушка 300000"
    assert _CUSHION_CMD_RE.search(t)
    assert not _GOAL_CMD_RE.search(t)
    assert not _MEMORY_SAVE_RE.match(t)


# ── load_budget_data: подушка отдельно, старый цель_подушка игнор ───────────

@pytest.mark.asyncio
async def test_load_budget_data_reads_cushion_separately():
    from core.repos.pg_memory_repo import Memory
    from core.repos import memory_repo as mrmod
    from core.repos.pg_cushion_repo import Cushion
    import core.repos.pg_cushion_repo as crmod
    from core.budget import load_budget_data

    fake_mems = [
        Memory(id="1", fact="цель: 📱 Телефон — 100000₽ · откладываю 0₽/мес", key="цель_телефон"),
        Memory(id="2", fact="цель: 💰 Подушка — 300000₽ · откладываю 5000₽/мес", key="цель_подушка"),
    ]
    with patch.object(mrmod._repo, "find_by_key_prefixes", AsyncMock(return_value=fake_mems)), \
         patch.object(crmod._repo, "get", AsyncMock(return_value=Cushion(
             user_notion_id="u", balance=42000, target=300000, monthly_contribution=5000))):
        data = await load_budget_data("u")

    names = [g["name"] for g in data["цели"]]
    assert "Телефон" in " ".join(names)
    assert not any("одушк" in n for n in names), "цель_подушка не должна попасть в цели"
    assert data["подушка"]["balance"] == 42000
    assert data["подушка"]["target"] == 300000


# ── _format_plan: секция подушки (с целью / без цели / нет подушки) ─────────

def test_format_plan_cushion_with_target():
    from nexus.handlers.finance import _format_plan
    out = _format_plan({"income_total": 100000, "fixed_total": 0,
                        "cushion": {"balance": 60000, "target": 300000}})
    assert "🛡️ Подушка: 60,000₽ / 300,000₽ (20%)" in out


def test_format_plan_cushion_without_target():
    from nexus.handlers.finance import _format_plan
    out = _format_plan({"income_total": 100000, "fixed_total": 0,
                        "cushion": {"balance": 15000, "target": None}})
    assert "🛡️ Подушка: 15,000₽</b>" in out
    assert "%" not in out  # без цели процент не показываем


def test_format_plan_no_cushion_section_when_empty():
    from nexus.handlers.finance import _format_plan
    assert "Подушка" not in _format_plan({"income_total": 100000, "fixed_total": 0})
    assert "Подушка" not in _format_plan({"income_total": 100000, "fixed_total": 0,
                                          "cushion": {"balance": 0, "target": None}})


# ── handle_cushion_command: пополнение vs цель ─────────────────────────────

@pytest.mark.asyncio
async def test_handle_cushion_deposit_increments():
    from nexus.handlers import finance
    import core.repos.pg_cushion_repo as crmod
    from core.repos.pg_cushion_repo import Cushion

    msg = MagicMock()
    msg.text = "положила в подушку 5000"
    msg.react = AsyncMock()
    msg.answer = AsyncMock()

    with patch.object(crmod._repo, "add_to_balance", AsyncMock(return_value=15000)) as m_add, \
         patch.object(crmod._repo, "get", AsyncMock(return_value=Cushion(balance=15000, target=None))):
        await finance.handle_cushion_command(msg, "положила в подушку 5000", "u")

    m_add.assert_awaited_once()
    assert m_add.await_args.args[1] == 5000
    assert "15,000" in msg.answer.call_args.args[0]


@pytest.mark.asyncio
async def test_handle_cushion_target_set():
    from nexus.handlers import finance
    import core.repos.pg_cushion_repo as crmod
    from core.repos.pg_cushion_repo import Cushion

    msg = MagicMock()
    msg.text = "подушка цель 300000"
    msg.react = AsyncMock()
    msg.answer = AsyncMock()

    with patch.object(crmod._repo, "add_to_balance", AsyncMock()) as m_add, \
         patch.object(crmod._repo, "set_target", AsyncMock()) as m_set, \
         patch.object(crmod._repo, "get", AsyncMock(return_value=Cushion(balance=0, target=300000))):
        await finance.handle_cushion_command(msg, "подушка цель 300000", "u")

    m_add.assert_not_awaited()
    m_set.assert_awaited_once()
    assert m_set.await_args.args[1] == 300000


# ── _budget_period_review: приоритет совета (долг → подушка → цель) ─────────

async def _run_review(*, debts, cushion, goals, spending, limits):
    from nexus.handlers import finance
    budget_data = {"доходы": [], "постоянные": [], "цели": goals, "долги": debts,
                   "лимиты": [], "подушка": cushion}
    records = [MagicMock(amount=amt, category=cat, type_="💸 Расход")
              for cat, amt in spending.items()]
    with patch.object(finance, "_get_payday", AsyncMock(return_value=1)), \
         patch.object(finance._repo, "query_records", AsyncMock(return_value=records)), \
         patch.object(finance, "_get_limits", AsyncMock(return_value=limits)), \
         patch.object(finance, "_load_budget_data", AsyncMock(return_value=budget_data)):
        text, saved = await finance._budget_period_review("u")
    return text, saved


@pytest.mark.asyncio
async def test_review_advice_priority_debt():
    text, saved = await _run_review(
        debts=[{"name": "Аня", "amount": 20000, "monthly_payment": 10000, "deadline": "апрель 2026"}],
        cushion={"balance": 1000, "target": 300000},
        goals=[{"name": "Телефон", "target": 100000}],
        spending={"🚬 Привычки": 5000}, limits={"привычки": 13000},
    )
    assert saved > 0
    assert "ускорение выплаты Аня" in text


@pytest.mark.asyncio
async def test_review_advice_priority_cushion():
    text, _ = await _run_review(
        debts=[],
        cushion={"balance": 50000, "target": 300000},
        goals=[{"name": "Телефон", "target": 100000}],
        spending={"🚬 Привычки": 5000}, limits={"привычки": 13000},
    )
    assert "в подушку" in text
    assert "50,000₽ из 300,000₽" in text


@pytest.mark.asyncio
async def test_review_advice_priority_goal():
    text, _ = await _run_review(
        debts=[],
        cushion={"balance": 300000, "target": 300000},  # полна
        goals=[{"name": "Наушники", "target": 20000}, {"name": "Ноутбук", "target": 150000}],
        spending={"🚬 Привычки": 5000}, limits={"привычки": 13000},
    )
    assert "приблизить «Наушники»" in text  # меньшая сумма = ближайшая


@pytest.mark.asyncio
async def test_review_no_advice_on_overspend():
    text, saved = await _run_review(
        debts=[{"name": "Аня", "amount": 20000, "monthly_payment": 10000, "deadline": "апрель 2026"}],
        cushion={"balance": 0, "target": 300000},
        goals=[{"name": "Телефон", "target": 100000}],
        spending={"🚬 Привычки": 20000}, limits={"привычки": 13000},  # перерасход 7000
    )
    assert saved < 0
    assert "💡" not in text
