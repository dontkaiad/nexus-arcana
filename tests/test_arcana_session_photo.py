"""tests/test_arcana_session_photo.py — фото на уровне сессии.

POST /api/arcana/sessions/by-slug/{slug}/photo:
- Резолвит slug → находит ВСЕ триплеты сессии и пишет URL в каждое поле «Фото».
- Сессия с одним триплетом — пишет на один.

GET /api/arcana/sessions/by-slug/{slug}:
- Возвращает session-level photo_url из первого непустого triplet.photo_url.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

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


def _triplet(pid: str, sname: str, client_id: str | None = "c-1",
             topic: str = "Q", photo_url: str | None = None) -> dict:
    rels_clients = [{"id": client_id}] if client_id else []
    return {
        "id": pid,
        "properties": {
            "Тема": {"title": [{"plain_text": topic}]},
            "Сессия": {"rich_text": [{"plain_text": sname}]},
            "👥 Клиенты": {"relation": rels_clients},
            "🪪 Пользователи": {"relation": [{"id": FAKE_NOTION}]},
            "Колоды": {"multi_select": []},
            "Тип расклада": {"multi_select": []},
            "Тип сеанса": {"select": {"name": "🤝 Клиентский"}},
            "Сумма": {"number": 0},
            "Оплачено": {"number": 0},
            "Фото": {"url": photo_url} if photo_url else {"url": None},
            "Дата": {"date": {"start": "2026-05-01"}},
            "Сбылось": {"select": None},
            "Карты": {"rich_text": []},
            "Трактовка": {"rich_text": []},
            "Дно колоды": {"rich_text": []},
            "Саммари триплета": {"rich_text": []},
            "Область": {"multi_select": []},
        },
    }


def test_session_photo_writes_to_all_triplets(client):
    sname = "Карьера весна"
    slug = f"{slugify(sname)}__c-1"
    triplets = [
        _triplet("t1", sname, "c-1", "1) карьера"),
        _triplet("t2", sname, "c-1", "2) деньги"),
        _triplet("t3", sname, "c-1", "3) близкие"),
        _triplet("other", "Другое", "c-1", "вопрос"),  # другая сессия — не трогаем
    ]
    fake_url = "https://res.cloudinary.com/x/abc.jpg"
    with patch("miniapp.backend.routes.writes._cloudinary_upload",
               AsyncMock(return_value=fake_url)) as cu, \
         patch("miniapp.backend.routes.writes.update_page",
               AsyncMock(return_value=None)) as up, \
         patch("miniapp.backend.routes.writes.sessions_all",
               AsyncMock(return_value=triplets)), \
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
    updated_ids = {call.args[0] for call in up.await_args_list}
    assert updated_ids == {"t1", "t2", "t3"}
    for call in up.await_args_list:
        assert call.args[1] == {"Фото": {"url": fake_url}}


def test_session_photo_solo_triplet(client):
    sname = "Соло"
    slug = f"{slugify(sname)}__c-9"
    triplets = [_triplet("solo-1", sname, "c-9")]
    fake_url = "https://res.cloudinary.com/x/solo.jpg"
    with patch("miniapp.backend.routes.writes._cloudinary_upload",
               AsyncMock(return_value=fake_url)), \
         patch("miniapp.backend.routes.writes.update_page",
               AsyncMock(return_value=None)) as up, \
         patch("miniapp.backend.routes.writes.sessions_all",
               AsyncMock(return_value=triplets)), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION)):
        r = client.post(
            f"/api/arcana/sessions/by-slug/{slug}/photo",
            files={"file": ("solo.jpg", b"FAKE", "image/jpeg")},
        )
    assert r.status_code == 200, r.text
    assert r.json()["updated_count"] == 1
    assert up.await_count == 1
    assert up.await_args.args[0] == "solo-1"


def test_session_get_returns_session_level_photo_url(client):
    sname = "Любовь лето"
    slug = f"{slugify(sname)}__c-2"
    triplets = [
        _triplet("t1", sname, "c-2", "1)", photo_url=None),
        _triplet("t2", sname, "c-2", "2)",
                 photo_url="https://res.cloudinary.com/x/love.jpg"),
        _triplet("t3", sname, "c-2", "3)", photo_url=None),
    ]
    with patch("miniapp.backend.routes.arcana_sessions.sessions_all",
               AsyncMock(return_value=triplets)), \
         patch("miniapp.backend.routes.arcana_sessions.load_clients_map",
               AsyncMock(return_value={"c-2": {"name": "Маша"}})), \
         patch("miniapp.backend.routes.arcana_sessions.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION)), \
         patch("miniapp.backend.routes.arcana_sessions.cache_get",
               return_value=None):
        r = client.get(f"/api/arcana/sessions/by-slug/{slug}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["photo_url"] == "https://res.cloudinary.com/x/love.jpg"
    assert len(body["triplets"]) == 3
