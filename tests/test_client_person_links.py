"""tests/test_client_person_links.py — связь клиент Арканы ↔ человек (ADR-0027, PR-8a).
Реальная тестовая БД. Privacy: generic фикстуры."""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, insert, text

from arcana.repos.clients_tables import clients
from core.db import get_engine
from core.people_links import parse_contact, rank_candidates
from core.repos import pg_client_links as cl
from core.repos import pg_people_repo as prepo
from core.repos.people_tables import people
from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id
from tests._people_helpers import insert_person

MARK = "test-client-links"
OTHER = "test-client-links-other"
TG = 700000002
TYPE = {"free": 1, "paid": 2, "self": 3}


def mk_client(name="Имя1", type_="paid", user=MARK, **kw) -> int:
    with get_engine().begin() as c:
        return c.execute(insert(clients).values(name=name, type_id=TYPE[type_], user_id=user, **kw)
                         .returning(clients.c.id)).scalar_one()


def person_of(cid):
    with get_engine().connect() as c:
        return c.execute(text("SELECT person_id FROM clients WHERE id=:i"), {"i": cid}).scalar()


@pytest.fixture(autouse=True)
def _clean():
    yield
    with get_engine().begin() as c:
        for u in (MARK, OTHER):
            c.execute(delete(clients).where(clients.c.user_id == u))
            c.execute(delete(people).where(people.c.user_id == u))


@pytest.fixture
def api():
    app.dependency_overrides[current_user_id] = lambda: TG
    with patch("miniapp.backend.routes.arcana_clients.get_user_id", AsyncMock(return_value=MARK)), \
         patch("miniapp.backend.routes.people.get_user_id", AsyncMock(return_value=MARK)):
        try:
            yield TestClient(app)
        finally:
            app.dependency_overrides.clear()


# ── parse_contact ────────────────────────────────────────────────────────────

def test_parse_contact_formats():
    r = parse_contact("@Some_Nick, t.me/other_nick, TG: 123456789, vk.com/id555, +7 000")
    assert r == {"usernames": {"some_nick", "other_nick"}, "tg_ids": {123456789}, "vk_ids": {555}}
    assert parse_contact(None) == {"usernames": set(), "tg_ids": set(), "vk_ids": set()}
    assert parse_contact("почта a@b.cc")["usernames"] == set()


# ── candidates ───────────────────────────────────────────────────────────────

def _people_view(user=MARK):
    return prepo.find_named(user)


def test_candidates_by_username_case_insensitive():
    pid = insert_person(MARK, "Имя2 Фамилия2", tg_id=111111, tg_username="Nick_One")
    insert_person(MARK, "Имя3 Фамилия3", tg_id=222222, tg_username="nick_two")
    res = rank_candidates({"name": "Кто-то", "contact": "@nick_one"}, _people_view())
    assert [c["person"]["id"] for c in res] == [pid] and res[0]["reasons"] == ["account"]


def test_candidates_by_tg_id_and_vk_id():
    a = insert_person(MARK, "Имя2 Ф2", tg_id=111111)
    b = insert_person(MARK, "Имя3 Ф3", vk_id=555)
    assert [c["person"]["id"] for c in rank_candidates({"name": "x", "contact": "TG: 111111"}, _people_view())] == [a]
    assert [c["person"]["id"] for c in rank_candidates({"name": "x", "contact": "vk.com/id555"}, _people_view())] == [b]


def test_candidates_by_name_and_name_birthday():
    pid = insert_person(MARK, "Имя2 Фамилия2", birthday=date(1990, 3, 5))
    r1 = rank_candidates({"name": "Фамилия2 Имя2", "contact": None}, _people_view())
    assert r1 and r1[0]["person"]["id"] == pid and "full_name" in r1[0]["reasons"]
    r2 = rank_candidates({"name": "Имя2", "contact": None, "birthday": date(1990, 3, 5)}, _people_view())
    assert r2 and "name_birthday" in r2[0]["reasons"]
    assert rank_candidates({"name": "Имя2", "contact": None, "birthday": date(1990, 3, 6)}, _people_view()) == []


def test_candidates_exclude_rejected_and_self_and_taken():
    rej = insert_person(MARK, "Имя2 Ф2", tg_id=111111, status="rejected")
    me = insert_person(MARK, "Имя3 Ф3", tg_id=222222, is_self=True)
    free = insert_person(MARK, "Имя4 Ф4", tg_id=333333)
    taken = insert_person(MARK, "Имя5 Ф5", tg_id=444444)
    mk_client("Занят", person_id=taken)
    cid = mk_client("Имя", contact="TG: 111111 TG: 222222 TG: 333333 TG: 444444")
    res = cl.candidates(MARK, cid)
    assert [c["person"]["id"] for c in res["candidates"]] == [free]
    assert rej and me


def test_self_client_candidate_only_me():
    me = insert_person(MARK, "Имя1", is_self=True)
    insert_person(MARK, "Имя1")
    cid = mk_client("Имя1", type_="self")
    assert [c["person"]["id"] for c in cl.candidates(MARK, cid)["candidates"]] == [me]


# ── ensure_self_link ─────────────────────────────────────────────────────────

def test_ensure_self_link_idempotent_and_only_self_type():
    me = insert_person(MARK, "Я", is_self=True)
    s = mk_client("Я", type_="self")
    other = mk_client("Платная", type_="paid")
    assert cl.ensure_self_link(MARK) == {"client_id": s, "person_id": me}
    assert cl.ensure_self_link(MARK) is None
    assert person_of(s) == me and person_of(other) is None


def test_ensure_self_link_without_self_person_or_client():
    mk_client("Я", type_="self")
    assert cl.ensure_self_link(MARK) is None            # нет «Я»
    insert_person(MARK, "Я", is_self=True)
    assert cl.ensure_self_link(OTHER) is None


# ── link / unlink ────────────────────────────────────────────────────────────

def test_link_unlink_and_uniqueness():
    pid = insert_person(MARK, "Имя2")
    c1, c2 = mk_client("A"), mk_client("B")
    assert cl.link(MARK, c1, pid)["id"] == pid
    with pytest.raises(cl.LinkError) as e:
        cl.link(MARK, c2, pid)
    assert e.value.code == "person_taken"
    with pytest.raises(cl.LinkError) as e:
        cl.link(MARK, c1, insert_person(MARK, "Имя3"))
    assert e.value.code == "already_linked"
    cl.unlink(MARK, c1)
    assert person_of(c1) is None
    assert cl.link(MARK, c2, pid)["id"] == pid


def test_link_rejects_rejected_foreign_and_self_client():
    rej = insert_person(MARK, "Имя2", status="rejected")
    foreign = insert_person(OTHER, "Имя3")
    c = mk_client("A")
    for pid, code in ((rej, "person_rejected"), (foreign, "person_not_found")):
        with pytest.raises(cl.LinkError) as e:
            cl.link(MARK, c, pid)
        assert e.value.code == code
    s = mk_client("Я", type_="self")
    with pytest.raises(cl.LinkError) as e:
        cl.link(MARK, s, insert_person(MARK, "Имя4"))
    assert e.value.code == "self_client"
    with pytest.raises(cl.LinkError) as e:
        cl.unlink(MARK, s)
    assert e.value.code == "self_client"


def test_person_delete_sets_null():
    pid = insert_person(MARK, "Имя2")
    c = mk_client("A", person_id=pid)
    with get_engine().begin() as conn:
        conn.execute(delete(people).where(people.c.id == pid))
    assert person_of(c) is None


# ── merge / absorb ───────────────────────────────────────────────────────────

def test_merge_moves_client_link_to_target():
    src, dst = insert_person(MARK, "Имя2"), insert_person(MARK, "Имя3")
    c = mk_client("A", person_id=src)
    prepo.merge_people(MARK, src, dst)
    assert person_of(c) == dst


def test_merge_keeps_target_link_when_only_target_has_client():
    src, dst = insert_person(MARK, "Имя2"), insert_person(MARK, "Имя3")
    c = mk_client("A", person_id=dst)
    prepo.merge_people(MARK, src, dst)
    assert person_of(c) == dst


def test_merge_conflict_when_both_have_clients():
    src, dst = insert_person(MARK, "Имя2"), insert_person(MARK, "Имя3")
    a, b = mk_client("A", person_id=src), mk_client("B", person_id=dst)
    with pytest.raises(prepo.MergeError) as e:
        prepo.merge_people(MARK, src, dst)
    assert e.value.code == "both_clients"
    assert person_of(a) == src and person_of(b) == dst
    with get_engine().connect() as c:                         # транзакция откатилась: источник жив
        assert c.execute(text("SELECT count(*) FROM people WHERE id=:i"), {"i": src}).scalar() == 1


def test_absorb_into_self_moves_link_or_conflicts():
    me = insert_person(MARK, "Я", is_self=True)
    src = insert_person(MARK, "Имя2")
    c = mk_client("A", person_id=src)
    prepo.absorb_into_self(MARK, src)
    assert person_of(c) == me
    src2 = insert_person(MARK, "Имя3")
    mk_client("B", person_id=src2)
    with pytest.raises(ValueError):
        prepo.absorb_into_self(MARK, src2)


# ── API ──────────────────────────────────────────────────────────────────────

def test_api_candidates_link_unlink_and_ownership(api):
    pid = insert_person(MARK, "Имя2 Фамилия2", tg_id=111111, tg_username="nick_one")
    cid = mk_client("Кто-то", contact="@nick_one")
    r = api.get(f"/api/arcana/clients/{cid}/person-candidates")
    assert r.status_code == 200 and [c["id"] for c in r.json()["candidates"]] == [pid]
    assert api.post(f"/api/arcana/clients/{cid}/link", json={"person_id": pid}).status_code == 200
    assert api.get(f"/api/arcana/clients/{cid}/person-candidates").json()["linked"]["id"] == pid
    assert api.post(f"/api/arcana/clients/{cid}/link", json={"person_id": pid}).status_code == 409
    assert api.delete(f"/api/arcana/clients/{cid}/link").status_code == 200
    assert person_of(cid) is None
    foreign_client = mk_client("Чужой", user=OTHER)
    assert api.get(f"/api/arcana/clients/{foreign_client}/person-candidates").status_code == 404
    assert api.post(f"/api/arcana/clients/{foreign_client}/link", json={"person_id": pid}).status_code == 404
    assert api.post(f"/api/arcana/clients/{cid}/link", json={}).status_code == 422
    assert api.post("/api/arcana/clients/abc/link", json={"person_id": pid}).status_code == 404


def test_api_people_exposes_client_id_and_self_autolink(api):
    me = insert_person(MARK, "Я", is_self=True)
    s = mk_client("Я", type_="self")
    other = insert_person(MARK, "Имя2")
    c = mk_client("A", person_id=other)
    rows = {p["id"]: p for p in api.get("/api/people").json()["people"]}
    assert rows[me]["client_id"] == s and rows[other]["client_id"] == c
    assert api.get(f"/api/people/{other}").json()["client_id"] == c


def test_api_merge_both_clients_409(api):
    a, b = insert_person(MARK, "Имя2"), insert_person(MARK, "Имя3")
    mk_client("A", person_id=a)
    mk_client("B", person_id=b)
    r = api.post(f"/api/people/{a}/merge", json={"into_id": b})
    assert r.status_code == 409
