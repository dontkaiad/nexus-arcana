"""tests/test_people_api.py — /api/people (ADR-0027, PR-4): список, карточка, PATCH,
ревью фактов. Реальная тестовая БД (DATABASE_URL), владелец подменяется через get_user_id.
Privacy: generic фикстуры."""
from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, insert, inspect, text
from sqlalchemy.exc import IntegrityError

from core.db import get_engine
from core.people import age, birthday_iso
from core.repos.memories_table import memories
from core.repos.people_tables import people
from core.repos.pg_memory_repo import PgMemoryRepo
from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id

REPO = Path(__file__).resolve().parent.parent
MARK = "test-people-api"
OTHER = "test-people-api-other"
TG = 700000001


@pytest.fixture
def owner():
    return {"uid": MARK}


@pytest.fixture
def client(owner):
    app.dependency_overrides[current_user_id] = lambda: TG
    with patch("miniapp.backend.routes.people.get_user_id",
               AsyncMock(side_effect=lambda tg: owner["uid"])):
        try:
            yield TestClient(app)
        finally:
            app.dependency_overrides.clear()
            with get_engine().begin() as c:
                for u in (MARK, OTHER):
                    c.execute(delete(memories).where(memories.c.user_id == u))
                    c.execute(delete(people).where(people.c.user_id == u))


def mk_person(name="Имя1 Фамилия1", user=MARK, **kw) -> int:
    with get_engine().begin() as c:
        return c.execute(insert(people).values(user_id=user, name=name, **kw).returning(people.c.id)).scalar_one()


def mk_fact(pid, type_="likes", status="pending", text_="факт", user=MARK, n=None, **kw) -> int:
    import uuid
    key = "person_%d_%s_%s" % (pid, type_, (n or uuid.uuid4().hex)[:8])
    with get_engine().begin() as c:
        return c.execute(insert(memories).values(
            fact_text=text_, key_name=key, category="👥 Люди", related_to="x", person_id=pid,
            review_status=status, user_id=user, source="auto", **kw).returning(memories.c.id)).scalar_one()


# ── age / birthday_iso ───────────────────────────────────────────────────────

def test_age_none_without_year_or_birthday():
    assert age(None) is None
    assert age(date(1904, 3, 5), year_known=False) is None
    assert age(date(1904, 3, 5), year_known=True) is None       # заглушка не считается годом
    assert age(date(1990, 3, 5), year_known=False) is None


def test_age_counts_full_years():
    assert age(date(1990, 6, 15), True, today=date(2026, 6, 14)) == 35
    assert age(date(1990, 6, 15), True, today=date(2026, 6, 15)) == 36
    assert age(date(2030, 1, 1), True, today=date(2026, 1, 1)) is None


def test_birthday_iso_hides_sentinel_year():
    assert birthday_iso(date(1990, 3, 5)) == "1990-03-05"
    assert birthday_iso(date(1904, 3, 5), False) == "--03-05"
    assert birthday_iso(date(1904, 3, 5)) == "--03-05"
    assert birthday_iso(None) is None


# ── миграция ─────────────────────────────────────────────────────────────────

def test_migration_marks_1904_unknown_and_allows_rejected():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = next((REPO / "alembic" / "versions").glob("*_people_birthday_year_known.py"))
    spec = importlib.util.spec_from_file_location("_mig_year", path)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    assert mig.down_revision == "b776c8f51671"
    with get_engine().connect() as conn:
        trans = conn.begin()
        try:
            with Operations.context(MigrationContext.configure(conn)):
                mig.downgrade()
                assert "birthday_year_known" not in [c["name"] for c in inspect(conn).get_columns("people")]
                with pytest.raises(IntegrityError):
                    with conn.begin_nested():
                        conn.execute(text("INSERT INTO people (user_id,name,status) VALUES ('t','x','rejected')"))
                conn.execute(text("INSERT INTO people (user_id,name,birthday) VALUES ('t','a','1904-03-05'),"
                                  "('t','b','1990-03-05'),('t','c',NULL)"))
                mig.upgrade()
                rows = dict(conn.execute(text(
                    "SELECT name, birthday_year_known FROM people WHERE user_id='t'")).all())
                assert rows == {"a": False, "b": True, "c": True}
                conn.execute(text("INSERT INTO people (user_id,name,status) VALUES ('t','r','rejected')"))
        finally:
            trans.rollback()


# ── список ───────────────────────────────────────────────────────────────────

def test_list_sorted_by_pending_desc_with_counts(client):
    a = mk_person("Аа Аа"); b = mk_person("Бб Бб"); c = mk_person("Вв Вв")
    mk_fact(a, status="pending"); 
    for _ in range(3):
        mk_fact(b, "important", "pending")
    mk_fact(b, "likes", "confirmed"); mk_fact(c, "likes", "confirmed")
    r = client.get("/api/people").json()
    assert [p["id"] for p in r["people"]] == [b, a, c]
    pb = r["people"][0]
    assert pb["counts"] == {"pending": 3, "confirmed": 1, "rejected": 0}
    assert pb["by_type"]["important"]["pending"] == 3 and pb["by_type"]["likes"]["confirmed"] == 1
    assert set(pb) >= {"name", "aliases", "birthday", "age", "status"}


def test_list_hides_rejected_by_default_and_filters_by_status_and_q(client):
    a = mk_person("Мария Иванова", aliases=["Машка", "@masha"]); b = mk_person("Пётр Петров")
    r_ = mk_person("Скрытый Человек", status="rejected")
    ids = lambda r: {p["id"] for p in r.json()["people"]}
    assert ids(client.get("/api/people")) == {a, b}
    assert ids(client.get("/api/people?status=rejected")) == {r_}
    assert ids(client.get("/api/people?q=ивано")) == {a}
    assert ids(client.get("/api/people?q=машк")) == {a}          # по алиасу
    assert ids(client.get("/api/people?q=%25")) == set()          # % — не wildcard
    assert client.get("/api/people?status=bogus").status_code == 422


def test_list_is_scoped_to_owner(client, owner):
    mine = mk_person("Мой Человек"); mk_person("Чужой Человек", user=OTHER)
    assert {p["id"] for p in client.get("/api/people").json()["people"]} == {mine}


# ── карточка ─────────────────────────────────────────────────────────────────

def test_card_groups_facts_important_first_and_hides_sentinel_year(client):
    pid = mk_person(birthday=date(1904, 3, 5), birthday_year_known=False)
    mk_fact(pid, "likes", "confirmed", "л"); mk_fact(pid, "dislikes", "pending", "д")
    mk_fact(pid, "important", "pending", "в"); mk_fact(pid, "other", "rejected", "о")
    arch = mk_fact(pid, "likes", "pending", "архив", is_archived=True)
    r = client.get("/api/people/%d" % pid).json()
    assert [g["type"] for g in r["facts"]] == ["important", "likes", "dislikes", "other"]
    assert r["birthday"] == "--03-05" and r["age"] is None and r["birthday_year_known"] is False
    assert arch not in [f["id"] for g in r["facts"] for f in g["items"]]
    assert r["facts"][3]["items"][0]["review_status"] == "rejected"


def test_card_age_when_year_known(client):
    pid = mk_person(birthday=date(1990, 1, 1))
    assert isinstance(client.get("/api/people/%d" % pid).json()["age"], int)


def test_card_404_for_missing_and_foreign(client):
    foreign = mk_person(user=OTHER)
    assert client.get("/api/people/%d" % foreign).status_code == 404
    assert client.get("/api/people/999999999").status_code == 404


def test_no_owner_is_403(client, owner):
    owner["uid"] = None
    assert client.get("/api/people").status_code == 403


# ── PATCH ────────────────────────────────────────────────────────────────────

def test_patch_updates_only_sent_fields(client):
    pid = mk_person("Старое Имя", aliases=["А"], notes="n")
    r = client.patch("/api/people/%d" % pid, json={"name": " Новое Имя ", "aliases": ["Б", " ", "В"],
                                                   "status": "confirmed"}).json()
    assert r["name"] == "Новое Имя" and r["aliases"] == ["Б", "В"] and r["status"] == "confirmed"
    assert r["notes"] == "n"                                       # не присылали — не тронуто


def test_patch_birthday_flow(client):
    pid = mk_person()
    r = client.patch("/api/people/%d" % pid, json={"birthday": "1990-06-07"}).json()
    assert r["birthday"] == "1990-06-07" and r["birthday_year_known"] is True and r["age"] is not None
    r = client.patch("/api/people/%d" % pid, json={"birthday": "1990-06-07", "birthday_year_known": False}).json()
    assert r["birthday"] == "--06-07" and r["age"] is None
    with get_engine().connect() as c:
        stored = c.execute(text("SELECT birthday FROM people WHERE id=:i"), {"i": pid}).scalar_one()
    assert stored == date(1904, 6, 7)
    r = client.patch("/api/people/%d" % pid, json={"birthday": None}).json()
    assert r["birthday"] is None and r["birthday_year_known"] is True


def test_patch_validation_and_conflicts(client):
    a = mk_person(tg_id=555001); b = mk_person()
    assert client.patch("/api/people/%d" % b, json={"status": "weird"}).status_code == 422
    assert client.patch("/api/people/%d" % b, json={"name": "  "}).status_code == 422
    assert client.patch("/api/people/%d" % b, json={"tg_id": 555001}).status_code == 409
    assert client.patch("/api/people/%d" % b, json={"tg_id": 555002}).status_code == 200
    foreign = mk_person(user=OTHER)
    assert client.patch("/api/people/%d" % foreign, json={"name": "x"}).status_code == 404


# ── ревью фактов ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_approve_makes_fact_visible_to_bot_and_reject_hides(client):
    pid = mk_person()
    f = mk_fact(pid, text_="зюзябрикс-факт")
    repo = PgMemoryRepo()
    assert await repo.search(["зюзябрикс"], user_id=MARK) == []
    r = client.post("/api/people/%d/facts/%d/approve" % (pid, f)).json()
    assert r["review_status"] == "confirmed" and r["counts"]["confirmed"] == 1
    assert len(await repo.search(["зюзябрикс"], user_id=MARK)) == 1
    r = client.post("/api/people/%d/facts/%d/reject" % (pid, f)).json()
    assert r["review_status"] == "rejected" and r["counts"]["rejected"] == 1
    assert await repo.search(["зюзябрикс"], user_id=MARK) == []


def test_fact_review_checks_ownership_and_person_match(client):
    a = mk_person(); b = mk_person()
    fact_a = mk_fact(a)
    foreign_p = mk_person(user=OTHER); foreign_f = mk_fact(foreign_p, user=OTHER)
    assert client.post("/api/people/%d/facts/%d/approve" % (b, fact_a)).status_code == 404   # факт чужого человека
    assert client.post("/api/people/%d/facts/%d/approve" % (foreign_p, foreign_f)).status_code == 404
    assert client.post("/api/people/%d/facts/999999999/reject" % a).status_code == 404


def test_approve_all_confirms_pending_and_person_only(client):
    pid = mk_person()
    p1 = mk_fact(pid, status="pending"); p2 = mk_fact(pid, "important", "pending")
    rej = mk_fact(pid, status="rejected"); conf = mk_fact(pid, status="confirmed")
    r = client.post("/api/people/%d/approve-all" % pid).json()
    assert r["changed"] == 2 and r["status"] == "confirmed"
    assert r["counts"] == {"pending": 0, "confirmed": 3, "rejected": 1}
    with get_engine().connect() as c:
        st = dict(c.execute(text("SELECT id, review_status FROM memories WHERE person_id=:p"), {"p": pid}).all())
    assert st[rej] == "rejected" and st[p1] == st[p2] == st[conf] == "confirmed"


def test_reject_all_rejects_pending_and_person_and_hides_from_list(client):
    pid = mk_person()
    mk_fact(pid, status="pending"); keep = mk_fact(pid, status="confirmed")
    r = client.post("/api/people/%d/reject-all" % pid).json()
    assert r["changed"] == 1 and r["status"] == "rejected"
    assert r["counts"] == {"pending": 0, "confirmed": 1, "rejected": 1}
    assert pid not in {p["id"] for p in client.get("/api/people").json()["people"]}
    assert keep                                                   # подтверждённый факт не тронут


def test_review_all_scoped_and_404(client):
    foreign = mk_person(user=OTHER); f = mk_fact(foreign, user=OTHER)
    assert client.post("/api/people/%d/approve-all" % foreign).status_code == 404
    with get_engine().connect() as c:
        assert c.execute(text("SELECT review_status FROM memories WHERE id=:i"), {"i": f}).scalar_one() == "pending"
    assert client.post("/api/people/999999999/reject-all").status_code == 404


# ── PR-6: батч-ревью, ручной факт, цитата ────────────────────────────────────

def _statuses(pid):
    with get_engine().connect() as c:
        return dict(c.execute(text("SELECT id, review_status FROM memories WHERE person_id=:p"), {"p": pid}).all())


def test_review_batch_applies_everything_in_one_go(client):
    pid = mk_person()
    a, b, c_ = mk_fact(pid), mk_fact(pid, "important"), mk_fact(pid, "dislikes")
    r = client.post("/api/people/%d/review" % pid, json={"approve": [a, b], "reject": [c_]})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "confirmed"
    assert r.json()["counts"] == {"pending": 0, "confirmed": 2, "rejected": 1}
    assert _statuses(pid) == {a: "confirmed", b: "confirmed", c_: "rejected"}


@pytest.mark.asyncio
async def test_review_batch_visibility_to_bot(client):
    pid = mk_person()
    ok = mk_fact(pid, text_="зюзябрикс-принят"); bad = mk_fact(pid, text_="зюзябрикс-снят")
    client.post("/api/people/%d/review" % pid, json={"approve": [ok], "reject": [bad]})
    got = {m.fact for m in await PgMemoryRepo().search(["зюзябрикс"], user_id=MARK)}
    assert got == {"зюзябрикс-принят"}


def test_review_batch_undo_restores_pending(client):
    pid = mk_person()
    a, b = mk_fact(pid), mk_fact(pid)
    client.post("/api/people/%d/review" % pid, json={"approve": [a], "reject": [b]})
    r = client.post("/api/people/%d/review" % pid, json={"reset": [a, b], "person_status": "pending"})
    assert r.json()["status"] == "pending" and r.json()["counts"]["pending"] == 2
    assert _statuses(pid) == {a: "pending", b: "pending"}


def test_review_batch_is_atomic_on_foreign_or_unknown_fact(client):
    pid = mk_person(); mine = mk_fact(pid)
    other_p = mk_person(); theirs = mk_fact(other_p)
    foreign_p = mk_person(user=OTHER); foreign = mk_fact(foreign_p, user=OTHER)
    for bad in (theirs, foreign, 999999999):
        r = client.post("/api/people/%d/review" % pid, json={"approve": [mine], "reject": [bad]})
        assert r.status_code == 404
    assert _statuses(pid) == {mine: "pending"}                     # approve из того же запроса не применился
    assert client.get("/api/people/%d" % pid).json()["status"] == "pending"


def test_review_batch_validation(client):
    pid = mk_person(); f = mk_fact(pid)
    assert client.post("/api/people/%d/review" % pid, json={"approve": [f], "reject": [f]}).status_code == 422
    assert client.post("/api/people/%d/review" % pid, json={"person_status": "weird"}).status_code == 422
    assert client.post("/api/people/999999999/review", json={}).status_code == 404
    foreign = mk_person(user=OTHER)
    assert client.post("/api/people/%d/review" % foreign, json={}).status_code == 404
    # пустой батч = просто подтвердить человека без фактов
    assert client.post("/api/people/%d/review" % pid, json={}).json()["status"] == "confirmed"


def test_card_exposes_quote(client):
    pid = mk_person()
    mk_fact(pid, source_quote="цитата-основание")
    mk_fact(pid, "other")
    items = client.get("/api/people/%d" % pid).json()["facts"]
    quotes = sorted((i["quote"] or "") for g in items for i in g["items"])
    assert quotes == ["", "цитата-основание"]


@pytest.mark.asyncio
async def test_manual_fact_is_confirmed_visible_idempotent(client):
    pid = mk_person("Мария Иванова", aliases=["Машка"])
    r = client.post("/api/people/%d/facts" % pid, json={"text": "  зюзябрикс  любит чай ", "type": "likes"})
    assert r.status_code == 200, r.text
    fid = r.json()["fact_id"]
    assert r.json()["counts"]["confirmed"] == 1
    with get_engine().connect() as c:
        row = c.execute(text("SELECT fact_text, key_name, source, review_status, related_to, scope, person_id "
                             "FROM memories WHERE id=:i"), {"i": fid}).one()
    assert row.fact_text == "Мария: зюзябрикс любит чай"          # имя подставлено
    assert row.key_name.startswith("person_%d_likes_" % pid)
    assert (row.source, row.review_status, row.scope, row.person_id) == ("manual", "confirmed", "nexus", pid)
    assert row.related_to == "Мария Иванова | Машка"
    assert [m.id for m in await PgMemoryRepo().search(["зюзябрикс"], user_id=MARK)] == [str(fid)]
    again = client.post("/api/people/%d/facts" % pid, json={"text": "зюзябрикс любит чай", "type": "likes"}).json()
    assert again["fact_id"] == fid and again["counts"]["confirmed"] == 1


def test_manual_fact_validation_and_resurrects_rejected(client):
    pid = mk_person()
    assert client.post("/api/people/%d/facts" % pid, json={"text": " ", "type": "likes"}).status_code == 422
    assert client.post("/api/people/%d/facts" % pid, json={"text": "x", "type": "weird"}).status_code == 422
    assert client.post("/api/people/999999999/facts", json={"text": "x"}).status_code == 404
    foreign = mk_person(user=OTHER)
    assert client.post("/api/people/%d/facts" % foreign, json={"text": "x"}).status_code == 404
    fid = client.post("/api/people/%d/facts" % pid, json={"text": "аллергия на орехи", "type": "important"}).json()["fact_id"]
    client.post("/api/people/%d/facts/%d/reject" % (pid, fid))
    client.post("/api/people/%d/facts" % pid, json={"text": "аллергия на орехи", "type": "important"})
    assert _statuses(pid)[fid] == "confirmed"                     # повторный ручной ввод возвращает факт


def test_patch_name_refreshes_related_to_of_facts(client):
    pid = mk_person("Старое Имя", aliases=["А"]); f = mk_fact(pid)
    client.patch("/api/people/%d" % pid, json={"name": "Новое Имя", "aliases": ["Б"]})
    with get_engine().connect() as c:
        assert c.execute(text("SELECT related_to FROM memories WHERE id=:i"), {"i": f}).scalar_one() == "Новое Имя | Б"
