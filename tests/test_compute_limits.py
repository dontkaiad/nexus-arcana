"""tests/test_compute_limits.py — детерминированная арифметика лимитов.

core/budget.py::compute_limits — чистая функция, без LLM. Проверяем конкретные
числа и инвариант «сумма всех категорий == discretionary» без копеечного дрейфа.
"""
import pytest

from core.budget import (
    compute_limits,
    CAT_TRANSPORT,
    CAT_IMPULSE,
    CAT_PRODUCTS,
    CAT_HABITS,
    PRIORITY_CHAIN,
    LIMIT_CATEGORIES,
)


def _sum(limits):
    return sum(limits.values())


def test_real_today_case_15350():
    """Реальный сегодняшний случай: pool_after_iron < 23000 → продукты/привычки
    делят остаток пополам, цепочка приоритета = 0."""
    limits = compute_limits(15350, 0)
    assert limits[CAT_PRODUCTS] == 6425
    assert limits[CAT_HABITS] == 6425
    assert limits[CAT_TRANSPORT] == 1500
    assert limits[CAT_IMPULSE] == 1000
    for cat in PRIORITY_CHAIN:
        assert limits[cat] == 0
    assert _sum(limits) == 15350


def test_comfortable_case_50000():
    """pool_after_iron = 47500 ≥ 23000 → продукты=10000, привычки=13000,
    остаток 24500 распределён по приоритету убывающе."""
    limits = compute_limits(50000, 0)
    assert limits[CAT_TRANSPORT] == 1500
    assert limits[CAT_IMPULSE] == 1000
    assert limits[CAT_PRODUCTS] == 10000
    assert limits[CAT_HABITS] == 13000

    chain_amounts = [limits[c] for c in PRIORITY_CHAIN]
    assert sum(chain_amounts) == 24500
    # Убывание по цепочке приоритета (кроме возможного равенства на хвосте).
    for a, b in zip(chain_amounts, chain_amounts[1:]):
        assert a >= b
    assert chain_amounts[0] == 12250  # первая забирает половину остатка
    assert _sum(limits) == 50000


def test_extreme_case_1500():
    """discretionary < 2500 → транспорт/импульсивные урезаны пропорционально,
    в отрицательные числа не уходим, всё остальное 0."""
    limits = compute_limits(1500, 0)
    assert limits[CAT_TRANSPORT] == 900   # 1500 * 1500/2500
    assert limits[CAT_IMPULSE] == 600     # остаток
    assert limits[CAT_PRODUCTS] == 0
    assert limits[CAT_HABITS] == 0
    for cat in PRIORITY_CHAIN:
        assert limits[cat] == 0
    assert _sum(limits) == 1500


def test_debt_payment_reduces_discretionary():
    """total_debt_payment вычитается из distributable_pool до расчёта."""
    limits = compute_limits(65350, 50000)
    # discretionary = 15350 → тот же результат, что и test_real_today_case
    assert limits[CAT_PRODUCTS] == 6425
    assert limits[CAT_HABITS] == 6425
    assert _sum(limits) == 15350


def test_non_positive_discretionary_all_zero():
    for pool, debt in [(0, 0), (10000, 10000), (5000, 9000), (-100, 0)]:
        limits = compute_limits(pool, debt)
        assert _sum(limits) == 0
        assert all(v == 0 for v in limits.values())


def test_habits_ceiling_leftover_flows_to_chain():
    """Огромный пул: привычки упираются в потолок 13000, всё сверх — в цепочку."""
    limits = compute_limits(200000, 0)
    assert limits[CAT_PRODUCTS] == 10000
    assert limits[CAT_HABITS] == 13000
    assert _sum(limits) == 200000


@pytest.mark.parametrize("pool", [
    2500, 2501, 3000, 12849, 12850, 12851, 15350, 22499, 25499, 25500, 25501,
    30001, 47777, 99999, 100000, 123457, 1_000_001,
])
def test_sum_exactly_equals_discretionary(pool):
    """Инвариант: сумма всех категорий ТОЧНО равна discretionary на любых входах,
    без копеечных расхождений от round()."""
    limits = compute_limits(pool, 0)
    assert _sum(limits) == round(pool)
    assert all(isinstance(v, int) and v >= 0 for v in limits.values())


@pytest.mark.parametrize("pool,debt", [
    (40000, 7500), (55123, 12345), (18000, 2500), (250000, 33333),
])
def test_sum_invariant_with_debt(pool, debt):
    limits = compute_limits(pool, debt)
    assert _sum(limits) == round(pool - debt)


def test_returns_all_categories_always():
    limits = compute_limits(0, 0)
    assert set(limits.keys()) == set(LIMIT_CATEGORIES)
    assert len(LIMIT_CATEGORIES) == 9
