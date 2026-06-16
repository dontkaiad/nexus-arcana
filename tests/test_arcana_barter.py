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
    from unittest.mock import MagicMock
    from arcana.repos.clients_repo import Client

    clients_list = [
        Client(id="1", name="Кай",  contact="", request="", notes="", since="",
               type_code="self",  status_code="active"),
        Client(id="2", name="Маша", contact="", request="", notes="", since="",
               type_code="paid",  status_code="active"),
        Client(id="3", name="Аня",  contact="", request="", notes="", since="",
               type_code="free",  status_code="active"),
    ]

    mock_cl = MagicMock()
    mock_cl.list_all = AsyncMock(return_value=clients_list)
    mock_sess = MagicMock()
    mock_sess.list_all = AsyncMock(return_value=[])
    mock_rit = MagicMock()
    mock_rit.list_all = AsyncMock(return_value=[])

    with patch("miniapp.backend.routes.arcana_clients._clients_repo", mock_cl), \
         patch("miniapp.backend.routes.arcana_clients._sessions_repo", mock_sess), \
         patch("miniapp.backend.routes.arcana_clients._rituals_repo", mock_rit), \
         patch("miniapp.backend.routes.arcana_clients.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION)):
        r = client.get("/api/arcana/clients")

    assert r.status_code == 200, r.text
    by_id = {c["id"]: c for c in r.json()["clients"]}
    assert by_id["1"]["type"] == "🌟"
    assert by_id["1"]["type_full"] == "🌟 Self"
    assert by_id["2"]["type"] == "🤝"
    assert by_id["3"]["type"] == "🎁"
    # barter_count = 0 (не хранится в PG, возвращаем 0)
    assert by_id["2"]["barter_count"] == 0
    assert by_id["3"]["barter_count"] == 0
    assert by_id["1"]["barter_count"] == 0


# ── /api/arcana/sessions (list): client_type + has_barter ────────────────────

def _make_pg_triplet(sid, question, cid, dt, barter_what="", amount=3000):
    from arcana.repos.sessions_repo import TripletEntry
    from decimal import Decimal
    return TripletEntry(
        id=sid, question=question, cards="", interpretation="",
        deck="Уэйт", session_name="", client_id=cid,
        date=dt, outcome="unverified",
        amount=Decimal(str(amount)), paid=Decimal("0"),
        spread_type="", area="", barter_what=barter_what,
        bottom_card="", photo_url=None,
    )


def _make_pg_client(cid, name, type_code="paid"):
    from arcana.repos.clients_repo import Client
    return Client(
        id=cid, name=name, contact="", request="", notes="", since="",
        type_code=type_code, status_code="active",
    )


def test_sessions_list_payload_has_client_type_and_has_barter(client):
    from unittest.mock import MagicMock

    today = date(2026, 5, 3)
    sessions_pg = [
        _make_pg_triplet("s1", "Q1", "c-paid", today.isoformat(), barter_what="торт"),
        _make_pg_triplet("s2", "Q2", "c-paid", today.isoformat()),
    ]
    clients_pg = [_make_pg_client("c-paid", "Маша", type_code="paid")]

    mock_sess = MagicMock()
    mock_sess.list_all = AsyncMock(return_value=sessions_pg)
    mock_cl = MagicMock()
    mock_cl.list_all = AsyncMock(return_value=clients_pg)

    with patch("miniapp.backend.routes.arcana_sessions._sessions_repo", mock_sess), \
         patch("miniapp.backend.routes.arcana_sessions._clients_repo", mock_cl), \
         patch("miniapp.backend.routes.arcana_sessions.today_user_tz",
               AsyncMock(return_value=(today, 3))), \
         patch("miniapp.backend.routes.arcana_sessions.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION)):
        r = client.get("/api/arcana/sessions")

    assert r.status_code == 200, r.text
    items = r.json()["sessions"]
    assert items, "ожидаем хотя бы одну запись"
    for it in items:
        assert "client_type" in it
        assert "has_barter" in it
    assert any(it["has_barter"] for it in items)
    for it in items:
        assert it["client_type"] == "🤝"
