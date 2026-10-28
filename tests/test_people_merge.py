"""tests/test_people_merge.py — кандидаты в дубли и merge (ADR-0027, PR-5).
Реальная тестовая БД. Privacy: generic фикстуры."""
from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, insert, select, text

from core.db import get_engine
from tests._people_helpers import insert_person
from core.people import find_duplicates
from core.repos import pg_people_repo as repo
from core.repos.memories_table import memories
from core.repos.people_tables import people
from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id
from tools.people_import import import_memory as im

MARK = "test-people-merge"
OTHER = "test-people-merge-other"


def P(id_, name, **kw):
    base = {"id": id_, "name": name, "aliases": [], "birthday": None, "birthday_year_known": True,
            "tg_id": None, "vk_id": None, "status": "pending"}
    base.update(kw)
    return base


# ── find_duplicates (чистая функция) ─────────────────────────────────────────

def test_dup_full_name_is_order_insensitive_and_needs_two_words():
    d = find_duplicates([P(1, "Мария Иванова"), P(2, "иванова  мария"), P(3, "Маша"), P(4, "Маша")])
    assert [(x["a"]["id"], x["b"]["id"], x["reasons"]) for x in d] == [(1, 2, ["full_name"])]


def test_dup_first_name_plus_birthday():
    a = P(1, "Мария Иванова", birthday=date(1990, 3, 5)); b = P(2, "Мария", birthday=date(1990, 3, 5))
    c = P(3, "Мария", birthday=date(1990, 3, 6))
    d = find_duplicates([a, b, c])
    assert [(x["a"]["id"], x["b"]["id"]) for x in d] == [(1, 2)]
    assert d[0]["reasons"] == ["name_birthday"]


def test_dup_birthday_year_ignored_when_unknown():
    a = P(1, "Мария А", birthday=date(1904, 3, 5), birthday_year_known=False)
    b = P(2, "Мария Б", birthday=date(1990, 3, 5))
    assert len(find_duplicates([a, b])) == 1
    b2 = P(3, "Мария В", birthday=date(1991, 3, 5)); b3 = P(4, "Мария Г", birthday=date(1990, 3, 5))
    assert find_duplicates([b2, b3]) == []        # год известен у обоих и разный


def test_dup_alias_equals_other_name():
    d = find_duplicates([P(1, "Мария Иванова", aliases=["Машка"]), P(2, "Машка")])
    assert len(d) == 1 and d[0]["reasons"] == ["alias_is_name"]


def test_dup_popular_alias_is_not_a_signal():
    # «Маша» — алиас у троих и имя ещё одного: общее имя, а не дубль
    rows = [P(1, "Мария Иванова", aliases=["Маша"]), P(2, "Мария Петрова", aliases=["Маша"]),
            P(3, "Мария Сидорова", aliases=["Маша"]), P(4, "Маша")]
    assert find_duplicates(rows) == []


def test_dup_combined_signals_raise_score_and_sort_first():
    strong = [P(1, "Мария Иванова", birthday=date(1990, 3, 5), aliases=["Машка"]),
              P(2, "Иванова Мария", birthday=date(1990, 3, 5))]
    weak = [P(3, "Пётр", aliases=["Петька"]), P(4, "Петька")]
    d = find_duplicates(strong + weak)
    assert d[0]["a"]["id"] == 1 and d[0]["score"] > d[1]["score"]
    assert set(d[0]["reasons"]) == {"full_name", "name_birthday"}


def test_dup_candidates_ignore_account_differences_but_skip_rejected():
    # аккаунты при merge объединяются, поэтому «разные tg_id/vk_id» больше не повод прятать пару
    assert len(find_duplicates([P(1, "Мария Иванова", tg_id=1), P(2, "Мария Иванова", tg_id=2)])) == 1
    assert len(find_duplicates([P(1, "Мария Иванова", vk_id=1), P(2, "Мария Иванова", vk_id=2)])) == 1
    assert len(find_duplicates([P(1, "Мария Иванова", tg_id=1), P(2, "Мария Иванова", vk_id=2)])) == 1
    assert find_duplicates([P(1, "Мария Иванова"), P(2, "Мария Иванова", status="rejected")]) == []


# ── БД ───────────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    app.dependency_overrides[current_user_id] = lambda: 700000001
    with patch("miniapp.backend.routes.people.get_user_id", AsyncMock(return_value=MARK)):
        try:
            yield TestClient(app)
        finally:
            app.dependency_overrides.clear()
            _wipe()


def _wipe():
    with get_engine().begin() as c:
        for u in (MARK, OTHER):
            c.execute(delete(memories).where(memories.c.user_id == u))
            c.execute(delete(people).where(people.c.user_id == u))


def mk_person(name="Имя1 Фамилия1", user=MARK, **kw) -> int:
    return insert_person(user, name, **kw)


def mk_fact(pid, type_="likes", h8="aaaaaaaa", status="pending", text_="факт", user=MARK, key=None) -> int:
    with get_engine().begin() as c:
        return c.execute(insert(memories).values(
            fact_text=text_, key_name=key or "person_%d_%s_%s" % (pid, type_, h8), category="👥 Люди",
            person_id=pid, review_status=status, user_id=user, source="auto").returning(memories.c.id)).scalar_one()


def person_row(pid):
    with get_engine().connect() as c:
        r = c.execute(select(people).where(people.c.id == pid)).first()
    return dict(r._mapping) if r else None


def accts_of(pid):
    """[(platform, external_id, username, is_primary)] — стабильно отсортировано."""
    with get_engine().connect() as c:
        return [tuple(r) for r in c.execute(text(
            "SELECT platform, external_id, username, is_primary FROM person_accounts WHERE person_id=:p "
            "ORDER BY platform, external_id"), {"p": pid})]


def facts_of(pid):
    with get_engine().connect() as c:
        return {r.id: (r.key_name, r.review_status, r.fact_text) for r in c.execute(
            select(memories).where(memories.c.person_id == pid))}


def test_duplicates_endpoint_route_order_scope_and_no_auto_merge(client):
    a = mk_person("Мария Иванова"); b = mk_person("Иванова Мария")
    mk_person("Мария Иванова", user=OTHER)
    r = client.get("/api/people/duplicates")
    assert r.status_code == 200                     # не уходит в /people/{id} → 422
    pair = r.json()["pairs"][0]
    assert {pair["a"]["id"], pair["b"]["id"]} == {a, b} and pair["reasons"] == ["full_name"] and pair["score"] > 0
    assert r.json()["total"] == 1
    assert person_row(a) and person_row(b)           # ничего не склеено


def test_merge_moves_rekeys_and_drops_conflicts_keeping_target(client):
    src = mk_person("Мария И", aliases=["Машка"], notes="заметка 1", birthday=date(1990, 3, 5),
                    birthday_year_known=True, tg_id=111, tg_username="mash", status="confirmed")
    dst = mk_person("Мария Иванова", aliases=["М"], notes="заметка 2", vk_id=222)
    f_only = mk_fact(src, "likes", "11111111", "confirmed", "только у источника")
    f_conf_src = mk_fact(src, "important", "22222222", "confirmed", "общий (источник)")
    f_conf_dst = mk_fact(dst, "important", "22222222", "pending", "общий (цель)")
    f_dst = mk_fact(dst, "other", "33333333", "pending", "только у цели")
    manual = mk_fact(src, key="manual_key_1", text_="ручной")

    r = client.post("/api/people/%d/merge" % src, json={"into_id": dst})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["moved"] == 2 and body["dropped_duplicates"] == 1       # 1111… и ручной переехали; общий — дубль
    assert person_row(src) is None
    t = person_row(dst)
    assert t["name"] == "Мария Иванова" and t["status"] == "pending"        # имя/статус цели не тронуты
    assert t["aliases"] == ["М", "Машка", "Мария И"]
    assert (t["birthday"], t["birthday_year_known"]) == (date(1990, 3, 5), True)
    assert accts_of(dst) == [("tg", 111, "mash", True), ("vk", 222, None, True)]   # аккаунты объединились
    assert body["accounts_moved"] == 1
    assert t["notes"] == "заметка 2\nзаметка 1"
    fs = facts_of(dst)
    assert f_conf_src not in fs and fs[f_conf_dst][1:] == ("pending", "общий (цель)")      # остаётся целевой
    assert fs[f_only][0] == "person_%d_likes_11111111" % dst                                # ключ по id цели
    assert fs[manual][0] == "manual_key_1" and f_dst in fs
    assert body["person"]["id"] == dst


def test_merge_fills_only_empty_target_fields(client):
    src = mk_person("А Б", birthday=date(1990, 1, 1), tg_id=5, notes="x")
    dst = mk_person("А Б", birthday=date(1985, 2, 2), notes="x")
    assert client.post("/api/people/%d/merge" % src, json={"into_id": dst}).status_code == 200
    t = person_row(dst)
    assert t["birthday"] == date(1985, 2, 2) and t["notes"] == "x" and accts_of(dst) == [("tg", 5, None, True)]


def test_merge_errors_leave_state_untouched(client):
    a = mk_person(tg_id=1); b = mk_person(tg_id=2); fa = mk_fact(a)
    assert client.post("/api/people/%d/merge" % a, json={"into_id": a}).status_code == 422
    assert client.post("/api/people/%d/merge" % a, json={"into_id": 999999999}).status_code == 404
    foreign = mk_person(user=OTHER)
    assert client.post("/api/people/%d/merge" % a, json={"into_id": foreign}).status_code == 404
    assert client.post("/api/people/%d/merge" % foreign, json={"into_id": a}).status_code == 404
    assert person_row(a) and person_row(b) and fa in facts_of(a)


def test_merge_is_atomic_rolls_back_on_failure():
    try:
        src = mk_person("Ис", tg_id=7); dst = mk_person("Цель")
        f1 = mk_fact(src, "likes", "11111111"); f2 = mk_fact(src, "other", "44444444")
        before = facts_of(src)
        with patch.object(repo, "_delete_person", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError):
                repo.merge_people(MARK, src, dst)
        assert person_row(src) is not None and accts_of(src) == [("tg", 7, None, True)]
        assert facts_of(src) == before and facts_of(dst) == {} and accts_of(dst) == []   # ничего не уехало, ключи не переписаны
        assert f1 in facts_of(src) and f2 in facts_of(src)
    finally:
        _wipe()


def test_merge_two_tg_and_vk_accounts_keeps_target_primary_and_removes_old_409(client):
    a = mk_person("Имя1 Фамилия1", tg_id=1, tg_username="first")
    b = mk_person("Имя1 Ф", tg_id=2, tg_username="second", vk_id=3)
    c3 = mk_person("Имя1", accounts=[{"platform": "tg", "external_id": 4, "username": None, "is_primary": True}])
    assert client.post("/api/people/%d/merge" % b, json={"into_id": a}).status_code == 200   # раньше — 409 «разные аккаунты»
    assert accts_of(a) == [("tg", 1, "first", True), ("tg", 2, "second", False), ("vk", 3, None, True)]
    assert client.post("/api/people/%d/merge" % c3, json={"into_id": a}).status_code == 200
    assert accts_of(a) == [("tg", 1, "first", True), ("tg", 2, "second", False), ("tg", 4, None, False),
                           ("vk", 3, None, True)]                       # основной аккаунт цели сохранён
    got = client.get("/api/people/%d" % a).json()
    assert [(x["platform"], x["external_id"], x["is_primary"]) for x in got["accounts"]][0] == ("tg", 1, True)
    assert got["tg_id"] == 1 and got["tg_username"] == "first" and got["vk_id"] == 3    # производные от основных


def test_merge_source_primary_becomes_primary_only_when_target_has_none(client):
    a = mk_person("А", tg_id=1)                       # у цели есть основной TG, VK нет
    b = mk_person("Б", tg_id=2, vk_id=9)
    client.post("/api/people/%d/merge" % b, json={"into_id": a})
    assert accts_of(a) == [("tg", 1, None, True), ("tg", 2, None, False), ("vk", 9, None, True)]


def test_reimport_after_merge_does_not_resurrect_duplicate(monkeypatch):
    monkeypatch.setenv("PEOPLE_IMPORT_USER_ID", MARK)

    def row(name, fact, **kw):
        base = {"name": name, "aliases": [], "fact": fact, "type": "likes", "quote": "q", "source": "tg",
                "origin": "chat", "tg_id": None, "tg_username": None, "vk_id": None, "vk_screen": None}
        base.update(kw)
        return base
    rows = [row("Имя1 Фамилия1", "любит чай", tg_id=1),
            row("Имя1 Ф", "не ест рыбу", tg_id=2),                                   # второй TG-аккаунт
            row("Имя1 Ф", "любит чай", source="vk", vk_id=3),
            row("Имя1 Ф", "любит джаз", source="vk", vk_id=3)]
    monkeypatch.setattr(im, "load_staging", lambda: rows)

    def n(table):
        with get_engine().connect() as c:
            return c.execute(text("SELECT count(*) FROM %s WHERE user_id=:u" % table), {"u": MARK}).scalar_one()

    def pid_by(platform, ext):
        with get_engine().connect() as c:
            return c.execute(text("SELECT person_id FROM person_accounts WHERE user_id=:u AND platform=:p AND external_id=:e"),
                             {"u": MARK, "p": platform, "e": ext}).scalar_one()
    try:
        asyncio.run(im.run(False, True, 20, 0))
        assert n("people") == 3 and n("person_accounts") == 3
        a, b, c3 = pid_by("tg", 1), pid_by("tg", 2), pid_by("vk", 3)
        repo.merge_people(MARK, b, a)
        repo.merge_people(MARK, c3, a)
        facts_before, accounts_before = n("memories"), accts_of(a)
        assert n("people") == 1 and accounts_before == [("tg", 1, None, True), ("tg", 2, None, False), ("vk", 3, None, True)]
        asyncio.run(im.run(False, True, 20, 0))                                      # повторный импорт после merge
        assert n("people") == 1 and n("memories") == facts_before and accts_of(a) == accounts_before
        assert {pid_by("tg", 1), pid_by("tg", 2), pid_by("vk", 3)} == {a}            # все три аккаунта ведут к цели
    finally:
        _wipe()
