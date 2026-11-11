"""tests/test_sitter_blocks.py — ситтер видит факты про котов ТОЛЬКО по блокам: profile / care / relations, из health только «критично» (ключ important).

Одна и та же картина мира проходит через ВСЕ места, где факты про котов уходят наружу под служебными токенами: GET /api/sitter/cat-care, POST /api/sitter/ask,
GET /api/cats (+ /{id}) под читающим токеном приложения cats. Закрытые факты (owner, unsorted, NULL, health не «критично», факт карточки человека, чужой владелец,
архивные, отклонённые) нигде не появляются ни в тексте, ни в счётчиках. Перебор всех маршрутов служебными токенами сверяет тела ответов с метками закрытых фактов.
Вымышленные коты и животные-метки, реальных данных нет.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from fastapi.routing import APIRoute
from sqlalchemy import delete, insert, text

from core import cat_blocks
from core import sitter_expand as se
from core import sitter_search as ss
from core.cats import cat_fact_key
from core.db import get_engine
from core.repos import pg_cats_repo as crepo
from core.repos.cats_tables import cats
from core.repos.memories_table import memories
from core.repos.people_tables import people
from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id
from miniapp.backend.routes import sitter as sitter_routes
from tests.test_sitter_cat_care import CATS, HEALTH, MARK, OK, OTHER, OWNER, READ, WRITE, _env, client, fact, general, get  # noqa: F401  (фикстуры и помощники)

CARE_H = {"Authorization": f"Bearer {CATS}"}
OPEN_CAT = {"profile": "ягуар", "care": "жираф", "relations": "зебра", "critical": "верблюд"}
OPEN_GEN = {"profile": "тюлень", "care": "морж", "relations": "нарвал"}
CLOSED_CAT = ["носорог", "бизон", "лемур", "тапир", "гепард", "пингвин", "фламинго", "койот"]
CLOSED_GEN = ["ламантин", "выхухоль", "ленивец", "броненосец", "суслик"]
ALL_CLOSED = CLOSED_CAT + CLOSED_GEN


@pytest.fixture(autouse=True)
def _ask_env(monkeypatch):
    import core.claude_client as cc
    monkeypatch.setattr(sitter_routes, "_budget", ss.QueryBudget(limit=100000))
    monkeypatch.setattr(ss, "_embed", lambda texts, input_type="document": [])         # только FTS: сети нет
    monkeypatch.setattr(cc, "ask_claude", AsyncMock(return_value="-"))
    se._cache.clear()
    se._day.update(d="", n=0)
    from unittest.mock import patch
    with patch("miniapp.backend.routes.cats.get_user_id", AsyncMock(return_value=MARK)):
        yield
    with get_engine().begin() as c:
        c.execute(delete(people).where(people.c.user_id.in_([MARK, OTHER])))


def sentence(animal: str) -> str:
    return f"в комнате сидит {animal}, это важно знать"


def world():
    a = crepo.create(MARK, "Тучка")
    fact(a, sentence(OPEN_CAT["profile"]), "other", cat_block="profile")
    fact(a, sentence(OPEN_CAT["care"]), "other", cat_block="care")
    fact(a, sentence(OPEN_CAT["relations"]), "other", cat_block="relations")
    fact(a, sentence(OPEN_CAT["critical"]), "important", cat_block="health")                      # health + ключ important: открыт
    fact(a, sentence("носорог"), "other", cat_block="health")
    fact(a, sentence("бизон"), "other", cat_block="owner")
    fact(a, sentence("лемур"), "other", cat_block="unsorted")
    fact(a, sentence("тапир"), "other", cat_block=None)
    fact(a, sentence("гепард"), "other", cat_block="health", key_name="cat_%d_important_zzzzzzzz" % a)   # ключ-подделка: не «критично»
    fact(a, sentence("пингвин"), "other", cat_block="care", is_archived=True)
    fact(a, sentence("фламинго"), "other", cat_block="care", review_status="rejected")
    fact(None, sentence("койот"), "other", user=OTHER, cat_block="care")                          # чужой владелец (без кота)
    for b, animal in OPEN_GEN.items():
        general(sentence(animal), cat_block=b)
    general(sentence("ламантин") + ", нельзя, аллергия", cat_block="health")                      # общий health: «критичного» ключа нет → закрыт
    general(sentence("выхухоль"), cat_block="owner")
    general(sentence("ленивец"), cat_block="unsorted")
    general(sentence("броненосец"), cat_block=None)
    with get_engine().begin() as c:
        pid = c.execute(insert(people).values(user_id=MARK, name="Оля").returning(people.c.id)).scalar_one()
    general(sentence("суслик"), cat_block="care", person_id=pid)                                  # факт карточки человека, даже с блоком care
    return a


def open_texts():
    return [sentence(x) for x in list(OPEN_CAT.values()) + list(OPEN_GEN.values())]


def blob(resp) -> str:
    return json.dumps(resp.json(), ensure_ascii=False)


def assert_no_closed(s: str):
    leaked = [w for w in ALL_CLOSED if w in s]
    assert not leaked, leaked


# ── cat-care ─────────────────────────────────────────────────────────────────

def test_cat_care_gives_only_open_blocks_and_no_closed_text_anywhere(client):
    world()
    r = get(client)
    s = blob(r)
    assert_no_closed(s)
    for t in open_texts():
        assert t in s, t
    body = r.json()
    f = body["cats"][0]["facts"]
    assert sentence("верблюд") in f["important"]                                                    # «критично» из health
    assert body["blocks"] == {"profile": [sentence("тюлень")], "care": [sentence("морж")], "relations": [sentence("нарвал")]}
    assert sorted(body["general"]) == sorted(sentence(x) for x in OPEN_GEN.values())


def test_a_fact_without_a_block_is_closed_even_when_it_is_the_only_one(client):
    a = crepo.create(MARK, "Тучка")
    fact(a, "без блока", "other", cat_block=None)
    general("общий без блока", cat_block=None)
    body = get(client).json()
    assert body["cats"][0]["facts"] == {"important": [], "other": [], "likes": [], "dislikes": []} and body["general"] == [] and body["blocks"] == {"profile": [], "care": [], "relations": []}


# ── параллель SQL и Python ───────────────────────────────────────────────────

@pytest.mark.parametrize("block", [None, "profile", "care", "health", "relations", "owner", "unsorted"])
@pytest.mark.parametrize("key", ["", "k", "cat_1_important_aaaaaaaa", "cat_1_other_aaaaaaaa", "cat_1_important_zzzzzzzz", "cat_1_important_aaaaaaaaa", "xcat_1_important_aaaaaaaa"])
def test_sql_predicate_and_python_audience_never_disagree(block, key):
    with get_engine().begin() as c:
        mid = c.execute(insert(memories).values(fact_text="x", key_name=key, category="🐾 Коты", user_id=MARK, review_status="confirmed", cat_block=block)
                        .returning(memories.c.id)).scalar_one()
        sql_open = c.execute(text("SELECT %s FROM memories m WHERE m.id = :i" % cat_blocks.sitter_sql("m")), {"i": mid}).scalar()
    assert bool(sql_open) is (cat_blocks.audience(block, cat_blocks.critical_key(key)) == "sitter")


# ── читающий токен приложения cats (/api/cats) ───────────────────────────────

def test_cats_read_token_gets_only_open_facts_counts_and_no_block_key(client):
    a = world()
    full = client.get("/api/cats?with_facts=1", headers=CARE_H)
    card = client.get(f"/api/cats/{a}", headers=CARE_H)
    plain = client.get("/api/cats", headers=CARE_H)
    for r in (full, card, plain):
        assert r.status_code == 200
        assert_no_closed(blob(r))
        assert '"block"' not in blob(r)
    shown = [i["text"] for g in full.json()["cats"][0]["facts"] for i in g["items"]]
    assert sorted(shown) == sorted(sentence(OPEN_CAT[k]) for k in OPEN_CAT)
    assert full.json()["cats"][0]["facts_count"] == 4 and plain.json()["cats"][0]["facts_count"] == 4 and card.json()["facts_count"] == 4
    assert full.json()["general_count"] == plain.json()["general_count"] == 3                       # открытых общих, не всех (в том числе «Я»)


def test_owner_session_still_sees_everything_and_the_blocks(client):
    a = world()
    app.dependency_overrides[current_user_id] = lambda: OWNER
    try:
        from unittest.mock import patch
        with patch("miniapp.backend.routes.cats.get_user_id", AsyncMock(return_value=MARK)):
            r = client.get(f"/api/cats/{a}")
            lst = client.get("/api/cats?with_facts=1")
            g = client.get("/api/cats/general")
    finally:
        app.dependency_overrides.pop(current_user_id, None)
    s = blob(r)
    for w in ("носорог", "бизон", "лемур", "тапир", "ягуар"):
        assert w in s, w
    assert '"block": null' in s and '"block": "owner"' in s                                         # владелец видит и размеченное, и неразмеченное
    assert lst.json()["cats"][0]["facts_count"] == 9 and "носорог" in blob(lst) and '"block": "owner"' in blob(lst) and lst.json()["general_count"] == 7
    assert "ламантин" in blob(g) and "выхухоль" in blob(g)                                           # общие: и закрытые


# ── /api/sitter/ask ──────────────────────────────────────────────────────────

def ask(q):
    return TestClient(app).post("/api/sitter/ask", json={"q": q}, headers=OK)


def test_ask_finds_open_facts_and_never_a_closed_one(client):
    world()
    for animal in list(OPEN_CAT.values()) + list(OPEN_GEN.values()):
        r = ask(animal)
        assert r.status_code == 200 and animal in json.dumps(r.json(), ensure_ascii=False), animal
    for animal in ALL_CLOSED:
        r = ask(animal)
        assert r.status_code == 200 and not r.json()["found"], animal
        assert_no_closed(blob(r))


def test_ask_does_not_return_the_owners_own_card_facts_even_with_an_open_block(client):
    world()
    assert not ask("суслик").json()["found"]


# ── перебор всех маршрутов служебными токенами ───────────────────────────────

def test_every_route_opened_by_a_service_token_leaks_no_closed_fact(client):
    a = world()
    tc = TestClient(app, raise_server_exceptions=False)
    scanned = 0
    for tok in (OK, CARE_H, {"Authorization": f"Bearer {WRITE}"}, {"Authorization": f"Bearer {HEALTH}"}):
        for r in app.routes:
            if not isinstance(r, APIRoute) or not r.path.startswith("/api/"):
                continue
            path = r.path
            for name in getattr(r, "param_convertors", {}):
                path = path.replace("{%s}" % name, str(a if name == "cat_id" else 1))
            for method in sorted(r.methods - {"HEAD", "OPTIONS"}):
                kw = {"json": {"q": "кто сидит в комнате"}} if method in ("POST", "PUT", "PATCH", "DELETE") else {}
                got = tc.request(method, path, headers=tok, **kw)
                if got.status_code in (401, 403) or tc.request(method, path, **kw).status_code not in (401, 403):
                    continue                                                                           # токеном не открыто (или открыто и без него: не служебный доступ)
                scanned += 1
                assert_no_closed(got.text)
    assert scanned >= 4                                                                              # перебор реально что-то открыл (cat-care, ask, cats ×2, …)


def test_route_set_opened_by_the_read_tokens_did_not_grow():
    from tests.test_care_write import _opened_by
    assert _opened_by(OK) == {("GET", "/api/sitter/lists"), ("GET", "/api/care/schedule"), ("POST", "/api/sitter/ask"), ("GET", "/api/sitter/cat-care")}
    assert _opened_by(CARE_H) == {("GET", "/api/cats"), ("GET", "/api/cats/{cat_id}")}
