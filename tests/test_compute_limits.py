"""tests/test_compute_limits.py — детерминированная арифметика лимитов + взнос в подушку.

core/budget.py::compute_limits — чистая функция, без LLM. Возвращает
{"limits": {категория: рубли}, "cushion_contribution": int}. Проверяем конкретные
числа, инвариант «сумма лимитов == discretionary после вычета подушки» и
динамический взнос (20% дохода в комфортный месяц / 0 в тяжёлый).
"""
import pytest

from core.budget import (
    compute_limits,
    CUSHION_COMFORTABLE_RATE,
    CAT_TRANSPORT,
    CAT_IMPULSE,
    CAT_PRODUCTS,
    CAT_HABITS,
    PRIORITY_CHAIN,
    LIMIT_CATEGORIES,
)


def _limits(pool, debt=0, income=0):
    return compute_limits(pool, debt, income)["limits"]


def _sum(pool, debt=0, income=0):
    return sum(_limits(pool, debt, income).values())


def _cushion(pool, debt=0, income=0):
    return compute_limits(pool, debt, income)["cushion_contribution"]


# ── Распределение лимитов (без дохода → без подушки) ────────────────────────

def test_real_today_case_15350():
    lim = _limits(15350, 0)
    assert lim[CAT_PRODUCTS] == 6425
    assert lim[CAT_HABITS] == 6425
    assert lim[CAT_TRANSPORT] == 1500
    assert lim[CAT_IMPULSE] == 1000
    for cat in PRIORITY_CHAIN:
        assert lim[cat] == 0
    assert _sum(15350, 0) == 15350
    assert _cushion(15350, 0) == 0


def test_comfortable_case_50000():
    lim = _limits(50000, 0)
    assert lim[CAT_TRANSPORT] == 1500
    assert lim[CAT_IMPULSE] == 1000
    assert lim[CAT_PRODUCTS] == 10000
    assert lim[CAT_HABITS] == 13000
    chain = [lim[c] for c in PRIORITY_CHAIN]
    assert sum(chain) == 24500
    for a, b in zip(chain, chain[1:]):
        assert a >= b
    assert chain[0] == 12250
    assert _sum(50000, 0) == 50000


def test_extreme_case_1500():
    lim = _limits(1500, 0)
    assert lim[CAT_TRANSPORT] == 900
    assert lim[CAT_IMPULSE] == 600
    assert lim[CAT_PRODUCTS] == 0 and lim[CAT_HABITS] == 0
    assert _sum(1500, 0) == 1500
    assert _cushion(1500, 0) == 0


def test_debt_payment_reduces_discretionary():
    lim = _limits(65350, 50000)
    assert lim[CAT_PRODUCTS] == 6425 and lim[CAT_HABITS] == 6425
    assert _sum(65350, 50000) == 15350


def test_non_positive_discretionary_all_zero():
    for pool, debt in [(0, 0), (10000, 10000), (5000, 9000), (-100, 0)]:
        assert _sum(pool, debt) == 0
        assert _cushion(pool, debt) == 0


def test_habits_ceiling_leftover_flows_to_chain():
    lim = _limits(200000, 0)
    assert lim[CAT_PRODUCTS] == 10000
    assert lim[CAT_HABITS] == 13000
    assert _sum(200000, 0) == 200000


@pytest.mark.parametrize("pool", [
    2500, 2501, 3000, 12849, 12850, 12851, 15350, 22499, 25499, 25500, 25501,
    30001, 47777, 99999, 100000, 123457, 1_000_001,
])
def test_sum_exactly_equals_discretionary(pool):
    lim = _limits(pool, 0)
    assert sum(lim.values()) == round(pool)
    assert all(isinstance(v, int) and v >= 0 for v in lim.values())


@pytest.mark.parametrize("pool,debt", [
    (40000, 7500), (55123, 12345), (18000, 2500), (250000, 33333),
])
def test_sum_invariant_with_debt(pool, debt):
    assert _sum(pool, debt) == round(pool - debt)


def test_returns_all_categories_always():
    lim = _limits(0, 0)
    assert set(lim.keys()) == set(LIMIT_CATEGORIES)
    assert len(LIMIT_CATEGORIES) == 9


# ── Динамический взнос в подушку ───────────────────────────────────────────

def test_cushion_reserved_in_comfortable_month():
    """pool_after_iron ≥ 23000 + доход → 20% дохода в подушку, лимиты от
    уменьшенного пула."""
    res = compute_limits(50000, 0, income_total=100000)
    assert res["cushion_contribution"] == 20000  # 100000 * 0.20
    assert sum(res["limits"].values()) == 50000 - 20000
    assert CUSHION_COMFORTABLE_RATE == 0.20


def test_no_cushion_in_tight_month():
    """pool_after_iron < 23000 → cushion_contribution = 0 даже при доходе."""
    res = compute_limits(15350, 0, income_total=100000)
    assert res["cushion_contribution"] == 0
    assert sum(res["limits"].values()) == 15350


def test_no_cushion_without_income():
    res = compute_limits(50000, 0)  # income_total не передан
    assert res["cushion_contribution"] == 0
    assert sum(res["limits"].values()) == 50000


def test_cushion_plus_limits_equals_original_discretionary():
    """Комфортный месяц: cushion + сумма лимитов == distributable − debt."""
    res = compute_limits(80000, 10000, income_total=120000)
    assert res["cushion_contribution"] == 24000
    assert res["cushion_contribution"] + sum(res["limits"].values()) == 70000


def test_comfortable_threshold_is_pool_after_iron_23000():
    """Граница: discretionary 25500 = pool_after_iron ровно 23000 → комфортный."""
    assert compute_limits(25500, 0, income_total=50000)["cushion_contribution"] == 10000
    assert compute_limits(25499, 0, income_total=50000)["cushion_contribution"] == 0


def test_cushion_never_drives_limits_negative():
    """Огромный доход при скромном пуле — подушка не больше самого пула."""
    res = compute_limits(26000, 0, income_total=10_000_000)
    assert res["cushion_contribution"] <= 26000
    assert sum(res["limits"].values()) >= 0
    assert res["cushion_contribution"] + sum(res["limits"].values()) == 26000
