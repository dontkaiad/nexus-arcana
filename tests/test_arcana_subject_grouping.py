"""tests/test_arcana_subject_grouping.py — subject_id группировка в Mini App (#189).

GET /api/arcana/sessions:
- триплеты с одинаковым subject_id, но РАЗНЫМИ session_name схлопываются
  в одну группу (фикс фрагментации «Вадим» / «Вадим — отношения» / …);
- slug такой группы — "subj-{subject_id}".

GET /api/arcana/sessions/by-slug/{slug}:
- slug "subj-N" уходит через list_by_subject, а не list_by_slug.

(GET /api/arcana/sessions/by-subject/{id} существовал как альтернативный
вход в ту же _aggregate_group по числовому id, но фронт им не пользовался
ни разу — удалён, см. by-slug/subj-N тесты ниже для той же логики.)
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id
from tests.test_miniapp_arcana import _make_triplet, _mock_clients_repo

FAKE_TG = 67686090
FAKE_NOTION = "user-notion-id-42"


@pytest.fixture
def client():
    app.dependency_overrides[current_user_id] = lambda: FAKE_TG
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _ctx(mock_repo, mock_cl=None):
    mock_cl = mock_cl or _mock_clients_repo(list_all_result=[])
    return [
        patch("miniapp.backend.routes.arcana_sessions._sessions_repo", mock_repo),
        patch("miniapp.backend.routes.arcana_sessions._clients_repo", mock_cl),
        patch("miniapp.backend.routes.arcana_sessions.get_user_notion_id",
              AsyncMock(return_value=FAKE_NOTION)),
        patch("miniapp.backend.routes.arcana_sessions.today_user_tz",
              AsyncMock(return_value=(__import__("datetime").date(2026, 5, 1), 3))),
    ]


def test_list_sessions_collapses_by_subject_id_despite_different_names(client):
    sessions_pg = [
        _make_triplet("1", "Что думает Вадим", session_name="Вадим",
                      subject_id=42, date="2026-05-01"),
        _make_triplet("2", "Диагностика Вадима", session_name="Вадим — диагностика",
                      subject_id=42, date="2026-05-02"),
        _make_triplet("3", "Вадим и отношения", session_name="Вадим — отношения",
                      subject_id=42, date="2026-05-03"),
        _make_triplet("4", "Не Вадим", session_name="Другая тема",
                      date="2026-05-01"),
    ]
    mock_repo = MagicMock()
    mock_repo.list_all = AsyncMock(return_value=sessions_pg)

    ctx = _ctx(mock_repo)
    with ctx[0], ctx[1], ctx[2], ctx[3]:
        r = client.get("/api/arcana/sessions")

    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 2  # subject-группа (3 триплета) + одиночка

    by_slug = {s["slug"]: s for s in data["sessions"]}
    assert "subj-42" in by_slug
    subj_group = by_slug["subj-42"]
    assert subj_group["subject_id"] == 42
    assert subj_group["triplet_count"] == 3


def test_by_slug_subj_prefix_uses_list_by_subject(client):
    matching = [
        _make_triplet("1", "Q1", session_name="Вадим", subject_id=42, date="2026-05-01"),
        _make_triplet("2", "Q2", session_name="Вадим — диагностика", subject_id=42,
                      date="2026-05-02"),
    ]
    mock_repo = MagicMock()
    mock_repo.list_by_subject = AsyncMock(return_value=matching)

    ctx = _ctx(mock_repo)
    with ctx[0], ctx[1], ctx[2], ctx[3]:
        r = client.get("/api/arcana/sessions/by-slug/subj-42")

    assert r.status_code == 200
    data = r.json()
    assert data["subject_id"] == 42
    assert len(data["triplets"]) == 2
    mock_repo.list_by_subject.assert_awaited_once()
    assert mock_repo.list_by_subject.await_args.args[0] == 42

    # events — по одному на каждый (session_name, client_id), хронологически.
    assert len(data["events"]) == 2
    assert [e["session_name"] for e in data["events"]] == ["Вадим", "Вадим — диагностика"]
    assert [e["date"] for e in data["events"]] == ["2026-05-01", "2026-05-02"]
    assert all(e["triplet_count"] == 1 for e in data["events"])
    for e in data["events"]:
        assert "slug" in e and e["slug"]
        assert "status" in e


def test_regular_session_has_no_events_field(client):
    matching = [
        _make_triplet("1", "Q1", session_name="Обычная тема", date="2026-05-01"),
    ]
    mock_repo = MagicMock()
    mock_repo.list_by_slug = AsyncMock(return_value=matching)

    ctx = _ctx(mock_repo)
    with ctx[0], ctx[1], ctx[2], ctx[3]:
        r = client.get("/api/arcana/sessions/by-slug/obychnaya-tema__self")

    assert r.status_code == 200
    assert r.json()["events"] is None


def test_by_slug_subj_prefix_404_when_empty(client):
    mock_repo = MagicMock()
    mock_repo.list_by_subject = AsyncMock(return_value=[])

    ctx = _ctx(mock_repo)
    with ctx[0], ctx[1], ctx[2], ctx[3]:
        r = client.get("/api/arcana/sessions/by-slug/subj-999")

    assert r.status_code == 404
