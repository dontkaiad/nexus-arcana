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
from tests._people_helpers import insert_person
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
    return insert_person(user, name, **kw)


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


def test_patch_validation_and_ignores_removed_account_fields(client):
    b = mk_person()
    assert client.patch("/api/people/%d" % b, json={"status": "weird"}).status_code == 422
    assert client.patch("/api/people/%d" % b, json={"name": "  "}).status_code == 422
    r = client.patch("/api/people/%d" % b, json={"tg_id": 555002, "name": "Новое"})       # поля аккаунтов убраны из PATCH
    assert r.status_code == 200 and r.json()["name"] == "Новое" and r.json()["accounts"] == []
    foreign = mk_person(user=OTHER)
    assert client.patch("/api/people/%d" % foreign, json={"name": "x"}).status_code == 404


# ── аккаунты человека (PR-7) ────────────────────────────────────────────────

def test_card_exposes_accounts_with_urls_and_derived_primary_fields(client):
    pid = mk_person(tg_id=11, tg_username="nick", vk_id=22)
    add = client.post("/api/people/%d/accounts" % pid, json={"platform": "tg", "external_id": 33})
    assert add.status_code == 200
    got = client.get("/api/people/%d" % pid).json()
    accs = {(a["platform"], a["external_id"]): a for a in got["accounts"]}
    assert accs[("tg", 11)]["url"] == "https://t.me/nick" and accs[("tg", 11)]["is_primary"] is True
    assert accs[("vk", 22)]["url"] == "https://vk.com/id22"
    assert accs[("tg", 33)]["url"] is None and accs[("tg", 33)]["is_primary"] is False   # только id — ссылки нет
    assert (got["tg_id"], got["tg_username"], got["vk_id"]) == (11, "nick", 22)
    row = [p for p in client.get("/api/people").json()["people"] if p["id"] == pid][0]
    assert len(row["accounts"]) == 3 and row["tg_id"] == 11


def test_add_account_validation_conflicts_and_ownership(client):
    a = mk_person(tg_id=555001); b = mk_person()
    assert client.post("/api/people/%d/accounts" % b, json={"platform": "tg", "external_id": 555001}).status_code == 409
    assert client.post("/api/people/%d/accounts" % b, json={"platform": "vk", "external_id": 555001}).status_code == 200   # другая платформа
    assert client.post("/api/people/%d/accounts" % b, json={"platform": "ok", "external_id": 1}).status_code == 422
    assert client.post("/api/people/%d/accounts" % b, json={"platform": "tg", "external_id": 0}).status_code == 422
    assert client.post("/api/people/%d/accounts" % b, json={"platform": "vk", "external_id": 5, "username": "x"}).status_code == 422
    assert client.post("/api/people/999999999/accounts", json={"platform": "tg", "external_id": 7}).status_code == 404
    foreign = mk_person(user=OTHER)
    assert client.post("/api/people/%d/accounts" % foreign, json={"platform": "tg", "external_id": 8}).status_code == 404
    mk_person(user=OTHER, tg_id=999)                                                        # у другого владельца тот же id — не конфликт
    assert client.post("/api/people/%d/accounts" % a, json={"platform": "tg", "external_id": 999}).status_code == 200
    r = client.post("/api/people/%d/accounts" % b, json={"platform": "tg", "external_id": 31, "username": "@With_At"})
    assert [x["username"] for x in r.json()["accounts"] if x["external_id"] == 31] == ["With_At"]   # @ срезается


def test_first_account_of_platform_is_primary_and_primary_can_be_switched(client):
    pid = mk_person()
    r1 = client.post("/api/people/%d/accounts" % pid, json={"platform": "tg", "external_id": 1}).json()
    r2 = client.post("/api/people/%d/accounts" % pid, json={"platform": "tg", "external_id": 2}).json()
    acc = {a["external_id"]: a for a in r2["accounts"]}
    assert acc[1]["is_primary"] and not acc[2]["is_primary"] and r1["tg_id"] == 1
    r3 = client.post("/api/people/%d/accounts/%d/primary" % (pid, acc[2]["id"])).json()
    flags = {a["external_id"]: a["is_primary"] for a in r3["accounts"]}
    assert flags == {1: False, 2: True} and r3["tg_id"] == 2                              # ровно один основной на платформу
    r4 = client.post("/api/people/%d/accounts" % pid, json={"platform": "tg", "external_id": 3, "is_primary": True}).json()
    assert {a["external_id"]: a["is_primary"] for a in r4["accounts"]} == {1: False, 2: False, 3: True}
    assert client.post("/api/people/%d/accounts/999999999/primary" % pid).status_code == 404


def test_removing_primary_promotes_next_and_unknown_or_foreign_is_404(client):
    pid = mk_person(tg_id=1); other = mk_person(tg_id=77)
    client.post("/api/people/%d/accounts" % pid, json={"platform": "tg", "external_id": 2})
    first = [a for a in client.get("/api/people/%d" % pid).json()["accounts"] if a["external_id"] == 1][0]
    r = client.delete("/api/people/%d/accounts/%d" % (pid, first["id"])).json()
    assert [(a["external_id"], a["is_primary"]) for a in r["accounts"]] == [(2, True)]        # второй стал основным
    foreign_acc = client.get("/api/people/%d" % other).json()["accounts"][0]["id"]
    assert client.delete("/api/people/%d/accounts/%d" % (pid, foreign_acc)).status_code == 404   # аккаунт чужого человека
    assert client.delete("/api/people/%d/accounts/999999999" % pid).status_code == 404
    last = r["accounts"][0]["id"]
    assert client.delete("/api/people/%d/accounts/%d" % (pid, last)).json()["accounts"] == []   # можно удалить и последний


def test_deleting_person_cascades_accounts(client):
    pid = mk_person(tg_id=1, vk_id=2)
    with get_engine().begin() as c:
        c.execute(delete(people).where(people.c.id == pid))
        assert c.execute(text("SELECT count(*) FROM person_accounts WHERE person_id=:p"), {"p": pid}).scalar_one() == 0


def test_search_finds_person_by_account_username(client):
    pid = mk_person("Имя Без Совпадений", tg_id=5, tg_username="unique_handle_xyz")
    assert [p["id"] for p in client.get("/api/people?q=handle_xyz").json()["people"]] == [pid]


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


# ── PR-6b: «Я», происхождение фактов, упоминания ─────────────────────────────

def mk_self(user=MARK, **kw) -> int:
    return mk_person("Кай", user=user, is_self=True, status="confirmed", **kw)


def test_self_card_is_pinned_first_even_when_filtered_out(client):
    me = mk_self()
    a = mk_person("Аа Аа"); mk_fact(a)
    mk_fact(me, status="pending")
    ids = [p["id"] for p in client.get("/api/people?status=pending").json()["people"]]
    assert ids[0] == me and a in ids                       # self подмешан при любом фильтре и стоит первым
    first = client.get("/api/people").json()["people"][0]
    assert first["id"] == me and first["is_self"] is True
    assert [p["id"] for p in client.get("/api/people?q=аа").json()["people"]] == [a]   # при поиске — как обычно


def test_self_card_cannot_be_rejected_merged_away_or_listed_as_duplicate(client):
    me = mk_self(); other = mk_person("Кай")              # «одноимённый» человек
    assert client.post("/api/people/%d/reject-all" % me).status_code == 409
    assert client.patch("/api/people/%d" % me, json={"status": "rejected"}).status_code == 409
    assert client.post("/api/people/%d/merge" % me, json={"into_id": other}).status_code == 409
    assert client.get("/api/people/duplicates").json()["total"] == 0
    r = client.post("/api/people/%d/review" % me, json={"person_status": "rejected"})
    assert r.status_code == 200 and r.json()["status"] == "confirmed"          # ревью «Я» статус не меняет
    assert client.post("/api/people/%d/merge" % other, json={"into_id": me}).status_code == 200   # В «Я» вливать можно


def test_card_exposes_provenance_and_mentions_and_self_sorts_by_last_mention(client):
    me = mk_self()
    old = mk_fact(me, text_="старое", n="aaaa1111", mention_count=3, last_mention_date=date(2019, 5, 1),
                  first_mention_date=date(2018, 1, 1))
    new = mk_fact(me, text_="свежее", n="bbbb2222", mention_count=7, last_mention_date=date(2023, 6, 1),
                  first_mention_date=date(2020, 1, 1), source_platform="vk", source_chat_id=42,
                  source_message_date=date(2023, 6, 1), quote_author="partner")
    card = client.get("/api/people/%d" % me).json()
    items = card["facts"][0]["items"]
    assert [i["id"] for i in items] == [new, old]                                  # свежие упоминания сверху
    f = items[0]
    assert (f["mention_count"], f["last_mention"], f["first_mention"]) == (7, "2023-06-01", "2020-01-01")
    assert (f["platform"], f["chat_id"], f["message_date"], f["quote_author"]) == ("vk", 42, "2023-06-01", "partner")
    plain = mk_person("Обычный"); mk_fact(plain)
    g = client.get("/api/people/%d" % plain).json()["facts"][0]["items"][0]
    assert g["mention_count"] == 1 and g["platform"] is None and g["last_mention"] is None


def test_self_is_scoped_to_owner(client):
    mk_self(user=OTHER)
    assert all(not p["is_self"] for p in client.get("/api/people").json()["people"])


def test_migration_adds_provenance_and_self_columns_reversibly():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    mods = []
    for pat in ("*_memories_provenance.py", "*_people_self_mentions.py"):
        path = next((REPO / "alembic" / "versions").glob(pat))
        spec = importlib.util.spec_from_file_location("_m_" + pat[2:8], path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mods.append(mod)
    prov, selfm = mods
    assert selfm.down_revision == prov.revision
    with get_engine().connect() as conn:
        trans = conn.begin()
        try:
            with Operations.context(MigrationContext.configure(conn)):
                selfm.downgrade(); prov.downgrade()
                cols = {c["name"] for c in inspect(conn).get_columns("memories")}
                assert not cols & {"source_chat_id", "quote_author", "mention_count", "source_platform"}
                assert "is_self" not in {c["name"] for c in inspect(conn).get_columns("people")}
                prov.upgrade(); selfm.upgrade()
                cols = {c["name"] for c in inspect(conn).get_columns("memories")}
                assert {"source_chat_id", "source_message_date", "quote_author", "source_platform",
                        "mention_count", "first_mention_date", "last_mention_date"} <= cols
                with pytest.raises(IntegrityError):
                    with conn.begin_nested():
                        conn.execute(text("INSERT INTO memories (fact_text, user_id, quote_author) VALUES ('x','t','bogus')"))
        finally:
            trans.rollback()


# ── правки UI Людей: возврат, «Стереть», префикс «Имя:» ─────────────────────

def _raw(fid):
    with get_engine().connect() as c:
        return c.execute(text(
            "SELECT fact_text, key_name, review_status, source_quote, related_to, embedding IS NULL AS no_emb, "
            "source_chat_id, quote_author, source_platform, mention_count, is_archived, person_id "
            "FROM memories WHERE id = :i"), {"i": fid}).one()


def test_restore_returns_rejected_fact_to_pending(client):
    pid = mk_person(); f = mk_fact(pid, status="rejected")
    r = client.post("/api/people/%d/facts/%d/restore" % (pid, f))
    assert r.status_code == 200 and r.json()["review_status"] == "pending" and r.json()["counts"]["pending"] == 1
    assert client.post("/api/people/%d/facts/999999999/restore" % pid).status_code == 404
    foreign = mk_person(user=OTHER); ff = mk_fact(foreign, status="rejected", user=OTHER)
    assert client.post("/api/people/%d/facts/%d/restore" % (foreign, ff)).status_code == 404


def test_erase_only_rejected_clears_everything_but_key_and_status(client):
    pid = mk_person()
    f = mk_fact(pid, n="11111111", status="rejected", text_="секретный текст", source_quote="цитата",
                source_chat_id=5, quote_author="kai", source_platform="vk", mention_count=4)
    with get_engine().begin() as c:
        c.execute(text("UPDATE memories SET embedding = CAST(:e AS vector) WHERE id = :i"),
                  {"e": "[" + ",".join(["1"] + ["0"] * 1023) + "]", "i": f})
    pending = mk_fact(pid, n="22222222", status="pending"); confirmed = mk_fact(pid, n="33333333", status="confirmed")
    assert client.post("/api/people/%d/facts/%d/erase" % (pid, pending)).status_code == 409          # только отклонённые
    assert client.post("/api/people/%d/facts/%d/erase" % (pid, confirmed)).status_code == 409
    r = client.post("/api/people/%d/facts/%d/erase" % (pid, f))
    assert r.status_code == 200 and r.json()["counts"] == {"pending": 1, "confirmed": 1, "rejected": 0}   # стёртый не считается
    row = _raw(f)
    assert row.fact_text == "" and row.source_quote is None and row.related_to == "" and row.no_emb
    assert (row.source_chat_id, row.quote_author, row.source_platform, row.mention_count) == (None, None, None, 1)
    assert row.key_name == "person_%d_likes_11111111" % pid and row.review_status == "rejected"      # tombstone
    assert row.is_archived is False and row.person_id == pid                                         # не архив: импорт видит ключ
    shown = [i["id"] for g in client.get("/api/people/%d" % pid).json()["facts"] for i in g["items"]]
    assert f not in shown and set(shown) == {pending, confirmed}                                     # в UI не показывается
    assert client.post("/api/people/%d/facts/%d/erase" % (pid, f)).status_code == 404                # повтор
    assert client.post("/api/people/%d/facts/%d/restore" % (pid, f)).status_code == 404              # стёртое не вернуть
    assert client.post("/api/people/%d/facts/%d/approve" % (pid, f)).status_code == 404
    foreign = mk_person(user=OTHER); ff = mk_fact(foreign, status="rejected", user=OTHER)
    assert client.post("/api/people/%d/facts/%d/erase" % (foreign, ff)).status_code == 404
    assert _raw(ff).fact_text != ""                                                                   # чужое не тронуто


def test_card_strips_name_prefix_only_for_known_words_and_not_for_self(client):
    pid = mk_person("Мария Иванова", aliases=["Машка"])
    a = mk_fact(pid, n="aaaa0001", text_="Мария: любит кофе")
    b = mk_fact(pid, n="aaaa0002", text_="Машка: не ест рыбу")
    c_ = mk_fact(pid, n="aaaa0003", text_="Важно: аллергия на орехи")
    d = mk_fact(pid, n="aaaa0004", text_="Мария Иванова: ничего не режем без слова в двоеточии")
    me = mk_person("Кай", is_self=True, status="confirmed"); e = mk_fact(me, n="bbbb0001", text_="Кай: пример")
    texts = {i["id"]: i["text"] for g in client.get("/api/people/%d" % pid).json()["facts"] for i in g["items"]}
    assert texts[a] == "любит кофе" and texts[b] == "не ест рыбу"
    assert texts[c_] == "Важно: аллергия на орехи" and texts[d].startswith("Мария Иванова:")
    self_texts = [i["text"] for g in client.get("/api/people/%d" % me).json()["facts"] for i in g["items"]]
    assert self_texts == ["Кай: пример"]                                                             # «Я» не трогаем
    with get_engine().connect() as c:                                                                # в БД префикс остаётся (нужен боту)
        assert c.execute(text("SELECT fact_text FROM memories WHERE id=:i"), {"i": a}).scalar_one() == "Мария: любит кофе"
