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


def test_prompt_json_schema_has_already_spent():
    assert '"already_spent"' in BUDGET_SONNET_SYSTEM


def test_format_plan_shows_already_spent_when_positive():
    out = _format_plan({"already_spent": 12000, "income_total": 100000})
    assert "📤 Уже потрачено в этом периоде: 12,000₽" in out


def test_format_plan_hides_already_spent_when_zero_or_missing():
    assert "Уже потрачено" not in _format_plan({"already_spent": 0, "income_total": 100000})
    assert "Уже потрачено" not in _format_plan({"income_total": 100000})
