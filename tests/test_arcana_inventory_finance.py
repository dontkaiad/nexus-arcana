"""tests/test_arcana_inventory_finance.py — расходники Arcana → Финансы.

Проверяем:
 1. core.list_manager.check_items для bot="🌒 Arcana" пишет финансы с
    bot_label="🌒 Arcana" и категорией из CATEGORY_TO_FINANCE.
 2. Mini app endpoint /api/arcana/inventory/{id}/purchase — finance_add вызван
    с правильными аргументами; Количество в инвентаре приплюсовано.
 3. /api/arcana/inventory отдаёт categories с count'ами.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

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
    page = {
        "id": "list-1",
        "properties": {
            "Название": {"title": [{"text": {"content": "соль"}, "plain_text": "соль"}]},
            "Категория": {"select": {"name": "🕯️ Расходники"}},
            "Тип": {"select": {"name": "🛒 Покупки"}},
            "Бот": {"select": {"name": "🌒 Arcana"}},
            "Статус": {"status": {"name": "Not started"}},
        },
    }
    with patch("core.list_manager.db_query", AsyncMock(return_value=[page])), \
         patch("core.list_manager.update_page", AsyncMock(return_value=None)), \
         patch("core.list_manager.finance_add",
               AsyncMock(return_value="fin-1")) as fa:
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

def _inv_page(pid: str, name: str = "соль", qty: float = 200.0,
              cat: str = "🕯️ Расходники", owner: str = FAKE_NOTION_USER) -> dict:
    return {
        "id": pid,
        "properties": {
            "Название": {"title": [{"text": {"content": name}, "plain_text": name}]},
            "Категория": {"select": {"name": cat}},
            "Тип": {"select": {"name": "📦 Инвентарь"}},
            "Бот": {"select": {"name": "🌒 Arcana"}},
            "Статус": {"status": {"name": "Not started"}},
            "Количество": {"number": qty},
            "🪪 Пользователи": {"relation": [{"id": owner}]},
        },
    }


def test_purchase_endpoint_writes_finance_and_appends_qty(client):
    page = _inv_page("inv-1", qty=100.0)
    with patch("miniapp.backend.routes.arcana_inventory.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)), \
         patch("miniapp.backend.routes.arcana_inventory.get_page",
               AsyncMock(return_value=page)), \
         patch("miniapp.backend.routes.arcana_inventory.finance_add",
               AsyncMock(return_value="fin-X")) as fa, \
         patch("miniapp.backend.routes.arcana_inventory.update_page",
               AsyncMock(return_value=None)) as up:
        r = client.post(
            "/api/arcana/inventory/inv-1/purchase",
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
    # qty 100 + 500 = 600
    qty_calls = [c for c in up.await_args_list if "Количество" in c.args[1]]
    assert qty_calls
    assert qty_calls[0].args[1]["Количество"]["number"] == 600.0


def test_depleted_endpoint_archives_and_optionally_adds_to_buy(client):
    page = _inv_page("inv-2", qty=0)
    with patch("miniapp.backend.routes.arcana_inventory.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)), \
         patch("miniapp.backend.routes.arcana_inventory.get_page",
               AsyncMock(return_value=page)), \
         patch("miniapp.backend.routes.arcana_inventory.update_page",
               AsyncMock(return_value=None)) as up, \
         patch("core.list_manager.add_items",
               AsyncMock(return_value=[{"id": "buy-1", "name": "соль"}])):
        r = client.post(
            "/api/arcana/inventory/inv-2/depleted",
            json={"add_to_buy": True},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["archived"] is True
    assert body["buy_id"] == "buy-1"
    arc_calls = [c for c in up.await_args_list if "Статус" in c.args[1]]
    assert arc_calls
    assert arc_calls[0].args[1]["Статус"]["status"]["name"] == "Archived"


# ── 3. /api/arcana/inventory list+categories ─────────────────────────────────

def test_inventory_list_returns_categories_with_counts(client):
    pages = [
        _inv_page("inv-a", name="соль", cat="🕯️ Расходники"),
        _inv_page("inv-b", name="лаванда", cat="🌿 Травы/Масла"),
        _inv_page("inv-c", name="свеча", cat="🕯️ Расходники"),
    ]
    with patch("miniapp.backend.routes.arcana_inventory.query_pages",
               AsyncMock(return_value=pages)), \
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
