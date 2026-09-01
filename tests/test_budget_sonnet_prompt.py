"""tests/test_budget_sonnet_prompt.py — наличие инструкций в BUDGET_SONNET_SYSTEM.

Это промпт для Sonnet, не детерминированный код — проверяем только что нужные
инструкции присутствуют в тексте, не арифметику самого Sonnet.
"""
from __future__ import annotations

from nexus.handlers.finance import BUDGET_SONNET_SYSTEM, _format_plan


def test_prompt_has_step_1_5_subtract_spending():
    """Подшаг 1.5: уже потраченное в периоде вычитается из Распределяемых."""
    assert "Шаг 1.5" in BUDGET_SONNET_SYSTEM
    assert "spending_by_category" in BUDGET_SONNET_SYSTEM
    # именно вычитание из распределяемых, до долгов/лимитов
    assert "Распределяемые = Распределяемые − сумма всех значений spending_by_category" in BUDGET_SONNET_SYSTEM
    assert BUDGET_SONNET_SYSTEM.index("Шаг 1.5") < BUDGET_SONNET_SYSTEM.index("Шаг 2:")


def test_prompt_has_step_1_6_add_last_savings():
    """Подшаг 1.6: экономия прошлого периода прибавляется к Распределяемым."""
    assert "Шаг 1.6" in BUDGET_SONNET_SYSTEM
    assert "Распределяемые = Распределяемые + savings_from_last_period" in BUDGET_SONNET_SYSTEM
    # после 1.5, до долгов
    assert BUDGET_SONNET_SYSTEM.index("Шаг 1.5") < BUDGET_SONNET_SYSTEM.index("Шаг 1.6") < BUDGET_SONNET_SYSTEM.index("Шаг 2:")


def test_prompt_json_schema_has_already_spent():
    assert '"already_spent"' in BUDGET_SONNET_SYSTEM
    assert '"savings_from_last_period"' in BUDGET_SONNET_SYSTEM


def test_format_plan_shows_already_spent_when_positive():
    out = _format_plan({"already_spent": 12000, "income_total": 100000})
    assert "📤 Уже потрачено в этом периоде: 12,000₽" in out


def test_format_plan_hides_already_spent_when_zero_or_missing():
    assert "Уже потрачено" not in _format_plan({"already_spent": 0, "income_total": 100000})
    assert "Уже потрачено" not in _format_plan({"income_total": 100000})


def test_format_plan_distributable_subtracts_already_spent():
    """'Распределяемые' в выводе = доход - фикс - already_spent (цифры бьются с низом)."""
    plan = {
        "income_total": 100000,
        "fixed": [{"name": "аренда", "category": "🏠 Жильё", "amount": 30000}],
        "fixed_total": 30000,
        "already_spent": 12000,
    }
    out = _format_plan(plan)
    # 100000 - 30000 - 12000 = 58000
    assert "💳 Распределяемые: <b>58,000₽</b>" in out
    assert "70,000₽" not in out  # старое поведение (без вычета) не должно светиться


def test_format_plan_distributable_adds_last_savings():
    """'Распределяемые' = доход - фикс - already_spent + savings_from_last_period."""
    plan = {
        "income_total": 100000,
        "fixed": [{"name": "аренда", "category": "🏠 Жильё", "amount": 30000}],
        "fixed_total": 30000,
        "already_spent": 12000,
        "savings_from_last_period": 5000,
    }
    out = _format_plan(plan)
    assert "🛡️ Экономия с прошлого периода: +5,000₽" in out
    # 100000 - 30000 - 12000 + 5000 = 63000
    assert "💳 Распределяемые: <b>63,000₽</b>" in out


def test_format_plan_hides_last_savings_when_zero_or_missing():
    assert "Экономия с прошлого периода" not in _format_plan(
        {"income_total": 100000, "savings_from_last_period": 0})
    assert "Экономия с прошлого периода" not in _format_plan({"income_total": 100000})
