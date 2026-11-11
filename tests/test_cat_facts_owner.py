"""tests/test_cat_facts_owner.py — владелец размечает «Неразобранное» из приложения cats: GET /api/cat-health/facts/unsorted, PATCH /api/cat-health/facts/{id}/block.

Токен здоровья (CATS_WRITE_TOKEN) — токен владельца. Вымышленные коты, реальная БД."""
from __future__ import annotations

import pytest
from sqlalchemy import delete, insert, text

from core.db import get_engine
from core.repos import pg_cats_repo as crepo
from core.repos.memories_table import memories
from core.repos.people_tables import people
from tests.test_cat_health_api import H, MARK, OTHER, READ, CATSREAD, _env, client  # noqa: F401

CAT = "🐾 Коты"
U = "/api/cat-health/facts/unsorted"


@pytest.fixture(autouse=True)
def _clean():
    yield
    with get_engine().begin() as c:
        c.execute(delete(memories).where(memories.c.user_id.in_([MARK, OTHER])))
        c.execute(delete(people).where(people.c.user_id.in_([MARK, OTHER])))


def fact(text_, cat_id=None, user=MARK, **kw):
    vals = dict(fact_text=text_, key_name="k", category=CAT, related_to="", user_id=user, review_status="confirmed", cat_id=cat_id)
    vals.update(kw)
    with get_engine().begin() as c:
        return c.execute(insert(memories).values(**vals).returning(memories.c.id)).scalar_one()


def block(i):
    with get_engine().connect() as c:
        return c.execute(text("SELECT cat_block FROM memories WHERE id = :i"), {"i": i}).scalar_one()


def test_unsorted_lists_null_and_unsorted_of_cats_and_general_only(client):
    cid = crepo.create(MARK, "Тучка")
    with get_engine().begin() as c:
        pid = c.execute(insert(people).values(user_id=MARK, name="Оля").returning(people.c.id)).scalar_one()
    a, b, g = fact("без блока", cid), fact("неразобранный", cid, cat_block="unsorted"), fact("общий без блока")
    fact("размечен", cid, cat_block="care"); fact("личное", cat_block="owner")
    fact("карточка человека", person_id=pid); fact("архив", cid, is_archived=True); fact("отклонён", cid, review_status="rejected")
    fact("чужой", user=OTHER); fact("не про котов", category="🏠 Быт"); fact("", cid)
    r = client.get(U, headers=H)
    assert r.status_code == 200 and set(r.json()) == {"facts"}
    got = {f["id"]: f for f in r.json()["facts"]}
    assert set(got) == {a, b, g} and got[a]["cat"] == "Тучка" and got[g]["cat"] is None
    assert all(set(f) == {"id", "text", "cat"} for f in got.values())                           # без user_id, ключей, блока, дат


def test_one_tap_sets_the_block_and_the_fact_leaves_the_list(client):
    cid = crepo.create(MARK, "Тучка")
    a = fact("без блока", cid)
    r = client.patch(f"/api/cat-health/facts/{a}/block", json={"block": "care"}, headers=H)
    assert r.status_code == 200 and r.json() == {"ok": True, "fact_id": a, "block": "care"} and block(a) == "care"
    assert client.get(U, headers=H).json()["facts"] == []


@pytest.mark.parametrize("bad", ["sitter", "", "Care", None, 5])
def test_unknown_block_is_422_and_nothing_changes(client, bad):
    a = fact("без блока")
    assert client.patch(f"/api/cat-health/facts/{a}/block", json={"block": bad}, headers=H).status_code == 422
    assert block(a) is None


def test_foreign_person_archived_and_missing_are_404(client):
    cid = crepo.create(MARK, "Тучка")
    with get_engine().begin() as c:
        pid = c.execute(insert(people).values(user_id=MARK, name="Оля").returning(people.c.id)).scalar_one()
    ids = [fact("чужой", user=OTHER), fact("человек", person_id=pid), fact("архив", cid, is_archived=True), fact("не про котов", category="🏠 Быт")]
    for i in ids + [999999999]:
        assert client.patch(f"/api/cat-health/facts/{i}/block", json={"block": "care"}, headers=H).status_code == 404
    assert [block(i) for i in ids] == [None] * 4


def test_only_the_health_token_opens_them(client):
    a = fact("без блока")
    for hdr in ({}, {"Authorization": f"Bearer {READ}"}, {"Authorization": f"Bearer {CATSREAD}"}, {"Authorization": "Bearer wrong"}):
        assert client.get(U, headers=hdr).status_code in (401, 403)
        assert client.patch(f"/api/cat-health/facts/{a}/block", json={"block": "care"}, headers=hdr).status_code in (401, 403)
    assert block(a) is None
