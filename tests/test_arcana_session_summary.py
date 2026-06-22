"""tests/test_arcana_session_summary.py — сводка ТЕМЫ живёт в БД (#162, #165).

После #165 кросс-дневная сводка группы — это theme_summary якоря.

GET /api/arcana/sessions/by-slug/{slug}:
- summary берётся из theme_summary якорного триплета (источник истины),
  кеш — fallback для домиграционных записей.

POST /api/arcana/sessions/by-slug/{slug}/summarize:
- уже посчитанная (в theme_summary) сводка возвращается как cached, без Sonnet;
- свежая — пишется в theme_summary якоря (set_theme_summary), НЕ в session_summary.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from core.session_cache import slugify
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


def _pg_triplet(pid, sname, client_id="c-1", topic="Q", session_summary="",
                theme_summary=""):
    from arcana.repos.sessions_repo import TripletEntry
    return TripletEntry(
        id=pid, question=topic, cards="2 мечей, шут, маг",
        interpretation="", deck="Уэйт", session_name=sname, client_id=client_id,
        date="2026-05-01", outcome="unverified",
        amount=Decimal("0"), paid=Decimal("0"),
        spread_type="", area="", triplet_summary="кратко",
        session_summary=session_summary, theme_summary=theme_summary,
        barter_what="", bottom_card="", photo_url=None,
    )


def _patch_get(mock_repo):
    mock_cl = MagicMock()
    mock_cl.list_all = AsyncMock(return_value=[])
    return [
        patch("miniapp.backend.routes.arcana_sessions._sessions_repo", mock_repo),
        patch("miniapp.backend.routes.arcana_sessions._clients_repo", mock_cl),
        patch("miniapp.backend.routes.arcana_sessions.get_user_notion_id",
              AsyncMock(return_value=FAKE_NOTION)),
        patch("miniapp.backend.routes.arcana_sessions.today_user_tz",
              AsyncMock(return_value=(datetime.date(2026, 5, 1), 3))),
    ]


def test_get_prefers_db_summary_over_cache(client):
    # #165: summary группы берётся из theme_summary якоря (кросс-дневная тема).
    sname = "Вадим"
    slug = f"{slugify(sname)}__c-1"
    matching = [
        _pg_triplet("t1", sname, "c-1", "1) общее", theme_summary="ИЗ БД"),
        _pg_triplet("t2", sname, "c-1", "2) чувства"),
    ]
    repo = MagicMock()
    repo.list_by_slug = AsyncMock(return_value=matching)
    ctx = _patch_get(repo)
    ctx.append(patch("miniapp.backend.routes.arcana_sessions.cache_get",
                     return_value="ИЗ КЕША"))
    for c in ctx:
        c.start()
    try:
        r = client.get(f"/api/arcana/sessions/by-slug/{slug}")
    finally:
        for c in ctx:
            c.stop()
    assert r.status_code == 200, r.text
    assert r.json()["summary"] == "ИЗ БД"


def test_get_falls_back_to_cache_when_db_empty(client):
    sname = "Вадим"
    slug = f"{slugify(sname)}__c-1"
    matching = [
        _pg_triplet("t1", sname, "c-1", "1) общее", session_summary=""),
        _pg_triplet("t2", sname, "c-1", "2) чувства"),
    ]
    repo = MagicMock()
    repo.list_by_slug = AsyncMock(return_value=matching)
    ctx = _patch_get(repo)
    ctx.append(patch("miniapp.backend.routes.arcana_sessions.cache_get",
                     return_value="ИЗ КЕША"))
    for c in ctx:
        c.start()
    try:
        r = client.get(f"/api/arcana/sessions/by-slug/{slug}")
    finally:
        for c in ctx:
            c.stop()
    assert r.status_code == 200, r.text
    assert r.json()["summary"] == "ИЗ КЕША"


def test_summarize_returns_db_summary_without_sonnet(client):
    sname = "Вадим"
    slug = f"{slugify(sname)}__c-1"
    matching = [
        _pg_triplet("t1", sname, "c-1", "1) общее", theme_summary="ГОТОВОЕ"),
        _pg_triplet("t2", sname, "c-1", "2) чувства"),
    ]
    repo = MagicMock()
    repo.list_by_slug = AsyncMock(return_value=matching)
    repo.set_theme_summary = AsyncMock(return_value=True)
    ask = AsyncMock(return_value="НЕ ДОЛЖНО ВЫЗВАТЬСЯ")
    with patch("miniapp.backend.routes.arcana_sessions._sessions_repo", repo), \
         patch("miniapp.backend.routes.arcana_sessions.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION)), \
         patch("miniapp.backend.routes.arcana_sessions.cache_get", return_value=None), \
         patch("miniapp.backend.routes.arcana_sessions.cache_set"), \
         patch("core.claude_client.ask_claude", ask):
        r = client.post(f"/api/arcana/sessions/by-slug/{slug}/summarize")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["summary"] == "ГОТОВОЕ"
    assert body["cached"] is True
    ask.assert_not_awaited()
    repo.set_theme_summary.assert_not_awaited()


def test_summarize_persists_fresh_to_db_anchor(client):
    sname = "Вадим"
    slug = f"{slugify(sname)}__c-1"
    matching = [
        _pg_triplet("t2", sname, "c-1", "2) чувства", session_summary=""),
        _pg_triplet("t1", sname, "c-1", "1) общее", session_summary=""),
    ]
    repo = MagicMock()
    repo.list_by_slug = AsyncMock(return_value=matching)
    repo.set_theme_summary = AsyncMock(return_value=True)
    ask = AsyncMock(return_value="свежая кросс-дневная сводка темы")
    with patch("miniapp.backend.routes.arcana_sessions._sessions_repo", repo), \
         patch("miniapp.backend.routes.arcana_sessions.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION)), \
         patch("miniapp.backend.routes.arcana_sessions.cache_get", return_value=None), \
         patch("miniapp.backend.routes.arcana_sessions.cache_set"), \
         patch("core.claude_client.ask_claude", ask):
        r = client.post(f"/api/arcana/sessions/by-slug/{slug}/summarize")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["summary"] == "свежая кросс-дневная сводка темы"
    assert body["cached"] is False
    ask.assert_awaited_once()
    # Якорь = «1) общее» (index 1) после сортировки → t1, не t2.
    # Пишется в theme_summary (НЕ session_summary), #165.
    repo.set_theme_summary.assert_awaited_once()
    assert repo.set_theme_summary.await_args.args[0] == "t1"
