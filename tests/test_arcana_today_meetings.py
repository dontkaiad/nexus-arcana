"""tests/test_arcana_today_meetings.py — «встречи» на Today-экране Арканы (#163).

client_sessions_today = число distinct КЛИЕНТСКИХ сессий за сегодня
(триплеты одной сессии = одна встреча), исключая self-client/личные расклады.
Раньше подпись считала триплеты (5) вместо сессий (1) и считала self-расклад.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from arcana.repos.sessions_repo import TripletEntry
from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id
from miniapp.backend.routes import arcana_today as at

FAKE_TG = 67686090
TODAY = date(2026, 6, 22)


@pytest.fixture
def client():
    app.dependency_overrides[current_user_id] = lambda: FAKE_TG
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _triplet(pid, sname, client_id, q="вопрос"):
    return TripletEntry(
        id=pid, question=q, cards="2 мечей, шут, маг", interpretation="",
        deck="Уэйт", session_name=sname, client_id=client_id,
        date=TODAY.isoformat(), outcome="unverified",
        amount=Decimal("0"), paid=Decimal("0"),
    )


def _call(client, triplets, clients_map):
    patches = [
        patch.object(at, "today_user_tz", AsyncMock(return_value=(TODAY, 3))),
        patch.object(at, "get_user_notion_id", AsyncMock(return_value="u-1")),
        patch.object(at, "load_clients_map", AsyncMock(return_value=clients_map)),
        patch.object(at._pg_sessions_repo, "list_all",
                     AsyncMock(return_value=triplets)),
        patch.object(at, "_works_schedule", AsyncMock(return_value=([], []))),
        patch.object(at, "_load_rituals", AsyncMock(return_value=[])),
        patch.object(at._pnl_repo, "query_month", AsyncMock(return_value=[])),
    ]
    for p in patches:
        p.start()
    try:
        r = client.get("/api/arcana/today")
    finally:
        for p in patches:
            p.stop()
    assert r.status_code == 200, r.text
    return r.json()


def test_self_session_is_not_a_meeting(client):
    """5 триплетов, одна self-сессия → встреч 0 (личный расклad ≠ встреча)."""
    triplets = [_triplet(f"t{i}", "Вадим", "self-1") for i in range(5)]
    cmap = {"self-1": {"type_code": "self", "name": "Кай"}}
    body = _call(client, triplets, cmap)
    assert body["client_sessions_today"] == 0
    # sessions_today по-прежнему = триплеты (для других виджетов)
    assert len(body["sessions_today"]) == 5


def test_triplets_of_one_client_session_count_once(client):
    """3 триплета одной платной сессии → одна встреча."""
    triplets = [_triplet(f"t{i}", "Маша — приворот", "c-2") for i in range(3)]
    cmap = {"c-2": {"type_code": "paid", "name": "Маша"}}
    body = _call(client, triplets, cmap)
    assert body["client_sessions_today"] == 1


def test_two_distinct_client_sessions(client):
    """Две разные клиентские сессии (платная + бесплатная) → 2 встречи."""
    triplets = [
        _triplet("t1", "Маша", "c-2"),
        _triplet("t2", "Маша", "c-2"),
        _triplet("t3", "Оля", "c-3"),
    ]
    cmap = {
        "c-2": {"type_code": "paid", "name": "Маша"},
        "c-3": {"type_code": "free", "name": "Оля"},
    }
    body = _call(client, triplets, cmap)
    assert body["client_sessions_today"] == 2


def test_self_excluded_client_counted(client):
    """Self-сессия + одна платная → встреч 1 (self не считается)."""
    triplets = [
        _triplet("t1", "Личное", "self-1"),
        _triplet("t2", "Клиент Х", "c-2"),
    ]
    cmap = {
        "self-1": {"type_code": "self", "name": "Кай"},
        "c-2": {"type_code": "paid", "name": "Х"},
    }
    body = _call(client, triplets, cmap)
    assert body["client_sessions_today"] == 1
