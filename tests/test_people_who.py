"""tests/test_people_who.py — «кто это» (people.relation / people.about): подпись, API, контекст бота, подписи тёзок.
Privacy: generic фикстуры."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from core.db import get_engine
from core.memory_search import _people_labels
from core.people import clean_about, person_label
from core.repos.memories_table import memories
from core.repos.people_tables import people
from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id
from tests._people_helpers import insert_person
from tests.test_people_api import mk_fact

MARK = "test-people-who"
TG = 700000002


@pytest.fixture
def client():
    app.dependency_overrides[current_user_id] = lambda: TG
    with patch("miniapp.backend.routes.people.get_user_id", AsyncMock(return_value=MARK)):
        try:
            yield TestClient(app)
        finally:
            app.dependency_overrides.clear()
            with get_engine().begin() as c:
                c.execute(delete(memories).where(memories.c.user_id == MARK))
                c.execute(delete(people).where(people.c.user_id == MARK))


def test_person_label_forms():
    assert person_label("Тёма Лисов") == "Тёма Лисов"
    assert person_label("Тёма Лисов", "friend") == "Тёма Лисов — друг"
    assert person_label("Тёма Лисов", "friend", "коллега по проекту") == "Тёма Лисов — друг, коллега по проекту"
    assert person_label("Тёма", "other", "сосед снизу") == "Тёма — сосед снизу"      # «другое» не дублирует пояснение
    assert person_label("Тёма", "other") == "Тёма — другое"
    assert person_label("Тёма", None, "  из   клуба ") == "Тёма — из клуба"
    assert person_label("Тёма", "bogus") == "Тёма"


def test_clean_about_one_line_and_limit():
    assert clean_about("  а\n\nб  ") == "а б"
    assert clean_about("") is None and clean_about(None) is None
    assert len(clean_about("я" * 300)) == 120


def test_create_person_plus_button(client):
    r = client.post("/api/people", json={"name": "Тёма Лисов", "relation": "friend", "about": "из клуба"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "confirmed" and d["relation"] == "friend" and d["about"] == "из клуба"
    assert d["label"] == "Тёма Лисов — друг, из клуба" and d["homonym_ids"] == []
    # тёзка разрешён, но о существующем сообщаем
    r2 = client.post("/api/people", json={"name": "Тёма Лисов", "relation": "colleague"})
    assert r2.status_code == 200 and r2.json()["homonym_ids"] == [d["id"]]


def test_create_person_validation(client):
    assert client.post("/api/people", json={"name": "  "}).status_code == 422
    assert client.post("/api/people", json={"name": "А", "relation": "enemy"}).status_code == 422
    assert client.post("/api/people", json={"name": "А", "about": "я" * 121}).status_code == 422
    assert client.post("/api/people", json={"name": "А"}).json()["relation"] is None     # «кто это» необязательно


def test_patch_who_and_clear(client):
    pid = insert_person(MARK, "Имя1", status="confirmed")
    r = client.patch(f"/api/people/{pid}", json={"relation": "family", "about": "двоюродная сестра"})
    assert r.status_code == 200 and r.json()["label"] == "Имя1 — семья, двоюродная сестра"
    r = client.patch(f"/api/people/{pid}", json={"relation": None, "about": ""})
    assert r.json()["relation"] is None and r.json()["about"] is None and r.json()["label"] == "Имя1"
    assert client.patch(f"/api/people/{pid}", json={"relation": "enemy"}).status_code == 422


def test_homonym_labels_in_search_groups(client):
    a = insert_person(MARK, "Тёма", status="confirmed", relation="friend", about="из клуба")
    b = insert_person(MARK, "Тёма", status="confirmed", notes="старая заметка")
    c = insert_person(MARK, "Тёма", status="confirmed")
    labels = _people_labels([a, b, c])
    assert labels[a] == "Тёма — друг, из клуба"
    assert labels[b] == "Тёма (старая заметка)"          # без «кто это» — как раньше
    assert labels[c] == "Тёма"


def test_bot_context_labels_person(client):
    from core.memory import _people_context_block
    from core.repos import pg_people_repo as prepo
    a = prepo.create(MARK, "Тёма", [], status="confirmed", relation="friend", about="из клуба")
    mk_fact(a, "likes", "confirmed", "любит чай", user=MARK)
    seen = set()
    block = _people_context_block(MARK, ["тёма"], seen)
    assert "[Тёма — друг, из клуба]" in block and "любит чай" in block
    # человек без «кто это» и не тёзка — заголовка-скобок нет, как раньше
    b = prepo.create(MARK, "Оля", [], status="confirmed")
    mk_fact(b, "likes", "confirmed", "любит кофе", user=MARK)
    assert "[Оля" not in _people_context_block(MARK, ["оля"], set())
