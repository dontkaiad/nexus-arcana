"""tests/test_arcana_debts.py — /api/arcana/debts.

Кейсы:
1. Empty — нет долгов и нет открытых бартеров.
2. Только money — расклад/ритуал с непогашенным остатком.
3. Только бартер — открытый item группы, сматченный на ритуал клиента.
4. Self-client с долгом → исключается.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

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


# ── PG helpers ───────────────────────────────────────────────────────────────

def _pg_client(cid, name, type_code="paid"):
    from arcana.repos.clients_repo import Client
    return Client(id=cid, name=name, contact="", request="", notes="", since="",
                  type_code=type_code, status_code="active")


def _pg_triplet(sid, question, cid, sum_=3000, paid_=0):
    from arcana.repos.sessions_repo import TripletEntry
    return TripletEntry(
        id=sid, question=question, cards="", interpretation="",
        deck="Уэйт", session_name="", client_id=cid,
        date="2026-04-10", outcome="unverified",
        amount=Decimal(str(sum_)), paid=Decimal(str(paid_)),
        spread_type="", area="", barter_what="", bottom_card="", photo_url=None,
    )


def _pg_ritual(rid, name, cid, price=5000, paid_=0):
    from arcana.repos.rituals_repo import Ritual
    from datetime import datetime, timezone
    r = Ritual(
        id=rid, name=name, goal=None, place=None, result="unverified",
        price=Decimal(str(price)), paid=Decimal(str(paid_)),
        date=datetime(2026, 4, 10, tzinfo=timezone.utc),
        type_code="paid", consumables="", structure="",
        offerings="", powers="", time_min=None, notes=None, photo_url=None,
    )
    r.client_id = cid
    return r


def _barter_item(iid, name, group):
    from core.repos.pg_nexus_lists_repo import InventoryItem
    return InventoryItem(
        id=str(iid),
        name=name,
        list_type="чеклист",
        status="not_started",
        category="🔄 Бартер",
        group_name=group,
    )


def _patches(clients=None, sessions=None, rituals=None, barter_items=None):
    mock_cl = MagicMock()
    mock_cl.list_all = AsyncMock(return_value=clients or [])
    mock_sess = MagicMock()
    mock_sess.list_all = AsyncMock(return_value=sessions or [])
    mock_rit = MagicMock()
    mock_rit.list_all = AsyncMock(return_value=rituals or [])
    mock_inv = MagicMock()
    mock_inv.get_open_barter = AsyncMock(return_value=barter_items or [])
    return [
        patch("miniapp.backend.routes.arcana_debts._clients_repo", mock_cl),
        patch("miniapp.backend.routes.arcana_debts._sessions_repo", mock_sess),
        patch("miniapp.backend.routes.arcana_debts._rituals_repo", mock_rit),
        patch("miniapp.backend.routes.arcana_debts._arcana_inv_repo", mock_inv),
        patch("miniapp.backend.routes.arcana_debts.get_user_notion_id",
              AsyncMock(return_value=FAKE_NOTION)),
    ]


def _run(client, **kwargs):
    pp = _patches(**kwargs)
    for p in pp:
        p.start()
    try:
        return client.get("/api/arcana/debts")
    finally:
        for p in pp:
            p.stop()


def test_debts_empty(client):
    r = _run(client, clients=[_pg_client("c1", "Маша")])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["money"] == []
    assert body["barter"] == []
    assert body["totals"] == {"money": 0, "barter_items": 0}


def test_debts_money_only(client):
    clients = [_pg_client("c1", "Маша"), _pg_client("c2", "Аня")]
    sessions = [_pg_triplet("s1", "Карьера", "c1", sum_=3000, paid_=1000)]
    rituals = [_pg_ritual("r1", "Чистка", "c2", price=5000, paid_=0)]
    r = _run(client, clients=clients, sessions=sessions, rituals=rituals)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["totals"]["money"] == 7000
    assert body["totals"]["barter_items"] == 0
    # сортировка по amount desc → Аня (5000) первой
    assert [m["client_name"] for m in body["money"]] == ["Аня", "Маша"]
    assert body["money"][0]["amount"] == 5000
    assert body["money"][0]["items"][0]["kind"] == "ritual"
    assert body["money"][0]["items"][0]["desc"] == "Чистка"
    assert body["money"][1]["amount"] == 2000
    assert body["money"][1]["items"][0]["kind"] == "session"
    assert body["money"][1]["items"][0]["paid"] == 1000


def test_debts_barter_only(client):
    clients = [_pg_client("c1", "Оля", type_code="free")]
    rituals = [_pg_ritual("r1", "Чистка квартиры", "c1", price=0, paid_=0)]
    barter = [
        _barter_item("b1", "колода таро", "Чистка квартиры"),
        _barter_item("b2", "благовония", "Чистка квартиры"),
    ]
    r = _run(client, clients=clients, rituals=rituals, barter_items=barter)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["totals"]["money"] == 0
    assert body["totals"]["barter_items"] == 2
    assert len(body["barter"]) == 1
    assert body["barter"][0]["client_name"] == "Оля"
    assert body["barter"][0]["client_type"] == "🎁"
    assert {i["name"] for i in body["barter"][0]["items"]} == {"колода таро", "благовония"}


def test_debts_self_client_excluded(client):
    clients = [
        _pg_client("c-self", "Кай", type_code="self"),
        _pg_client("c-paid", "Маша", type_code="paid"),
    ]
    sessions = [
        _pg_triplet("s1", "Себе", "c-self", sum_=10000, paid_=0),
        _pg_triplet("s2", "Клиент", "c-paid", sum_=2000, paid_=0),
    ]
    barter = [_barter_item("b1", "торт", "Себе")]
    r = _run(client, clients=clients, sessions=sessions, barter_items=barter)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["totals"]["money"] == 2000
    assert [m["client_name"] for m in body["money"]] == ["Маша"]
    assert body["barter"] == []
    assert body["totals"]["barter_items"] == 0
