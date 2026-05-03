"""tests/test_arcana_debts.py — /api/arcana/debts.

Кейсы:
1. Empty — нет долгов и нет открытых бартеров.
2. Только money — расклад/ритуал с непогашенным остатком.
3. Только бартер — открытый item группы, сматченный на ритуал клиента.
4. Self-client с долгом → исключается.
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
             topic: str = "Расклад") -> dict:
    return {
        "id": sid,
        "properties": {
            "Тема": {"title": [{"plain_text": topic}]},
            "👥 Клиенты": {"relation": [{"id": cid}]},
            "Сумма": {"number": sum_},
            "Оплачено": {"number": paid},
            "Бартер · что": {"rich_text": []},
        },
    }


def _ritual(rid: str, cid: str, price: int = 5000, paid: int = 0,
            name: str = "Ритуал") -> dict:
    return {
        "id": rid,
        "properties": {
            "Название": {"title": [{"plain_text": name}]},
            "👥 Клиенты": {"relation": [{"id": cid}]},
            "Цена за ритуал": {"number": price},
            "Оплачено": {"number": paid},
            "Бартер · что": {"rich_text": []},
        },
    }


def _barter_item(iid: str, name: str, group: str, status: str = "To do") -> dict:
    return {
        "id": iid,
        "properties": {
            "Название": {"title": [{"plain_text": name}]},
            "Группа": {"rich_text": [{"plain_text": group}]},
            "Статус": {"status": {"name": status}},
        },
    }


def _patches(clients=None, sessions=None, rituals=None, barter_items=None):
    return [
        patch("miniapp.backend.routes.arcana_debts.arcana_clients_summary",
              AsyncMock(return_value=clients or [])),
        patch("miniapp.backend.routes.arcana_debts.sessions_all",
              AsyncMock(return_value=sessions or [])),
        patch("miniapp.backend.routes.arcana_debts.rituals_all",
              AsyncMock(return_value=rituals or [])),
        patch("miniapp.backend.routes.arcana_debts.query_pages",
              AsyncMock(return_value=barter_items or [])),
        patch("miniapp.backend.routes.arcana_debts.get_user_notion_id",
              AsyncMock(return_value=FAKE_NOTION)),
        patch("miniapp.backend.routes.arcana_debts.config.db_lists", "lists-db-id"),
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
    r = _run(client, clients=[_client("c1", "Маша")])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["money"] == []
    assert body["barter"] == []
    assert body["totals"] == {"money": 0, "barter_items": 0}


def test_debts_money_only(client):
    clients = [_client("c1", "Маша"), _client("c2", "Аня")]
    sessions = [_session("s1", "c1", sum_=3000, paid=1000, topic="Карьера")]
    rituals = [_ritual("r1", "c2", price=5000, paid=0, name="Чистка")]
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
    clients = [_client("c1", "Оля", "🎁 Бесплатный")]
    rituals = [_ritual("r1", "c1", price=0, paid=0, name="Чистка квартиры")]
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
        _client("c-self", "Кай", "🌟 Self"),
        _client("c-paid", "Маша", "🤝 Платный"),
    ]
    sessions = [
        _session("s1", "c-self", sum_=10000, paid=0, topic="Себе"),
        _session("s2", "c-paid", sum_=2000, paid=0, topic="Клиент"),
    ]
    barter = [_barter_item("b1", "торт", "Себе")]
    r = _run(client, clients=clients, sessions=sessions, barter_items=barter)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["totals"]["money"] == 2000
    assert [m["client_name"] for m in body["money"]] == ["Маша"]
    # Бартер группы "Себе" сматчен на self-client → исключён
    assert body["barter"] == []
    assert body["totals"]["barter_items"] == 0
