"""tests/test_windfall_income_distribution.py

Непредвиденный доход (не Зарплата/Практика) — авто-распределение:
тяжёлый месяц докидывает 🎲 Импульсивные (потолок 3000₽/период из
windfall-источников, накопленный трекером impulse_windfall_бонус_{период}),
остаток (или весь доход в обычный месяц) — активный долг либо подушка.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nexus.handlers import finance


_BUDGET_TIGHT = {
    # income - fixed - debt_payment < BUDGET_TIGHT_THRESHOLD (25500) → тяжёлый
    "доходы": [{"name": "зарплата", "amount": 30000}],
    "постоянные": [{"name": "аренда", "amount": 10000}],
    "долги": [], "цели": [], "лимиты": [],
}
_BUDGET_NORMAL_WITH_DEBT = {
    # income - fixed - debt_payment = 100000 - 10000 - 5000 = 85000 ≥ 25500 → обычный
    "доходы": [{"name": "зарплата", "amount": 100000}],
    "постоянные": [{"name": "аренда", "amount": 10000}],
    "долги": [{"name": "Вика", "amount": 20000, "deadline": "2026-12-01",
               "strategy": "", "monthly_payment": 5000}],
    "цели": [], "лимиты": [],
}
_BUDGET_NORMAL_NO_DEBT = {
    "доходы": [{"name": "зарплата", "amount": 100000}],
    "постоянные": [{"name": "аренда", "amount": 10000}],
    "долги": [], "цели": [], "лимиты": [],
}


def _patches(budget_data, impulse_bonus=0.0, limits=None):
    """Общий набор моков для _distribute_windfall_income."""
    return [
        patch.object(finance, "_get_user_tz", AsyncMock(return_value=3)),
        patch.object(finance, "_get_payday", AsyncMock(return_value=1)),
        patch.object(finance, "_load_budget_data", AsyncMock(return_value=budget_data)),
        patch.object(finance, "_get_impulse_windfall_bonus", AsyncMock(return_value=impulse_bonus)),
        patch.object(finance, "_add_impulse_windfall_bonus",
                     AsyncMock(side_effect=lambda uid, ps, amt: impulse_bonus + amt)),
        patch.object(finance, "_get_limits", AsyncMock(return_value=limits or {})),
        patch.object(finance, "_save_memory_entry", AsyncMock()),
    ]


class _apply:
    def __init__(self, patchers):
        self._patchers = patchers

    def __enter__(self):
        for p in self._patchers:
            p.__enter__()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patchers):
            p.__exit__(*exc)
        return False


@pytest.mark.asyncio
async def test_tight_month_full_headroom_all_to_impulse():
    """Тяжёлый месяц, доход 2000₽, headroom полный (3000) → всё в Импульсивные,
    ничего в долг/подушку."""
    with _apply(_patches(_BUDGET_TIGHT, impulse_bonus=0.0)), \
         patch("core.repos.pg_cushion_repo._repo.add_to_balance", AsyncMock()) as m_cushion, \
         patch.object(finance, "_partial_debt_payment", AsyncMock()) as m_debt:
        msg = await finance._distribute_windfall_income(2000, uid=1, user_id="u-1")

    assert "🎲 Импульсивные +2,000₽" in msg
    assert "Долг" not in msg
    assert "Подушка" not in msg
    m_cushion.assert_not_called()
    m_debt.assert_not_called()


@pytest.mark.asyncio
async def test_tight_month_partial_headroom_splits_impulse_and_debt():
    """Тяжёлый месяц, доход 5000₽, headroom 3000 (ничего ещё не добавлено) →
    3000 в Импульсивные, 2000 — остаток по правилу обычного месяца (долг)."""
    budget = dict(_BUDGET_TIGHT, долги=[{"name": "Вика", "amount": 20000,
                  "deadline": "2026-12-01", "strategy": "", "monthly_payment": 1000}])
    with _apply(_patches(budget, impulse_bonus=0.0)), \
         patch.object(finance, "_partial_debt_payment", AsyncMock(return_value=(18000, 0.0))) as m_debt:
        msg = await finance._distribute_windfall_income(5000, uid=1, user_id="u-1")

    assert "Импульсивные +3" in msg
    assert "Долг Вика +2" in msg
    m_debt.assert_awaited_once_with("Вика", 2000, "u-1")


@pytest.mark.asyncio
async def test_tight_month_headroom_exhausted_all_to_debt():
    """Тяжёлый месяц, доход 2000₽, headroom уже исчерпан прошлым windfall в
    этом периоде → все 2000 идут в долг/подушку, не в Импульсивные."""
    budget = dict(_BUDGET_TIGHT, долги=[{"name": "Вика", "amount": 20000,
                  "deadline": "2026-12-01", "strategy": "", "monthly_payment": 1000}])
    with _apply(_patches(budget, impulse_bonus=3000.0)), \
         patch.object(finance, "_partial_debt_payment", AsyncMock(return_value=(18000, 0.0))) as m_debt:
        msg = await finance._distribute_windfall_income(2000, uid=1, user_id="u-1")

    assert "Импульсивные" not in msg
    assert "Долг Вика +2" in msg
    m_debt.assert_awaited_once_with("Вика", 2000, "u-1")


@pytest.mark.asyncio
async def test_normal_month_with_active_debt_all_to_debt():
    """Обычный месяц, есть активный долг → весь доход в долг."""
    with _apply(_patches(_BUDGET_NORMAL_WITH_DEBT)), \
         patch.object(finance, "_partial_debt_payment", AsyncMock(return_value=(12000, 0.0))) as m_debt, \
         patch("core.repos.pg_cushion_repo._repo.add_to_balance", AsyncMock()) as m_cushion:
        msg = await finance._distribute_windfall_income(8000, uid=1, user_id="u-1")

    assert "Импульсивные" not in msg
    assert "Долг Вика +8" in msg
    assert "осталось 12" in msg or "осталось 12,000" in msg or "12,000" in msg
    m_debt.assert_awaited_once_with("Вика", 8000, "u-1")
    m_cushion.assert_not_called()


@pytest.mark.asyncio
async def test_normal_month_no_debt_all_to_cushion():
    """Обычный месяц, долгов с платежом нет → весь доход в подушку."""
    with _apply(_patches(_BUDGET_NORMAL_NO_DEBT)), \
         patch("core.repos.pg_cushion_repo._repo.add_to_balance",
               AsyncMock(return_value=15000)) as m_cushion:
        msg = await finance._distribute_windfall_income(3000, uid=1, user_id="u-1")

    assert "Импульсивные" not in msg
    assert "Долг" not in msg
    assert "🛡️ Подушка +3" in msg
    m_cushion.assert_awaited_once()
    call = m_cushion.call_args
    assert call.args[0] == "u-1"
    assert call.args[1] == 3000
    assert call.kwargs.get("source") == "windfall_income"


@pytest.mark.asyncio
async def test_zero_or_negative_amount_returns_empty():
    with _apply(_patches(_BUDGET_NORMAL_NO_DEBT)):
        assert await finance._distribute_windfall_income(0, uid=1, user_id="u-1") == ""
        assert await finance._distribute_windfall_income(-100, uid=1, user_id="u-1") == ""


# ── handle_windfall_income: единая точка входа (из core/classifier) ──────────

def _msg():
    m = MagicMock()
    m.from_user.id = 42
    m.answer = AsyncMock()
    return m


@pytest.mark.parametrize("cat,title,expect", [
    ("💰 Зарплата", "зарплата", False),
    ("🔮 Практика", "расклад", False),
    ("🏠 Жильё", "аренда квартиры", False),
    ("🎁 Подарок", "подарок от бабушки", True),
    ("💳 Прочее", "вернули за телефон", True),
    ("💳 Прочее", "зарплата аванс", False),   # по описанию
])
def test_is_windfall_income(cat, title, expect):
    assert finance._is_windfall_income(cat, title) is expect


@pytest.mark.asyncio
async def test_handle_windfall_small_distributes_and_replies():
    msg = _msg()
    with patch.object(finance, "_distribute_windfall_income",
                      AsyncMock(return_value="💰 распределён: → 🛡️ Подушка +2,000₽")) as m_d:
        ok = await finance.handle_windfall_income(msg, 2000, "🎁 Подарок", "подарок", "u-1")
    assert ok is True
    m_d.assert_awaited_once_with(2000, 42, "u-1")
    assert "Подушка" in msg.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_handle_windfall_large_shows_manual_prompt():
    msg = _msg()
    with patch.object(finance, "_send_windfall_manual_prompt", AsyncMock()) as m_p, \
         patch.object(finance, "_distribute_windfall_income", AsyncMock()) as m_d:
        ok = await finance.handle_windfall_income(msg, 60000, "🎁 Подарок", "наследство", "u-1")
    assert ok is True
    m_p.assert_awaited_once()
    m_d.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_windfall_skips_salary():
    msg = _msg()
    with patch.object(finance, "_distribute_windfall_income", AsyncMock()) as m_d, \
         patch.object(finance, "_send_windfall_manual_prompt", AsyncMock()) as m_p:
        ok = await finance.handle_windfall_income(msg, 60000, "💰 Зарплата", "зарплата", "u-1")
    assert ok is False
    m_d.assert_not_awaited()
    m_p.assert_not_awaited()


@pytest.mark.asyncio
async def test_classifier_income_calls_windfall():
    """core/classifier.process_item на income → handle_windfall_income."""
    import core.classifier as clf
    msg = _msg()
    data = {"type": "income", "amount": 3000, "category": "🎁 Подарок",
            "title": "подарок", "confidence": "high", "source": "💳 Карта"}
    with patch.object(clf, "_fin_repo") as m_repo, \
         patch("nexus.handlers.finance.handle_windfall_income", AsyncMock()) as m_wf, \
         patch.object(clf, "react", AsyncMock()):
        m_repo.add = AsyncMock(return_value="fin-1")
        await clf.process_item(data, "подарок 3000", msg, {}, user_id="u-1")
    m_wf.assert_awaited_once()
    assert m_wf.await_args.args[1] == 3000
