"""tests/test_arcana_session_photo.py — фото на уровне сессии.

POST /api/arcana/sessions/by-slug/{slug}/photo:
- Резолвит slug → находит ВСЕ триплеты сессии и пишет URL через set_photo_url.
- Сессия с одним триплетом — пишет на один.

GET /api/arcana/sessions/by-slug/{slug}:
- Возвращает session-level photo_url из первого непустого triplet.photo_url.
"""
from __future__ import annotations

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


def _pg_triplet(pid, sname, client_id="c-1", topic="Q", photo_url=None):
    from arcana.repos.sessions_repo import TripletEntry
    return TripletEntry(
        id=pid, question=topic, cards="", interpretation="",
        deck="Уэйт", session_name=sname, client_id=client_id,
        date="2026-05-01", outcome="unverified",
        amount=Decimal("0"), paid=Decimal("0"),
        spread_type="", area="", barter_what="",
        bottom_card="", photo_url=photo_url,
    )


def _mock_sessions_repo(list_by_slug_result=None, find_result=None):
    repo = MagicMock()
    repo.list_by_slug = AsyncMock(return_value=list_by_slug_result or [])
    repo.find_by_id = AsyncMock(return_value=find_result)
    repo.set_photo_url = AsyncMock(return_value=True)
    repo.list_all = AsyncMock(return_value=[])
    return repo


def test_session_photo_writes_to_all_triplets(client):
    sname = "Карьера весна"
    slug = f"{slugify(sname)}__c-1"
    matching = [
        _pg_triplet("t1", sname, "c-1", "1) карьера"),
        _pg_triplet("t2", sname, "c-1", "2) деньги"),
        _pg_triplet("t3", sname, "c-1", "3) близкие"),
    ]
    fake_url = "https://res.cloudinary.com/x/abc.jpg"
    mock_repo = _mock_sessions_repo(list_by_slug_result=matching)

    with patch("miniapp.backend.routes.writes._sessions_pg_repo", mock_repo), \
         patch("miniapp.backend.routes.writes._cloudinary_upload",
               AsyncMock(return_value=fake_url)) as cu, \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION)):
        r = client.post(
            f"/api/arcana/sessions/by-slug/{slug}/photo",
            files={"file": ("table.jpg", b"FAKEJPG", "image/jpeg")},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["url"] == fake_url
    assert body["updated_count"] == 3
    cu.assert_awaited_once()
    updated_ids = {call.args[0] for call in mock_repo.set_photo_url.await_args_list}
    assert updated_ids == {"t1", "t2", "t3"}
    for call in mock_repo.set_photo_url.await_args_list:
        assert call.args[1] == fake_url


def test_session_photo_solo_triplet(client):
    sname = "Соло"
    slug = f"{slugify(sname)}__c-9"
    matching = [_pg_triplet("solo-1", sname, "c-9")]
    fake_url = "https://res.cloudinary.com/x/solo.jpg"
    mock_repo = _mock_sessions_repo(list_by_slug_result=matching)

    with patch("miniapp.backend.routes.writes._sessions_pg_repo", mock_repo), \
         patch("miniapp.backend.routes.writes._cloudinary_upload",
               AsyncMock(return_value=fake_url)), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION)):
        r = client.post(
            f"/api/arcana/sessions/by-slug/{slug}/photo",
            files={"file": ("solo.jpg", b"FAKE", "image/jpeg")},
        )
    assert r.status_code == 200, r.text
    assert r.json()["updated_count"] == 1
    assert mock_repo.set_photo_url.await_count == 1
    assert mock_repo.set_photo_url.await_args.args[0] == "solo-1"


def test_session_get_returns_session_level_photo_url(client):
    sname = "Любовь лето"
    slug = f"{slugify(sname)}__c-2"
    matching = [
        _pg_triplet("t1", sname, "c-2", "1)", photo_url=None),
        _pg_triplet("t2", sname, "c-2", "2)",
                    photo_url="https://res.cloudinary.com/x/love.jpg"),
        _pg_triplet("t3", sname, "c-2", "3)", photo_url=None),
    ]
    mock_repo = _mock_sessions_repo(list_by_slug_result=matching)
    mock_cl = MagicMock()
    mock_cl.list_all = AsyncMock(return_value=[])

    with patch("miniapp.backend.routes.arcana_sessions._sessions_repo", mock_repo), \
         patch("miniapp.backend.routes.arcana_sessions._clients_repo", mock_cl), \
         patch("miniapp.backend.routes.arcana_sessions.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION)), \
         patch("miniapp.backend.routes.arcana_sessions.today_user_tz",
               AsyncMock(return_value=(__import__("datetime").date(2026, 5, 1), 3))):
        r = client.get(f"/api/arcana/sessions/by-slug/{slug}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["photo_url"] == "https://res.cloudinary.com/x/love.jpg"
    assert len(body["triplets"]) == 3
