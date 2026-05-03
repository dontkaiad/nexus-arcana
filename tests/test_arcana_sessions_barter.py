"""tests/test_arcana_sessions_barter.py — /api/arcana/sessions (list)
включает client_type и has_barter в каждом item.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id


FAKE_TG = 67686090
FAKE_NOTION = "user-notion-id-42"


@pytest.fixture
def client():
    app.dependency_overrides[current_user_id] = lambda: FAKE_TG
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _sess(sid: str, cid: str, dt: str, barter: str = "", question: str = "Q") -> dict:
    return {
        "id": sid,
        "properties": {
            "Тема": {"title": [{"plain_text": question}]},
            "Дата": {"date": {"start": dt}},
            "👥 Клиенты": {"relation": [{"id": cid}]},
            "Сумма": {"number": 3000}, "Оплачено": {"number": 0},
            "Бартер · что": {"rich_text": ([{"plain_text": barter}] if barter else [])},
            "Колоды": {"multi_select": []},
            "Область": {"multi_select": []},
            "Тип расклада": {"multi_select": []},
            "Тип сеанса": {"select": None},
            "Сбылось": {"select": None},
            "Карты": {"rich_text": []},
            "Дно колоды": {"rich_text": []},
        },
    }


def _client_page(cid: str, name: str) -> dict:
    return {
        "id": cid,
        "properties": {
            "Имя": {"title": [{"plain_text": name}]},
            "Тип клиента": {"select": {"name": "🤝 Платный"}},
        },
    }


def test_sessions_list_payload_has_client_type_and_has_barter(client):
    today = date(2026, 5, 3)
    pages = [
        _sess("s1", "c-paid", today.isoformat(), barter="торт"),
        _sess("s2", "c-paid", today.isoformat()),  # без бартера
    ]
    with patch("miniapp.backend.routes.arcana_sessions.sessions_all",
               AsyncMock(return_value=pages)), \
         patch("miniapp.backend.routes._arcana_common.arcana_clients_summary",
               AsyncMock(return_value=[_client_page("c-paid", "Маша")])), \
         patch("miniapp.backend.routes.arcana_sessions.today_user_tz",
               AsyncMock(return_value=(today, 3))), \
         patch("miniapp.backend.routes.arcana_sessions.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION)), \
         patch("miniapp.backend.routes.arcana_today._client_types_map",
               AsyncMock(return_value={"c-paid": "🤝 Платный"})):
        r = client.get("/api/arcana/sessions")

    assert r.status_code == 200, r.text
    items = r.json()["sessions"]
    assert items, "ожидаем хотя бы одну запись"
    for it in items:
        assert "client_type" in it
        assert "has_barter" in it
    # Хотя бы одна запись с бартером (s1)
    assert any(it["has_barter"] for it in items)
    # Все: тип клиента — 🤝
    for it in items:
        assert it["client_type"] == "🤝"
