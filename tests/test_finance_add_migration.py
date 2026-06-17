"""tests/test_finance_add_migration.py — регресс: finance_add удалён, все вызовы через _fin_repo.add.

Гарантирует:
  1. classifier.py пишет с bot_label="☀️ Nexus" явно.
  2. Barter guard работает: source="🔄 Бартер" + Nexus → sanitised к "💳 Карта".
  3. _fin_repo.add с bot_label Arcana идёт в arcana_repo, не в nexus.
  4. finance_add как публичная функция удалена из core.notion_client.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

# ── 1. classifier.py → _fin_repo.add с явным bot_label="☀️ Nexus" ────────────

@pytest.mark.asyncio
async def test_classifier_defaults_to_nexus():
    """process_item(finance, kind=expense) вызывает _fin_repo.add с bot_label='☀️ Nexus'."""
    from core import classifier as clf

    fake_add = AsyncMock(return_value="page-nexus-1")

    # process_item ветвится по data["type"]; для финансов type="expense"|"income"
    data = {
        "type": "expense",
        "amount": 500,
        "category": "🍜 Продукты",
        "source": "💳 Карта",
        "title": "продукты",
        "confidence": "high",
    }
    fake_msg = MagicMock()
    fake_msg.answer = AsyncMock()
    fake_msg.chat.id = 1
    fake_msg.message_id = 1

    with patch.object(clf._fin_repo, "add", fake_add), \
         patch("core.classifier.log_error", AsyncMock(return_value="err-1")):
        await clf.process_item(
            data=data,
            original_text="продукты 500р",
            msg=fake_msg,
            clarify={},
            user_notion_id="uid-1",
        )

    assert fake_add.called, "_fin_repo.add не был вызван из classifier"
    kwargs = fake_add.call_args.kwargs
    assert kwargs["bot_label"] == "☀️ Nexus", (
        f"bot_label должен быть '☀️ Nexus', получили: {kwargs.get('bot_label')!r}"
    )
    assert kwargs["amount"] == 500
    assert kwargs["category"] == "🍜 Продукты"


# ── 2. Barter guard: Nexus + бартер → source sanitised к "💳 Карта" ──────────

def test_barter_source_nexus_sanitised():
    """_guard_source('🔄 Бартер', '☀️ Nexus') → '💳 Карта'."""
    from core.repos.finance_repo import _guard_source, BARTER_SOURCE

    result = _guard_source(BARTER_SOURCE, "☀️ Nexus")
    assert result == "💳 Карта", (
        f"Ожидали sanitised '💳 Карта' для Nexus+Бартер, получили: {result!r}"
    )


def test_barter_source_arcana_preserved():
    """_guard_source('🔄 Бартер', '🌒 Arcana') → бартер сохраняется."""
    from core.repos.finance_repo import _guard_source, BARTER_SOURCE

    result = _guard_source(BARTER_SOURCE, "🌒 Arcana")
    assert result == BARTER_SOURCE, (
        f"Бартер для Arcana должен сохраняться, получили: {result!r}"
    )


# ── 3. _fin_repo.add с bot_label Arcana → arcana_repo, не nexus ─────────────

@pytest.mark.asyncio
async def test_arcana_routing():
    """_fin_repo.add(bot_label='🌒 Arcana') вызывает arcana_repo.add_entry."""
    from core.repos import finance_repo as fr

    fake_arcana_add = AsyncMock(return_value="arcana-page-1")
    fake_nexus_add = AsyncMock(return_value="nexus-page-1")

    with patch.object(fr._arcana_repo, "add_entry", fake_arcana_add), \
         patch.object(fr._nexus_repo, "add_entry", fake_nexus_add):
        result = await fr._repo.add(
            date="2026-06-17",
            amount=1000.0,
            category="🔮 Практика",
            type_="💰 Доход",
            source="💳 Карта",
            bot_label="🌒 Arcana",
            description="тест arcana routing",
            user_notion_id="u-arc",
        )

    fake_arcana_add.assert_awaited_once()
    fake_nexus_add.assert_not_awaited()
    assert result == "arcana-page-1"

    # Проверяем что nexus не получил Arcana-запись
    arcana_kwargs = fake_arcana_add.call_args.kwargs
    assert arcana_kwargs["description"] == "тест arcana routing"
    assert arcana_kwargs["user_notion_id"] == "u-arc"


@pytest.mark.asyncio
async def test_nexus_routing():
    """_fin_repo.add(bot_label='☀️ Nexus') вызывает nexus_repo.add_entry."""
    from core.repos import finance_repo as fr

    fake_arcana_add = AsyncMock(return_value="arcana-page-2")
    fake_nexus_add = AsyncMock(return_value="nexus-page-2")

    with patch.object(fr._arcana_repo, "add_entry", fake_arcana_add), \
         patch.object(fr._nexus_repo, "add_entry", fake_nexus_add):
        result = await fr._repo.add(
            date="2026-06-17",
            amount=500.0,
            category="🍜 Продукты",
            type_="💸 Расход",
            source="💳 Карта",
            bot_label="☀️ Nexus",
            description="тест nexus routing",
            user_notion_id="u-nex",
        )

    fake_nexus_add.assert_awaited_once()
    fake_arcana_add.assert_not_awaited()
    assert result == "nexus-page-2"


# ── 4. finance_add удалена из core.notion_client ─────────────────────────────

def test_finance_add_removed():
    """core.notion_client НЕ экспортирует finance_add после удаления шима."""
    import core.notion_client as nc

    assert not hasattr(nc, "finance_add"), (
        "finance_add всё ещё присутствует в core.notion_client — шим не удалён"
    )
