"""tests/test_nexus_practice_redirect.py — #128.

Доход от практики, введённый напрямую в Nexus, НЕ пишется в кассу Нексуса
(bot_label="☀️ Nexus"), а отдаёт редирект в Аркану. Расход в 🔮 Практика
(Кай платит за своё обучение) — легитимен, редирект не срабатывает.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core import classifier as clf


@pytest.fixture
def fake_msg():
    m = MagicMock()
    m.answer = AsyncMock()
    m.chat.id = 1
    m.message_id = 1
    m.from_user.id = 42
    return m


def _fin(**over):
    d = {"type": "income", "amount": 3000, "category": "💳 Прочее",
         "source": "💳 Карта", "title": "расклад", "confidence": "high"}
    d.update(over)
    return d


@pytest.mark.parametrize("text,cat", [
    ("заработала на раскладе 3000", "💳 Прочее"),
    ("клиент оплатил сеанс 2000", "💳 Прочее"),
    ("получила за ритуал 5к", "💳 Прочее"),
    ("доход 4000", "🔮 Практика"),  # категория выдаёт практику даже без слова
])
@pytest.mark.asyncio
async def test_practice_income_redirects_not_written(fake_msg, text, cat):
    fake_add = AsyncMock(return_value="p")
    with patch.object(clf._fin_repo, "add", fake_add):
        out = await clf.process_item(_fin(category=cat), text, fake_msg, {}, "uid")
    assert "Arcana" in out and "arcana_kailark_bot" in out
    fake_add.assert_not_awaited()


@pytest.mark.asyncio
async def test_practice_expense_still_written(fake_msg):
    """Кай платит наставнику — обычный расход Нексуса, редиректа нет."""
    fake_add = AsyncMock(return_value="p")
    data = {"type": "expense", "amount": 8000, "category": "🔮 Практика",
            "source": "💳 Карта", "title": "курс таро", "confidence": "high"}
    with patch.object(clf._fin_repo, "add", fake_add), \
         patch("nexus.handlers.finance._check_budget_limit", AsyncMock()), \
         patch("core.list_manager.find_matching_items", AsyncMock(return_value=[])):
        await clf.process_item(data, "заплатила за курс таро 8000", fake_msg, {}, "uid")
    fake_add.assert_awaited_once()
    assert fake_add.call_args.kwargs["bot_label"] == "☀️ Nexus"


@pytest.mark.asyncio
async def test_plain_income_still_written(fake_msg):
    """Обычный доход без практики пишется как раньше."""
    fake_add = AsyncMock(return_value="p")
    with patch.object(clf._fin_repo, "add", fake_add):
        await clf.process_item(_fin(title="зарплата", category="💰 Зарплата"),
                               "пришла зарплата 3000", fake_msg, {}, "uid")
    fake_add.assert_awaited_once()


def test_prompt_documents_practice_income_redirect():
    from core.classifier import build_system
    s = build_system(3)
    assert "ДОХОД ОТ ПРАКТИКИ" in s and "arcana_redirect" in s


def test_rasklad_in_arcana_keywords():
    from core.config import ARCANA_KEYWORDS
    assert "расклад" in ARCANA_KEYWORDS
