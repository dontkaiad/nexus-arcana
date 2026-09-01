"""tests/test_budget_sonnet_prompt.py — наличие инструкций в BUDGET_SONNET_SYSTEM.

Это промпт для Sonnet, не детерминированный код — проверяем только что нужные
инструкции присутствуют в тексте, не арифметику самого Sonnet.
"""
from __future__ import annotations

from nexus.handlers.finance import (
    BUDGET_SONNET_SYSTEM,
    _BUDGET_PARSE_PROMPT_LEGACY,
    _BUDGET_VARIABLE_CATS,
    _format_plan,
)

_BOTH_PROMPTS = {
    "BUDGET_SONNET_SYSTEM": BUDGET_SONNET_SYSTEM,
    "_BUDGET_PARSE_PROMPT_LEGACY": _BUDGET_PARSE_PROMPT_LEGACY,
}
_PRIORITY_ORDER = ["Кафе/Доставка", "Бьюти", "Здоровье", "Гардероб", "Хобби/Учеба"]


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


# ── Привычки: потолок 50% + приоритетный дележ остатка ──────────────────────

import pytest


@pytest.mark.parametrize("name,prompt", _BOTH_PROMPTS.items())
def test_habits_cap_50_percent_described(name, prompt):
    """Привычки ≤ discretionary_pool × 0.5 — процент, не абсолют. Оба промпта."""
    assert "discretionary_pool" in prompt, name
    assert "× 0.5" in prompt, name
    assert "ПОТОЛОК" in prompt, name
    # products_min правило не трогали
    assert "max(3000, привычки / 2)" in prompt, name


@pytest.mark.parametrize("name,prompt", _BOTH_PROMPTS.items())
def test_priority_split_order(name, prompt):
    """Остаток пула делится по фиксированному приоритету в правильном порядке."""
    positions = [prompt.find(cat) for cat in _PRIORITY_ORDER]
    assert all(p != -1 for p in positions), f"{name}: не все категории приоритета найдены"
    assert positions == sorted(positions), f"{name}: порядок приоритета нарушен"


@pytest.mark.parametrize("name,prompt", _BOTH_PROMPTS.items())
def test_priority_split_in_both_normal_and_tight(name, prompt):
    """Правило описано и в общем распределении, и в варианте А тяжёлого месяца
    (два места, как было с бьюти — дублируются намеренно)."""
    assert prompt.count("discretionary_pool") >= 2, name
    # приоритетный список встречается минимум дважды (обычный + вариант А)
    assert prompt.count("🍱 Кафе/Доставка") >= 2, name


@pytest.mark.parametrize("name,prompt", _BOTH_PROMPTS.items())
def test_budget_prompts_do_not_name_arcana_categories(name, prompt):
    """🔮 Практика и 🕯️ Расходники — категории Арканы, НЕ личного бюджета.
    В шаблонах промптов их быть не должно (утечка полного CATEGORIES в
    рантайм-контекст задокументирована отдельно в docs/specs/BUDGET.md)."""
    assert "Практика" not in prompt, name
    assert "Расходники" not in prompt, name


def test_budget_variable_cats_has_no_arcana():
    assert "🔮 Практика" not in _BUDGET_VARIABLE_CATS
    assert "🕯️ Расходники" not in _BUDGET_VARIABLE_CATS
    assert len(_BUDGET_VARIABLE_CATS) == 8


# ── Вариант Б только при реальном платеже по долгу ──────────────────────────

@pytest.mark.parametrize("name,prompt", _BOTH_PROMPTS.items())
def test_fork_requires_active_debt_payment(name, prompt):
    """total_debt_payment == 0 в тяжёлый месяц → один план, без А/Б."""
    assert "total_debt_payment == 0" in prompt, name
    assert "total_debt_payment > 0" in prompt, name
