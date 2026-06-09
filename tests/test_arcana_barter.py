"""tests/test_arcana_barter.py — barter-поля в Mini App API Арканы.

Объединяет:
- /api/arcana/clients отдаёт type (🌟/🤝/🎁) и barter_count
  (бывший test_arcana_clients_types_barter.py);
- /api/arcana/sessions (list) включает client_type и has_barter
  (бывший test_arcana_sessions_barter.py).
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


# ── /api/arcana/clients: type + barter_count ─────────────────────────────────

def _client(cid: str, name: str, ctype: str = "🤝 Платный") -> dict:
    return {
        "id": cid,
        "properties": {
            "Имя": {"title": [{"plain_text": name}]},
            "Тип клиента": {"select": {"name": ctype}},
            "🪪 Пользователи": {"relation": [{"id": FAKE_NOTION}]},
        },
    }


def _session(sid: str, cid: str, sum_: int = 3000, paid: int = 0,
             barter_what: str = "") -> dict:
    return {
        "id": sid,
        "properties": {
            "Тема": {"title": [{"plain_text": "Q"}]},
            "👥 Клиенты": {"relation": [{"id": cid}]},
            "Сумма": {"number": sum_},
            "Оплачено": {"number": paid},
            "Бартер · что": {"rich_text": (
                [{"plain_text": barter_what}] if barter_what else []
            )},
        },
    }


def test_clients_payload_has_type_and_barter_count(client):
    clients_pages = [
        _client("c-self", "Кай", "🌟 Self"),
        _client("c-paid", "Маша", "🤝 Платный"),
        _client("c-free", "Аня", "🎁 Бесплатный"),
    ]
    sessions = [
        _session("s1", "c-paid", 3000, 0, barter_what="торт"),
        _session("s2", "c-paid", 5000, 5000),
        _session("s3", "c-free", 0, 0, barter_what="фото"),
    ]

    with patch("miniapp.backend.routes.arcana_clients.arcana_clients_summary",
               AsyncMock(return_value=clients_pages)), \
         patch("miniapp.backend.routes.arcana_clients.sessions_all",
               AsyncMock(return_value=sessions)), \
         patch("miniapp.backend.routes.arcana_clients.rituals_all",
               AsyncMock(return_value=[])), \
         patch("miniapp.backend.routes.arcana_clients.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION)):
        r = client.get("/api/arcana/clients")

    assert r.status_code == 200, r.text
    by_id = {c["id"]: c for c in r.json()["clients"]}
    assert by_id["c-self"]["type"] == "🌟"
    assert by_id["c-self"]["type_full"] == "🌟 Self"
    assert by_id["c-paid"]["type"] == "🤝"
    assert by_id["c-free"]["type"] == "🎁"
    # Бартер: у Маши 1 (s1), у Ани 1 (s3), у Кая 0
    assert by_id["c-paid"]["barter_count"] == 1
    assert by_id["c-free"]["barter_count"] == 1
    assert by_id["c-self"]["barter_count"] == 0


# ── /api/arcana/sessions (list): client_type + has_barter ────────────────────

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
