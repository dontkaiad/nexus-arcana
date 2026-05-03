"""tests/test_arcana_clients_types_barter.py — /api/arcana/clients отдаёт
type (🌟/🤝/🎁) и barter_count.
"""
from __future__ import annotations

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
