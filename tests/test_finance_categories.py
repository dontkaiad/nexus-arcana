"""tests/test_finance_categories.py — консолидация FINANCE_CATEGORIES (#4).

4 теста:
  1. FINANCE_CATEGORIES == dedup(EXPENSE + INCOME + ARCANA), дублей нет
  2. Bug 1 регрессия: доходные категории Nexus ∈ FINANCE_CATEGORIES
  3. Доменная граница: ARCANA_CATEGORIES ∈ FINANCE_CATEGORIES, но НЕ ∈ EXPENSE, НЕ ∈ INCOME
  4. finance.py CATEGORIES is FINANCE_CATEGORIES — один источник
"""
from __future__ import annotations


def test_finance_categories_is_dedup_union():
    """FINANCE_CATEGORIES == dedup(EXPENSE + INCOME + ARCANA), дублей нет."""
    from core.config import EXPENSE_CATEGORIES, INCOME_CATEGORIES, ARCANA_CATEGORIES, FINANCE_CATEGORIES

    seen: set = set()
    expected: list = []
    for cat in EXPENSE_CATEGORIES + INCOME_CATEGORIES + ARCANA_CATEGORIES:
        if cat not in seen:
            seen.add(cat)
            expected.append(cat)

    assert FINANCE_CATEGORIES == expected, "FINANCE_CATEGORIES должна быть dedup(EXPENSE+INCOME+ARCANA)"
    assert len(FINANCE_CATEGORIES) == len(set(FINANCE_CATEGORIES)), "дублей быть не должно"


def test_bug1_income_categories_visible_to_llm():
    """Bug 1 fix: все доходные категории Nexus видны LLM-парсеру через FINANCE_CATEGORIES."""
    from core.config import FINANCE_CATEGORIES

    for cat in ("💼 Фриланс", "🎁 Подарок", "💵 Возврат/кэшбэк", "💱 Продажа"):
        assert cat in FINANCE_CATEGORIES, f"{cat!r} должна быть в FINANCE_CATEGORIES"


def test_arcana_domain_boundary():
    """ARCANA_CATEGORIES ∈ FINANCE_CATEGORIES (LLM-вселенная), но НЕ ∈ EXPENSE и НЕ ∈ INCOME."""
    from core.config import EXPENSE_CATEGORIES, INCOME_CATEGORIES, ARCANA_CATEGORIES, FINANCE_CATEGORIES

    for cat in ARCANA_CATEGORIES:
        assert cat in FINANCE_CATEGORIES, f"{cat!r} должна быть в FINANCE_CATEGORIES"
        assert cat not in EXPENSE_CATEGORIES, f"{cat!r} не должна быть в EXPENSE_CATEGORIES (доменная граница)"
        assert cat not in INCOME_CATEGORIES, f"{cat!r} не должна быть в INCOME_CATEGORIES (доменная граница)"


def test_finance_handler_categories_is_config_finance_categories():
    """Bug 3 fix: finance.py CATEGORIES — это тот же объект что и config.FINANCE_CATEGORIES."""
    from core.config import FINANCE_CATEGORIES
    import nexus.handlers.finance as fin_mod

    assert fin_mod.CATEGORIES is FINANCE_CATEGORIES, \
        "CATEGORIES в finance.py должна быть алиасом FINANCE_CATEGORIES из config, не копией"
