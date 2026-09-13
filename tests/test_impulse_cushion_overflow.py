"""tests/test_impulse_cushion_overflow.py — перерасход категории списывается
сначала из импульсивных, а то, что не влезло — из подушки (#258).

Раньше "то, что не влезло" просто дописывалось в импульсивные без потолка
(лимит импульсивных мог уйти сколь угодно далеко в минус), а из подушки
ничего не списывалось вообще — этого шага не было в коде.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nexus.handlers import finance


def _msg():
    msg = MagicMock()
    msg.from_user.id = 7
    msg.answer = AsyncMock()
    return msg


@pytest.mark.asyncio
async def test_overflow_fits_entirely_in_impulse_no_cushion_touch():
    """Перерасход 200₽, у импульсивных есть 800₽ места — всё уходит туда,
    подушка не трогается."""
    msg = _msg()
    rec = MagicMock(amount=5200.0)  # period_total = 5200, limit 5000 → over=200
    with patch.object(finance, "_get_limits", AsyncMock(return_value={"продукты": 5000.0})), \
         patch.object(finance._repo, "query_records", AsyncMock(return_value=[rec])), \
         patch.object(finance, "_get_payday", AsyncMock(return_value=1)), \
         patch.object(finance, "_calc_impulse_status", AsyncMock(return_value=(1000.0, 200.0))), \
         patch.object(finance, "_handle_impulse_overflow", AsyncMock()) as m_impulse, \
         patch("core.repos.pg_cushion_repo._repo.add_to_balance", AsyncMock()) as m_cushion:
        await finance._check_budget_limit("🍜 Продукты", msg, "u-1", amount=200)

    m_impulse.assert_awaited_once()
    assert m_impulse.await_args.args[1] == 200.0  # весь overflow ушёл в импульсивные
    m_cushion.assert_not_awaited()


@pytest.mark.asyncio
async def test_overflow_exceeds_impulse_room_spills_into_cushion():
    """Перерасход 2000₽, у импульсивных остаётся только 200₽ места —
    200₽ в импульсивные, 1800₽ списывается из подушки."""
    msg = _msg()
    rec = MagicMock(amount=7000.0)  # limit 5000 → over=2000
    with patch.object(finance, "_get_limits", AsyncMock(return_value={"продукты": 5000.0})), \
         patch.object(finance._repo, "query_records", AsyncMock(return_value=[rec])), \
         patch.object(finance, "_get_payday", AsyncMock(return_value=1)), \
         patch.object(finance, "_calc_impulse_status", AsyncMock(return_value=(1000.0, 800.0))), \
         patch.object(finance, "_handle_impulse_overflow", AsyncMock()) as m_impulse, \
         patch("core.repos.pg_cushion_repo._repo.add_to_balance", AsyncMock(return_value=12200.0)) as m_cushion:
        await finance._check_budget_limit("🍜 Продукты", msg, "u-1", amount=2000)

    m_impulse.assert_awaited_once()
    assert m_impulse.await_args.args[1] == 200.0  # то, что влезло в импульсивные
    m_cushion.assert_awaited_once()
    args, kwargs = m_cushion.await_args
    assert args[0] == "u-1"
    assert args[1] == -1800.0  # то, что не влезло — списано из подушки
    out = " ".join(c.args[0] for c in msg.answer.await_args_list if c.args)
    assert "1,800₽" in out or "1800₽" in out
    assert "12,200₽" in out or "12200₽" in out


@pytest.mark.asyncio
async def test_no_impulse_reserve_no_cushion_touch():
    """Нет лимита на импульсивные вообще (impulse_limit=0) — старое поведение,
    подушка не трогается (нечем сравнивать, не наша забота чинить здесь)."""
    msg = _msg()
    rec = MagicMock(amount=6000.0)
    with patch.object(finance, "_get_limits", AsyncMock(return_value={"продукты": 5000.0})), \
         patch.object(finance._repo, "query_records", AsyncMock(return_value=[rec])), \
         patch.object(finance, "_get_payday", AsyncMock(return_value=1)), \
         patch.object(finance, "_calc_impulse_status", AsyncMock(return_value=(0.0, 0.0))), \
         patch.object(finance, "_handle_impulse_overflow", AsyncMock()) as m_impulse, \
         patch("core.repos.pg_cushion_repo._repo.add_to_balance", AsyncMock()) as m_cushion:
        await finance._check_budget_limit("🍜 Продукты", msg, "u-1", amount=1000)

    m_impulse.assert_not_awaited()
    m_cushion.assert_not_awaited()
