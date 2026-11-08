"""tests/test_give_facts_api.py — «Это про…»: отдать факты «Я» другому человеку, подсказка из того же чата, возврат в «Я»."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, text

from core.db import get_engine
from core.repos import pg_people_repo as prepo
from core.repos.memories_table import memories
from core.repos.people_tables import people
from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id
from tests._people_helpers import add_account_row, insert_person
from tests.test_people_api import mk_fact

MARK = "test-give-facts"
TG = 700000088


@pytest.fixture
def client():
    app.dependency_overrides[current_user_id] = lambda: TG
    with patch("miniapp.backend.routes.people.get_user_id", AsyncMock(return_value=MARK)), \
         patch("core.repos.pg_memory_repo._spawn_index") as spawn:
        c = TestClient(app)
        c.spawn = spawn
        try:
            yield c
        finally:
            app.dependency_overrides.clear()
            with get_engine().begin() as cn:
                cn.execute(delete(memories).where(memories.c.user_id == MARK))
                cn.execute(delete(people).where(people.c.user_id == MARK))


def _me():
    return insert_person(MARK, "Я", is_self=True, status="confirmed")


def _fact(pid, text_, chat=None, cat="❓ Проверить автора", typ="dislikes"):
    fid = mk_fact(pid, type_=typ, text_=text_, user=MARK, status="pending")
    with get_engine().begin() as c:
        c.execute(text("UPDATE memories SET category = :c, source_chat_id = :ch, embedding = NULL WHERE id = :i"),
                  {"c": cat, "ch": chat, "i": fid})
    return fid


def _row(fid):
    with get_engine().connect() as c:
        return c.execute(text("SELECT person_id, review_status, category, key_name, related_to, is_archived FROM memories WHERE id = :i"),
                         {"i": fid}).one()


def test_give_moves_fact_keeps_type_and_category_and_confirms(client):
    me = _me()
    nik = prepo.create(MARK, "Игорь", ["Игорь"], status="confirmed")
    f = _fact(me, "не любит жару", chat=111)
    r = client.post(f"/api/people/{me}/facts/give", json={"ids": [f], "to_person_id": nik}).json()
    assert r["moved"] == 1 and r["to_person_id"] == nik
    row = _row(f)
    assert row.person_id == nik and row.review_status == "confirmed" and row.category == "👥 Люди"          # «❓» → «Люди»
    assert row.key_name.startswith(f"person_{nik}_dislikes_") and row.related_to.startswith("Игорь")      # тип из старого ключа
    client.spawn.assert_called_once()
    cards = [x["id"] for g in client.get(f"/api/people/{me}").json()["facts"] for x in g["items"]]
    assert f not in cards
    assert f in [x["id"] for g in client.get(f"/api/people/{nik}").json()["facts"] for x in g["items"]]


def test_give_many_and_new_person(client):
    me = _me()
    a, b = _fact(me, "не ест грибы"), _fact(me, "любит чай", typ="likes")
    r = client.post(f"/api/people/{me}/facts/give", json={"ids": [a, b], "new_person": {"name": "  Даша  ", "relation": "friend"}}).json()
    assert r["moved"] == 2 and r["created"] is True
    with get_engine().connect() as c:
        card = c.execute(text("SELECT name, status, relation FROM people WHERE id = :i"), {"i": r["to_person_id"]}).one()
    assert (card.name, card.status, card.relation) == ("Даша", "confirmed", "friend")
    assert _row(a).person_id == r["to_person_id"] == _row(b).person_id


def test_give_is_reversible_back_to_self(client):
    me = _me()
    nik = prepo.create(MARK, "Игорь", ["Игорь"], status="confirmed")
    f = _fact(me, "не любит жару")
    client.post(f"/api/people/{me}/facts/give", json={"ids": [f], "to_person_id": nik})
    assert client.get(f"/api/people/{nik}").json()["self_id"] == me
    r = client.post(f"/api/people/{nik}/facts/give", json={"ids": [f], "to_person_id": me}).json()
    assert r["moved"] == 1
    row = _row(f)
    assert row.person_id == me and row.key_name.startswith(f"person_{me}_dislikes_")
    assert row.category == "🗂 Прочее"                                      # «Люди» в «Я» не категория; «❓» не возвращаем


def test_other_self_category_is_kept_both_ways(client):
    me = _me()
    nik = prepo.create(MARK, "Игорь", ["Игорь"], status="confirmed")
    f = _fact(me, "любит чай", cat="🛒 Предпочтения", typ="likes")
    client.post(f"/api/people/{me}/facts/give", json={"ids": [f], "to_person_id": nik})
    assert _row(f).category == "🛒 Предпочтения"                          # обычная категория сохраняется
    client.post(f"/api/people/{nik}/facts/give", json={"ids": [f], "to_person_id": me})
    assert _row(f).category == "🛒 Предпочтения"


def test_duplicate_at_target_is_a_conflict_not_a_loss(client):
    me = _me()
    nik = prepo.create(MARK, "Игорь", ["Игорь"], status="confirmed")
    from core.people import fact_hash
    f = _fact(me, "не любит жару")
    with get_engine().begin() as c:                      # у Никиты уже есть такой же факт
        c.execute(text("INSERT INTO memories (fact_text, key_name, category, user_id, person_id, review_status) VALUES "
                       "('не любит жару', :k, '👥 Люди', :u, :p, 'confirmed')"),
                  {"k": f"person_{nik}_dislikes_{fact_hash('не любит жару')}", "u": MARK, "p": nik})
    r = client.post(f"/api/people/{me}/facts/give", json={"ids": [f], "to_person_id": nik}).json()
    assert r["moved"] == 0 and r["conflicts"] == 1 and _row(f).person_id == me


def test_give_validation(client):
    me = _me()
    nik = prepo.create(MARK, "Игорь", ["Игорь"], status="confirmed")
    bad = prepo.create(MARK, "Плохой", ["Плохой"], status="pending")
    with get_engine().begin() as c:
        c.execute(text("UPDATE people SET status = 'rejected' WHERE id = :i"), {"i": bad})
    f = _fact(me, "х")
    u = f"/api/people/{me}/facts/give"
    assert client.post(u, json={"ids": [f]}).status_code == 400                                             # некому
    assert client.post(u, json={"ids": [f], "to_person_id": nik, "new_person": {"name": "Х"}}).status_code == 400
    assert client.post(u, json={"ids": [f], "to_person_id": bad}).status_code == 400                         # отклонённому нельзя
    assert client.post(u, json={"ids": [f], "to_person_id": me}).status_code == 400                          # самому себе
    assert client.post(u, json={"ids": [f], "new_person": {"name": "Х", "relation": "враг"}}).status_code == 400
    other_user = insert_person("test-give-other", "Чужой", status="confirmed")
    try:
        assert client.post(u, json={"ids": [f], "to_person_id": other_user}).status_code == 400              # чужой человек
    finally:
        with get_engine().begin() as c:
            c.execute(delete(people).where(people.c.user_id == "test-give-other"))
    assert _row(f).person_id == me


def test_foreign_fact_is_skipped(client):
    me = _me()
    nik = prepo.create(MARK, "Игорь", ["Игорь"], status="confirmed")
    oly = prepo.create(MARK, "Оля", ["Оля"], status="confirmed")
    g = _fact(oly, "чужой")
    r = client.post(f"/api/people/{me}/facts/give", json={"ids": [g], "to_person_id": nik}).json()
    assert r["moved"] == 0 and r["skipped"] == 1 and _row(g).person_id == oly


def test_suggest_owner_from_same_chat_account_first(client):
    me = _me()
    by_acc = prepo.create(MARK, "Аккаунтов", ["Аккаунтов"], status="confirmed", relation="friend", about="сосед по дому")
    add_account_row(MARK, by_acc, "tg", 555, None, True)
    by_fact = prepo.create(MARK, "Фактов", ["Фактов"], status="confirmed")
    rejected = prepo.create(MARK, "Отклонён", ["Отклонён"], status="confirmed")
    with get_engine().begin() as c:
        c.execute(text("UPDATE people SET status = 'rejected' WHERE id = :i"), {"i": rejected})
    for pid in (by_fact, by_fact, rejected):
        mk = mk_fact(pid, text_="из чата", user=MARK)
        with get_engine().begin() as c:
            c.execute(text("UPDATE memories SET source_chat_id = 555 WHERE id = :i"), {"i": mk})
    f = _fact(me, "не любит жару", chat=555)
    got = client.post(f"/api/people/{me}/facts/suggest-owner", json={"ids": [f]}).json()["candidates"]
    assert [c["id"] for c in got] == [by_acc, by_fact]                                    # аккаунт чата первым, отклонённый не предлагается
    assert got[0]["label"] == "Аккаунтов — друг, сосед по дому" and got[0]["why"] == "аккаунт этого чата"
    assert client.post(f"/api/people/{me}/facts/suggest-owner", json={"ids": [_fact(me, "без чата")]}).json()["candidates"] == []
