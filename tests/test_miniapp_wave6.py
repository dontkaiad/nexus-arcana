"""Wave 6 tests."""
from __future__ import annotations

import json as _json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from miniapp.backend import cache
from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id


FAKE_TG_ID = 67686090
FAKE_NOTION_USER = "user-notion-id-42"


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    db_file = tmp_path / "adhd_cache.db"
    monkeypatch.setattr(cache, "_DB_PATH", str(db_file))
    cache._init_db()
    yield


@pytest.fixture
def client():
    app.dependency_overrides[current_user_id] = lambda: FAKE_TG_ID
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _today_date():
    return (datetime.now(timezone.utc) + timedelta(hours=3)).date()


def _list_page(iid, *, name="item", type_="📋 Чеклист", status="Not started", bot_name="☀️ Nexus"):
    props = {
        "Название": {"title": [{"plain_text": name}]},
        "Тип": {"select": {"name": type_}},
        "Статус": {"status": {"name": status}},
        "Категория": {"select": None},
        "Количество": {"number": None},
        "Цена": {"number": None},
        "Заметка": {"rich_text": []},
        "Срок годности": {"date": None},
        "Повторяющийся": {"checkbox": False},
        "Группа": {"rich_text": []},
    }
    if bot_name is not None:
        props["Бот"] = {"select": {"name": bot_name}}
    else:
        props["Бот"] = {"select": None}
    return {"id": iid, "properties": props}


# ═════════════════════════════════════════════════════════════════════════════
# Stage 1.1: lists loading — client-side type matching + emoji tolerance
# ═════════════════════════════════════════════════════════════════════════════

def test_lists_check_loads_with_exact_emoji(client):
    pages = [
        _list_page("c1", name="купить молоко", type_="📋 Чеклист"),
        _list_page("c2", name="старое покупочное", type_="🛒 Покупки"),
    ]

    async def qp(*_, **__):
        return pages

    with patch("miniapp.backend.routes.lists.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.lists.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/lists?type=check")

    assert r.status_code == 200
    ids = [i["id"] for i in r.json()["items"]]
    assert "c1" in ids
    assert "c2" not in ids


def test_lists_check_loads_when_type_has_diff_spacing(client):
    """Если в Notion тип записан как '📋  Чеклист' с 2 пробелами — client-side match ловит."""
    pages = [
        _list_page("c1", name="план дня", type_="📋  Чеклист"),
        _list_page("c2", name="утро ритуал", type_="📋 Чеклист "),  # trailing space
    ]

    async def qp(*_, **__):
        return pages

    with patch("miniapp.backend.routes.lists.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.lists.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/lists?type=check")

    assert r.status_code == 200
    ids = {i["id"] for i in r.json()["items"]}
    assert ids == {"c1", "c2"}


def test_lists_inv_matches_partial_keyword(client):
    pages = [
        _list_page("i1", name="гречка", type_="📦 Инвентарь"),
        _list_page("i2", name="перец", type_="📦  Инвентарь"),  # double space
    ]

    async def qp(*_, **__):
        return pages

    with patch("miniapp.backend.routes.lists.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.lists.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/lists?type=inv")

    assert r.status_code == 200
    assert len(r.json()["items"]) == 2


def test_lists_archived_filtered_out(client):
    pages = [
        _list_page("a1", name="активное", type_="📋 Чеклист", status="Not started"),
        _list_page("a2", name="архив", type_="📋 Чеклист", status="Archived"),
    ]

    async def qp(*_, **__):
        return pages

    with patch("miniapp.backend.routes.lists.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.lists.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/lists?type=check")

    ids = [i["id"] for i in r.json()["items"]]
    assert ids == ["a1"]


# ═════════════════════════════════════════════════════════════════════════════
# Stage 1.3: /api/finance?view=today возвращает блок budget
# ═════════════════════════════════════════════════════════════════════════════

def test_finance_today_returns_budget_block(client):
    async def qp(*_, **__):
        return []  # нет расходов

    with patch("miniapp.backend.routes.finance.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.finance.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))), \
         patch("miniapp.backend.routes.finance.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)), \
         patch("core.notion_client.memory_get", AsyncMock(return_value=None)):
        r = client.get("/api/finance?view=today")

    assert r.status_code == 200
    data = r.json()
    assert "budget" in data
    assert data["budget"]["day"] == 4166  # дефолт
    assert data["budget"]["spent"] == 0
    assert data["budget"]["left"] == 4166
    assert data["budget"]["pct"] == 0


def test_finance_today_budget_reflects_spending(client):
    pages = [
        {
            "id": "p1",
            "properties": {
                "Сумма": {"number": 2000},
                "Описание": {"title": [{"plain_text": "магнит"}]},
                "Тип": {"select": {"name": "💸 Расход"}},
                "Категория": {"select": {"name": "🍜 Продукты"}},
            },
        },
    ]

    async def qp(*_, **__):
        return pages

    with patch("miniapp.backend.routes.finance.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.finance.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))), \
         patch("miniapp.backend.routes.finance.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)), \
         patch("core.notion_client.memory_get", AsyncMock(return_value=None)):
        r = client.get("/api/finance?view=today")

    assert r.status_code == 200
    b = r.json()["budget"]
    assert b["spent"] == 2000
    assert b["left"] == 2166
    assert b["pct"] == round(2000 / 4166 * 100)
