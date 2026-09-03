"""tests/test_budget_fixed_onetime_match.py

Постоянные и разовые расходы — обычные категории лимита (🔒 Фикс / 📦 Разовые).
При трате classify() обогащается списком известных позиций из Памяти и, если
описание похоже, ставит category='🔒 Фикс'/'📦 Разовые' вместо обычной.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core import classifier as clf


def _mem(key: str, fact: str):
    return MagicMock(key=key, fact=fact, is_current=True, id=key)


# ── Обогащение промпта classify известными позициями ───────────────────────

@pytest.mark.asyncio
async def test_classify_prompt_enriched_with_known_positions():
    mems = [
        _mem("постоянно_жильё_квартира", "постоянно: квартира (🏠 Жильё) — 30000₽/мес"),
        _mem("разовый_коммуналка_гай", "разовое: коммуналка Гай — 16000₽"),
    ]
    captured = {}

    async def fake_ask(text, system="", **kw):
        captured["system"] = system
        return '{"type":"expense","amount":8000,"category":"📦 Разовые","source":"💳 Карта","confidence":"high"}'

    with patch("core.repos.memory_repo._repo.find_by_key_prefixes", AsyncMock(return_value=mems)), \
         patch.object(clf, "ask_claude", side_effect=fake_ask):
        res = await clf.classify("коммуналка гай 8к", user_notion_id="u-1")

    assert res[0]["category"] == "📦 Разовые"
    sys = captured["system"]
    assert "СОПОСТАВЛЕНИЕ ТРАТЫ С ИЗВЕСТНЫМИ РАСХОДАМИ" in sys
    assert "коммуналка Гай — 16000₽" in sys
    assert "квартира — 30000₽" in sys
    assert "🔒 Фикс" in sys and "📦 Разовые" in sys
    # повторная трата по той же позиции — снова матчить
    assert "СНОВА та же категория" in sys


@pytest.mark.asyncio
async def test_classify_repeat_expense_same_position_still_matches():
    """Тот же факт всё ещё в Памяти → промпт снова содержит позицию (не «уже потрачено»)."""
    mems = [_mem("разовый_коммуналка_гай", "разовое: коммуналка Гай — 16000₽")]
    seen = []

    async def fake_ask(text, system="", **kw):
        seen.append(system)
        return '{"type":"expense","amount":10000,"category":"📦 Разовые","source":"💳 Карта","confidence":"high"}'

    with patch("core.repos.memory_repo._repo.find_by_key_prefixes", AsyncMock(return_value=mems)), \
         patch.object(clf, "ask_claude", side_effect=fake_ask):
        await clf.classify("коммуналка гай 8к", user_notion_id="u-1")
        await clf.classify("коммуналка гай 10к", user_notion_id="u-1")

    assert all("коммуналка Гай" in s for s in seen)
    assert len(seen) == 2


@pytest.mark.asyncio
async def test_classify_no_positions_prompt_not_enriched():
    """Нет известных позиций → блок сопоставления в промпт не добавляется, регресс не сломан."""
    captured = {}

    async def fake_ask(text, system="", **kw):
        captured["system"] = system
        return '{"type":"expense","amount":500,"category":"🚕 Транспорт","source":"💳 Карта","confidence":"high"}'

    with patch("core.repos.memory_repo._repo.find_by_key_prefixes", AsyncMock(return_value=[])), \
         patch.object(clf, "ask_claude", side_effect=fake_ask):
        res = await clf.classify("такси 500", user_notion_id="u-1")

    assert res[0]["category"] == "🚕 Транспорт"
    assert "СОПОСТАВЛЕНИЕ ТРАТЫ С ИЗВЕСТНЫМИ РАСХОДАМИ" not in captured["system"]


@pytest.mark.asyncio
async def test_classify_without_notion_id_skips_memory_lookup():
    """Без user_notion_id — не ходим в Память вообще."""
    m_prefix = AsyncMock(return_value=[])

    async def fake_ask(text, system="", **kw):
        return '{"type":"expense","amount":500,"category":"🚕 Транспорт","source":"💳 Карта","confidence":"high"}'

    with patch("core.repos.memory_repo._repo.find_by_key_prefixes", m_prefix), \
         patch.object(clf, "ask_claude", side_effect=fake_ask):
        await clf.classify("такси 500")

    m_prefix.assert_not_called()


@pytest.mark.asyncio
async def test_known_budget_positions_parses_names_and_amounts():
    mems = [
        _mem("постоянно_жильё_квартира", "постоянно: квартира (🏠 Жильё) — 30000₽/мес"),
        _mem("разовый_коммуналка_гай", "разовое: коммуналка Гай — 16000₽"),
        MagicMock(key="разовый_старьё", fact="разовое: старьё — 1000₽", is_current=False, id="x"),
    ]
    with patch("core.repos.memory_repo._repo.find_by_key_prefixes", AsyncMock(return_value=mems)):
        fixed, one_time = await clf._known_budget_positions("u-1")

    assert fixed == ["квартира — 30000₽"]
    assert one_time == ["коммуналка Гай — 16000₽"]  # неактивный факт отброшен


# ── get_limits / _check_budget_limit находят 🔒 Фикс / 📦 Разовые ───────────

@pytest.mark.asyncio
async def test_get_limits_reads_fixed_and_one_time_categories():
    from core.budget import get_limits

    mems = [
        MagicMock(fact="лимит: 🔒 Фикс — 35000₽/мес", related_to=""),
        MagicMock(fact="лимит: 📦 Разовые — 18500₽/мес", related_to=""),
        MagicMock(fact="лимит: 🍜 Продукты — 10000₽/мес", related_to=""),
    ]
    with patch("core.repos.memory_repo._repo.find_by_category", AsyncMock(return_value=mems)):
        limits = await get_limits()

    assert limits["фикс"] == 35000
    assert limits["разовые"] == 18500
    assert limits["продукты"] == 10000


def test_display_limit_name_maps_fixed_and_one_time():
    from core.budget import display_limit_name
    assert display_limit_name("лимит_фикс") == "🔒 Фикс"
    assert display_limit_name("разовые") == "📦 Разовые"


@pytest.mark.asyncio
async def test_check_budget_limit_matches_one_time_category():
    """Трата с category='📦 Разовые' → _check_budget_limit находит лимит_разовые."""
    from nexus.handlers import finance

    msg = MagicMock()
    msg.from_user.id = 7
    msg.answer = AsyncMock()

    rec = MagicMock(amount=8000.0)
    with patch.object(finance, "_get_limits", AsyncMock(return_value={"разовые": 16000.0})), \
         patch.object(finance._repo, "query_records", AsyncMock(return_value=[rec])), \
         patch.object(finance, "_get_payday", AsyncMock(return_value=1)):
        await finance._check_budget_limit("📦 Разовые", msg, "u-1", amount=8000)

    # что-то ответил (прогресс по лимиту), не свалился в «нет лимита»
    assert msg.answer.await_count >= 1
    out = " ".join(c.args[0] for c in msg.answer.await_args_list if c.args)
    assert "Разовые" in out or "разов" in out.lower()


# ── Строка «📋 Долги» в чеке после траты — только при активном платеже ──────

async def _check_limit_out(debts: list) -> str:
    from nexus.handlers import finance

    msg = MagicMock()
    msg.from_user.id = 7
    msg.answer = AsyncMock()
    rec = MagicMock(amount=1000.0)
    budget_data = {"долги": debts, "постоянные": [], "лимиты": [], "доходы": [], "цели": []}
    with patch.object(finance, "_get_limits", AsyncMock(return_value={"продукты": 10000.0})), \
         patch.object(finance._repo, "query_records", AsyncMock(return_value=[rec])), \
         patch.object(finance, "_get_payday", AsyncMock(return_value=1)), \
         patch.object(finance, "_load_budget_data", AsyncMock(return_value=budget_data)):
        await finance._check_budget_limit("🍜 Продукты", msg, "u-1", amount=1000)
    return " ".join(c.args[0] for c in msg.answer.await_args_list if c.args)


@pytest.mark.asyncio
async def test_check_budget_limit_hides_debt_line_when_all_deferred():
    out = await _check_limit_out([
        {"name": "Аня", "amount": 50000, "monthly_payment": 0},
        {"name": "Дядя", "amount": 30000, "monthly_payment": 0},
    ])
    assert "📋 Долги" not in out


@pytest.mark.asyncio
async def test_check_budget_limit_shows_debt_line_when_active_payment():
    out = await _check_limit_out([
        {"name": "Аня", "amount": 50000, "monthly_payment": 20000},
        {"name": "Дядя", "amount": 30000, "monthly_payment": 0},
    ])
    assert "📋 Долги: 50,000₽" in out  # только долг с активным платежом
