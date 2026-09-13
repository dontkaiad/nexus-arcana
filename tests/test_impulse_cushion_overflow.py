"""tests/test_impulse_cushion_overflow.py — перерасход агрегатного лимита
("🏠 Бюджет на жизнь" / "🚬 Привычки" — #259) списывается из подушки.

История: #258 сначала съедал overflow остатком лимита 🎲 Импульсивные, потом
из подушки. #259 убрал отдельный лимит Импульсивные вообще (слит в "Бюджет
на жизнь") — механизм упростился до "перерасход агрегата → сразу подушка".
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
async def test_life_budget_overflow_spills_into_cushion():
    """Трата "🍜 Продукты" толкает СУММУ всех Бюджет-на-жизнь категорий за
    лимит — overflow целиком списывается из подушки (нет больше отдельного
    импульсивного буфера, который бы его сначала поглощал)."""
    msg = _msg()
    rec_products = MagicMock(amount=6000.0, category="🍜 Продукты")
    rec_cafe = MagicMock(amount=1200.0, category="🍱 Кафе/Доставка")
    with patch.object(finance, "_get_limits", AsyncMock(return_value={"бюджет на жизнь": 5000.0})), \
         patch.object(finance._repo, "query_records", AsyncMock(return_value=[rec_products, rec_cafe])), \
         patch.object(finance, "_get_payday", AsyncMock(return_value=1)), \
         patch("core.repos.pg_cushion_repo._repo.add_to_balance", AsyncMock(return_value=11800.0)) as m_cushion:
        await finance._check_budget_limit("🍜 Продукты", msg, "u-1", amount=6000)

    m_cushion.assert_awaited_once()
    args, kwargs = m_cushion.await_args
    assert args[0] == "u-1"
    assert args[1] == -2200.0  # (6000+1200) - 5000 = 2200 перерасход
    out = " ".join(c.args[0] for c in msg.answer.await_args_list if c.args)
    assert "2,200₽" in out or "2200₽" in out
    assert "🏠 Бюджет на жизнь" in out


@pytest.mark.asyncio
async def test_life_budget_within_limit_no_cushion_touch():
    msg = _msg()
    rec = MagicMock(amount=3000.0, category="🍜 Продукты")
    with patch.object(finance, "_get_limits", AsyncMock(return_value={"бюджет на жизнь": 5000.0})), \
         patch.object(finance._repo, "query_records", AsyncMock(return_value=[rec])), \
         patch.object(finance, "_get_payday", AsyncMock(return_value=1)), \
         patch("core.repos.pg_cushion_repo._repo.add_to_balance", AsyncMock()) as m_cushion:
        await finance._check_budget_limit("🍜 Продукты", msg, "u-1", amount=3000)

    m_cushion.assert_not_awaited()


@pytest.mark.asyncio
async def test_habits_overflow_uses_calendar_week_not_full_period():
    """Привычки проверяются по календарной неделе, не по всему платёжному
    периоду — трата ВНЕ этой недели (пришедшая в query_records по более
    широкому окну, если бы окно было period-wide) не должна учитываться."""
    msg = _msg()
    rec_this_week = MagicMock(amount=1500.0, category="🚬 Привычки")
    with patch.object(finance, "_get_limits", AsyncMock(return_value={"привычки": 1000.0})), \
         patch.object(finance._repo, "query_records", AsyncMock(return_value=[rec_this_week])) as m_query, \
         patch("core.repos.pg_cushion_repo._repo.add_to_balance", AsyncMock(return_value=8500.0)) as m_cushion:
        await finance._check_budget_limit("🚬 Привычки", msg, "u-1", amount=1500)

    # окно запроса — понедельник этой недели, не начало платёжного периода
    date_from = m_query.await_args.kwargs["date_from"]
    from core.budget import calendar_week_start_iso
    assert date_from == calendar_week_start_iso(3)
    m_cushion.assert_awaited_once()
    args, _ = m_cushion.await_args
    assert args[1] == -500.0  # 1500 - 1000 перерасход


@pytest.mark.asyncio
async def test_manual_override_limit_on_single_life_category_still_specific():
    """Если Кай вручную ставит лимит на ОДНУ категорию (например «лимит кафе
    5000»), она проверяется отдельно от агрегата "Бюджет на жизнь", как и
    раньше — специфичный факт побеждает агрегатный."""
    msg = _msg()
    rec_cafe = MagicMock(amount=6000.0, category="🍱 Кафе/Доставка")
    rec_products = MagicMock(amount=100.0, category="🍜 Продукты")
    with patch.object(finance, "_get_limits", AsyncMock(
             return_value={"бюджет на жизнь": 50000.0, "кафе": 5000.0})), \
         patch.object(finance._repo, "query_records", AsyncMock(return_value=[rec_cafe, rec_products])), \
         patch.object(finance, "_get_payday", AsyncMock(return_value=1)), \
         patch("core.repos.pg_cushion_repo._repo.add_to_balance", AsyncMock(return_value=9000.0)) as m_cushion:
        await finance._check_budget_limit("🍱 Кафе/Доставка", msg, "u-1", amount=6000)

    # 6000 (только кафе, НЕ 6000+100 продуктов) - 5000 = 1000 перерасход
    m_cushion.assert_awaited_once()
    args, _ = m_cushion.await_args
    assert args[1] == -1000.0
