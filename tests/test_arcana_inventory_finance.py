"""tests/test_arcana_inventory_finance.py — расходники Arcana → Финансы.

Проверяем:
 1. core.list_manager.check_items для bot="🌒 Arcana" пишет финансы с
    bot_label="🌒 Arcana" и категорией из CATEGORY_TO_FINANCE.
 2. Mini app endpoint /api/arcana/inventory/{id}/purchase — finance_add вызван
    с правильными аргументами; Количество в инвентаре приплюсовано.
 3. /api/arcana/inventory отдаёт categories с count'ами.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id


FAKE_TG_ID = 67686090
FAKE_NOTION_USER = "user-notion-id-42"


@pytest.fixture
def client():
    app.dependency_overrides[current_user_id] = lambda: FAKE_TG_ID
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


# ── 1. bot=Arcana check_items → finance_add(bot_label=Arcana, cat=Расходники) ──

@pytest.mark.asyncio
async def test_check_items_arcana_writes_finance_with_arcana_bot():
    from core.list_manager import check_items
    from core.repos.pg_nexus_lists_repo import InventoryItem
    import core.list_manager as lm

    fake_item = InventoryItem(
        id="1", name="соль", list_type="инвентарь", status="not_started",
        category="🕯️ Расходники", user_notion_id=FAKE_NOTION_USER,
    )

    with patch.object(lm._arcana_repo, "search", AsyncMock(return_value=[fake_item])), \
         patch.object(lm._arcana_repo, "update_status", AsyncMock(return_value=True)), \
         patch.object(lm, "finance_add", AsyncMock(return_value="fin-1")) as fa:
        result = await check_items(
            [{"name": "соль", "price": 200}],
            bot_name="🌒 Arcana",
            user_page_id=FAKE_NOTION_USER,
        )

    assert result["finance_results"][0]["category"] == "🕯️ Расходники"
    fa.assert_awaited_once()
    kwargs = fa.await_args.kwargs
    assert kwargs["bot_label"] == "🌒 Arcana"
    assert kwargs["category"] == "🕯️ Расходники"
    assert kwargs["amount"] == 200.0


# ── 2. /api/arcana/inventory/{id}/purchase → finance_add + qty append ──────

def _inv_item(iid: str, name: str = "соль", qty: float = 200.0,
              cat: str = "🕯️ Расходники", owner: str = FAKE_NOTION_USER):
    from core.repos.pg_nexus_lists_repo import InventoryItem
    return InventoryItem(
        id=iid,
        name=name,
        list_type="инвентарь",
        status="not_started",
        category=cat,
        quantity=qty,
        user_notion_id=owner,
    )


def test_purchase_endpoint_writes_finance_and_appends_qty(client):
    from miniapp.backend.routes import arcana_inventory
    inv_item = _inv_item("1", qty=100.0)
    mock_repo = MagicMock()
    mock_repo.get_by_id = AsyncMock(return_value=inv_item)
    mock_repo.update = AsyncMock(return_value=True)
    fa = AsyncMock(return_value="fin-X")
    with patch("miniapp.backend.routes.arcana_inventory.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)), \
         patch("miniapp.backend.routes.arcana_inventory._arcana_inv_repo", mock_repo), \
         patch.object(arcana_inventory._fin_repo, "add", fa):
        r = client.post(
            "/api/arcana/inventory/1/purchase",
            json={"price": 250, "qty_added": 500},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["finance_id"] == "fin-X"
    assert body["finance_category"] == "🕯️ Расходники"
    fa.assert_awaited_once()
    kwargs = fa.await_args.kwargs
    assert kwargs["bot_label"] == "🌒 Arcana"
    assert kwargs["amount"] == 250.0
    # update called with qty 100 + 500 = 600
    mock_repo.update.assert_awaited_once()
    up_kwargs = mock_repo.update.await_args.kwargs
    assert up_kwargs.get("quantity") == 600.0


def test_purchase_arcana_pnl_guard(client):
    """Регресс: расход расходника → bot_label=🌒 Arcana и type_=💸 Расход.
    Гард: запись идёт в arcana_pnl, НЕ в nexus_budget."""
    from miniapp.backend.routes import arcana_inventory
    inv_item = _inv_item("3", name="ладан", cat="🌿 Травы/Масла", qty=50.0)
    mock_repo = MagicMock()
    mock_repo.get_by_id = AsyncMock(return_value=inv_item)
    mock_repo.update = AsyncMock(return_value=True)
    fa = AsyncMock(return_value="fin-guard")
    with patch("miniapp.backend.routes.arcana_inventory.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)), \
         patch("miniapp.backend.routes.arcana_inventory._arcana_inv_repo", mock_repo), \
         patch.object(arcana_inventory._fin_repo, "add", fa):
        r = client.post(
            "/api/arcana/inventory/3/purchase",
            json={"price": 180},
        )
    assert r.status_code == 200, r.text
    fa.assert_awaited_once()
    kw = fa.await_args.kwargs
    assert kw["bot_label"] == "🌒 Arcana"   # → arcana_pnl, не nexus_budget
    assert kw["type_"] == "💸 Расход"
    assert kw["category"] == "🕯️ Расходники"  # Травы/Масла → Расходники via CATEGORY_TO_FINANCE
    assert kw["amount"] == 180.0


def test_depleted_endpoint_archives_and_optionally_adds_to_buy(client):
    inv_item = _inv_item("2", qty=0)
    mock_repo = MagicMock()
    mock_repo.get_by_id = AsyncMock(return_value=inv_item)
    mock_repo.update_status = AsyncMock(return_value=True)
    with patch("miniapp.backend.routes.arcana_inventory.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)), \
         patch("miniapp.backend.routes.arcana_inventory._arcana_inv_repo", mock_repo), \
         patch("core.list_manager.add_items",
               AsyncMock(return_value=[{"id": "buy-1", "name": "соль"}])):
        r = client.post(
            "/api/arcana/inventory/2/depleted",
            json={"add_to_buy": True},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["archived"] is True
    assert body["buy_id"] == "buy-1"
    mock_repo.update_status.assert_awaited_once_with("2", "Archived")


# ── 3. /api/arcana/inventory list+categories ─────────────────────────────────

def test_inventory_list_returns_categories_with_counts(client):
    from core.repos.pg_nexus_lists_repo import InventoryItem
    pg_items = [
        InventoryItem(id="a", name="соль", list_type="инвентарь", status="not_started",
                      category="🕯️ Расходники", user_notion_id=FAKE_NOTION_USER),
        InventoryItem(id="b", name="лаванда", list_type="инвентарь", status="not_started",
                      category="🌿 Травы/Масла", user_notion_id=FAKE_NOTION_USER),
        InventoryItem(id="c", name="свеча", list_type="инвентарь", status="not_started",
                      category="🕯️ Расходники", user_notion_id=FAKE_NOTION_USER),
    ]
    mock_repo = MagicMock()
    mock_repo.get_list = AsyncMock(return_value=pg_items)
    with patch("miniapp.backend.routes.arcana_inventory._arcana_inv_repo", mock_repo), \
         patch("miniapp.backend.routes.arcana_inventory.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/arcana/inventory")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["items"]) == 3
    cats = {c["name"]: c["count"] for c in body["categories"]}
    assert cats["🕯️ Расходники"] == 2
    assert cats["🌿 Травы/Масла"] == 1
    assert cats["🃏 Карты/Колоды"] == 0
