"""tests/test_self_dedup_api.py — «Похожие» в карточке «Я»: предложения, объединить, «Это разное», разъединить.
Реальная тестовая БД; векторы задаются руками (1024-мерные, два направления), generic-фикстуры."""
from __future__ import annotations

from sqlalchemy import text

from core.db import get_engine
from tests.test_people_api import MARK, client, mk_fact, mk_person, owner  # noqa: F401


def _vec(i: int) -> str:
    v = [0.0] * 1024
    v[i] = 1.0
    return "[" + ",".join(str(x) for x in v) + "]"


def _set_vec(fid: int, i: int, tweak: float = 0.0):
    v = [0.0] * 1024
    v[i] = 1.0
    v[(i + 1) % 1024] = tweak
    with get_engine().begin() as c:
        c.execute(text("UPDATE memories SET embedding = CAST(:e AS vector) WHERE id = :i"),
                  {"e": "[" + ",".join(str(x) for x in v) + "]", "i": fid})


def _world():
    me = mk_person("Я", is_self=True, status="confirmed")
    a = mk_fact(me, text_="любит чай", status="pending")
    b = mk_fact(me, text_="обожает чай", status="pending", mention_count=3)
    c_ = mk_fact(me, text_="не любит чай", status="pending")                     # другая полярность: в группу не входит
    d = mk_fact(me, text_="играет в шахматы", status="pending")
    _set_vec(a, 0)
    _set_vec(b, 0, 0.05)
    _set_vec(c_, 0, 0.02)
    _set_vec(d, 5)
    return me, a, b, c_, d


def test_similar_groups_same_polarity_only(client):
    me, a, b, c_, d = _world()
    r = client.get(f"/api/people/{me}/similar").json()
    assert r["count"] == 1
    g = r["groups"][0]
    assert sorted(i["id"] for i in g["items"]) == sorted([a, b]) and c_ not in [i["id"] for i in g["items"]]
    assert g["keep_id"] == b                                   # больше упоминаний; ручной факт приоритетнее — отдельный тест


def test_manual_fact_is_default_keep(client):
    me, a, b, *_ = _world()
    with get_engine().begin() as c:
        c.execute(text("UPDATE memories SET source = 'manual' WHERE id = :i"), {"i": a})
    assert client.get(f"/api/people/{me}/similar").json()["groups"][0]["keep_id"] == a


def test_merge_hides_child_and_shows_it_under_survivor(client):
    me, a, b, *_ = _world()
    assert client.post(f"/api/people/{me}/similar/merge", json={"keep_id": b, "ids": [a, b]}).json()["merged"] == 1
    facts = [f for g in client.get(f"/api/people/{me}").json()["facts"] for f in g["items"]]
    ids = [f["id"] for f in facts]
    assert a not in ids and b in ids
    surv = next(f for f in facts if f["id"] == b)
    assert surv["mention_count"] == 4 and [m["id"] for m in surv["merged"]] == [a]    # 3 + 1
    assert client.get(f"/api/people/{me}/similar").json()["count"] == 0


def test_unmerge_restores_and_does_not_resuggest(client):
    me, a, b, *_ = _world()
    client.post(f"/api/people/{me}/similar/merge", json={"keep_id": b, "ids": [a, b]})
    assert client.post(f"/api/people/{me}/similar/unmerge", json={"keep_id": b}).json()["restored"] == 1
    ids = [f["id"] for g in client.get(f"/api/people/{me}").json()["facts"] for f in g["items"]]
    assert a in ids and b in ids
    assert client.get(f"/api/people/{me}/similar").json()["count"] == 0          # разъединили — значит «разное»


def test_dismiss_hides_pair(client):
    me, a, b, *_ = _world()
    assert client.post(f"/api/people/{me}/similar/dismiss", json={"ids": [a, b]}).json()["pairs"] == 1
    assert client.get(f"/api/people/{me}/similar").json()["count"] == 0


def test_merge_rejects_foreign_and_hidden(client):
    me, a, b, *_ = _world()
    other = mk_person("Имя2 Фамилия2")
    x = mk_fact(other, text_="любит чай")
    assert client.post(f"/api/people/{me}/similar/merge", json={"keep_id": a, "ids": [a, x]}).status_code == 404
    client.post(f"/api/people/{me}/similar/merge", json={"keep_id": b, "ids": [a, b]})
    assert client.post(f"/api/people/{me}/similar/merge", json={"keep_id": a, "ids": [a, b]}).status_code == 409


def test_chained_merge_moves_children_to_new_survivor(client):
    me, a, b, c_, d = _world()
    e = mk_fact(me, text_="любит чёрный чай", status="pending")
    _set_vec(e, 0, 0.03)
    client.post(f"/api/people/{me}/similar/merge", json={"keep_id": b, "ids": [a, b]})
    client.post(f"/api/people/{me}/similar/merge", json={"keep_id": e, "ids": [b, e]})
    with get_engine().connect() as c:
        got = dict(c.execute(text("SELECT id, merged_into FROM memories WHERE id = ANY(:i)"), {"i": [a, b, e]}).all())
    assert got == {a: e, b: e, e: None}
