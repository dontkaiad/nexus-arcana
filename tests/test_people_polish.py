"""Доводка «Людей» после разбора: единый вход (Память без фактов людей), провенанс, факты без префикса «Имя: »
(+ миграция и очередь пересчёта эмбеддингов), аудит «Других имён», запись «запомни, что Оля любит X» (PR-3b).
Реальная тестовая БД; LLM/сеть не используются. Privacy: generic имена."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import delete, insert, text

from core import memory as mem
from core.db import get_engine
from core.people import guess_fact_type, owner_fact
from core.repos import pg_people_repo as prepo
from core.repos.memories_table import memories
from core.repos.people_tables import people
from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id
from tests._people_helpers import insert_person
from tests.test_people_api import mk_fact

REPO = Path(__file__).resolve().parent.parent
MARK = "test-people-polish"
TG = 700000077
VEC = "[" + ",".join(["0.1"] * 1024) + "]"


@pytest.fixture(autouse=True)
def _clean():
    yield
    with get_engine().begin() as c:
        c.execute(delete(memories).where(memories.c.user_id == MARK))
        c.execute(delete(people).where(people.c.user_id == MARK))


def mk(name, aliases=(), **kw):
    pid = insert_person(MARK, name, aliases=list(aliases), **kw)
    with get_engine().begin() as c:
        prepo.sync_norm(c, pid)
    return pid


def _plain_fact(text_="обычная запись", cat="🏠 Быт", **kw):
    with get_engine().begin() as c:
        return c.execute(insert(memories).values(
            fact_text=text_, key_name="k_" + text_[:5], category=cat, related_to="", user_id=MARK,
            review_status="confirmed", **kw).returning(memories.c.id)).scalar_one()


@pytest.fixture
def api():
    app.dependency_overrides[current_user_id] = lambda: TG
    with patch("miniapp.backend.routes.memory.get_user_id", AsyncMock(return_value=MARK)):
        try:
            yield TestClient(app)
        finally:
            app.dependency_overrides.clear()


# ── 1–2. Память: единый вход в «Люди», провенанс ─────────────────────────────

def test_memory_list_hides_people_facts_but_search_finds_them(api):
    pid = mk("Зюзя Тестова")
    plain = _plain_fact("зюзябрикс обычный")
    fid = mk_fact(pid, status="confirmed", text_="зюзябрикс любит чай", user=MARK)
    items = api.get("/api/memory").json()
    got = {i["id"] for i in items["items"]}
    assert str(plain) in got and str(fid) not in got
    assert "👥 Люди" not in items["categories"]
    found = api.get("/api/memory", params={"q": "зюзябрикс"}).json()["items"]
    assert str(fid) in {i["id"] for i in found}


def test_memory_item_carries_person_and_provenance(api):
    pid = mk("Зюзя Тестова", aliases=["Зюзька"])
    fid = mk_fact(pid, status="confirmed", text_="зюзябрикс любит чай", user=MARK, source_quote="обожаю чай",
                  source_platform="vk", source_message_date=__import__("datetime").date(2021, 3, 12))
    with get_engine().begin() as c:
        c.execute(text("UPDATE memories SET related_to = 'Зюзя Тестова | Зюзька' WHERE id = :i"), {"i": fid})
    it = next(i for i in api.get("/api/memory", params={"q": "зюзябрикс"}).json()["items"] if i["id"] == str(fid))
    assert it["person_id"] == pid and it["person_name"] == "Зюзя Тестова"
    assert (it["source_quote"], it["source_platform"], it["source_message_date"]) == ("обожаю чай", "vk", "2021-03-12")
    plain_id = _plain_fact("обычная запись")
    plain = next(i for i in api.get("/api/memory").json()["items"] if i["id"] == str(plain_id))
    assert plain["person_id"] is None and plain["source_quote"] is None


# ── 3. Текст без префикса «Имя: » ────────────────────────────────────────────

def test_owner_fact_renders_name_only_for_person_facts():
    assert owner_fact("любит чай", "Мария Иванова | Машка", 5) == "Мария: любит чай"
    assert owner_fact("Мария любит чай", "Мария Иванова", 5) == "Мария любит чай"      # имя уже есть
    assert owner_fact("любит чай", "Мария Иванова", None) == "любит чай"              # не факт человека
    assert owner_fact("любит чай", "", 5) == "любит чай"


@pytest.mark.asyncio
async def test_bot_context_shows_name_for_bare_person_facts():
    pid = mk("Зюзя Тестова")
    mk_fact(pid, status="confirmed", text_="любит кардамон")
    with patch.object(mem, "_semantic_search_memory", AsyncMock(side_effect=lambda q, ex, **k: ex)):
        ctx = await mem.get_memories_for_context(MARK, ["Зюзе"], bot_label="☀️ Nexus")
    assert "Зюзя: любит кардамон" in ctx


def _load_mig():
    path = next((REPO / "alembic" / "versions").glob("*_people_fact_prefix_strip.py"))
    spec = importlib.util.spec_from_file_location("_mig_strip", path)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    return mig


def _emb(mid):
    with get_engine().begin() as c:
        c.execute(text("UPDATE memories SET embedding = CAST(:e AS vector) WHERE id = :i"), {"e": VEC, "i": mid})


def test_migration_strips_prefix_queues_only_embedded_and_is_reversible():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    mig = _load_mig()
    pid = mk("Зюзя Тестова", aliases=["Зюзька"])
    me = mk("Кай Тестова", is_self=True)
    a = mk_fact(pid, status="confirmed", text_="Зюзя: любит чай")           # с эмбеддингом → в очередь
    b = mk_fact(pid, status="pending", text_="Зюзька: не любит кофе")       # без эмбеддинга → не в очереди
    c_ = mk_fact(pid, status="confirmed", text_="Важно: аллергия")          # «Важно» — не имя → не трогаем
    d = mk_fact(pid, status="confirmed", text_="любит зюзячай")             # уже без префикса
    s = mk_fact(me, status="confirmed", text_="Кай: пишет код")             # «Я» не трогаем
    for m in (a, c_, d, s):
        _emb(m)
    eng = get_engine()
    with eng.connect() as conn:
        trans = conn.begin()
        try:
            with Operations.context(MigrationContext.configure(conn)):
                conn.execute(text("DROP TABLE IF EXISTS people_reembed_queue"))   # тестовая БД может быть уже на head
                mig.upgrade()
            rows = {r.id: r for r in conn.execute(text(
                "SELECT id, fact_text, embedding IS NULL AS no_emb FROM memories WHERE id = ANY(:i)"), {"i": [a, b, c_, d, s]})}
            queue = {r[0] for r in conn.execute(text("SELECT memory_id FROM people_reembed_queue"))}
            assert rows[a].fact_text == "любит чай" and rows[a].no_emb and a in queue
            assert rows[b].fact_text == "не любит кофе" and b not in queue
            assert rows[c_].fact_text == "Важно: аллергия" and not rows[c_].no_emb and c_ not in queue
            assert rows[d].fact_text == "любит зюзячай" and not rows[d].no_emb and d not in queue
            assert rows[s].fact_text == "Кай: пишет код" and s not in queue
            from tools.people_import import reembed_stripped as rs
            with patch.object(rs, "get_engine", return_value=_BoundEngine(conn)):
                items, mode = rs.plan()
            assert mode == "queue" and {i["id"] for i in items} >= {a} and not ({b, c_, d, s} & {i["id"] for i in items})
            assert next(i for i in items if i["id"] == a)["embed_text"].startswith("любит чай ")
            with Operations.context(MigrationContext.configure(conn)):
                mig.downgrade()
            assert conn.execute(text("SELECT to_regclass('people_reembed_queue') IS NULL")).scalar()
            assert conn.execute(text("SELECT fact_text FROM memories WHERE id = :i"), {"i": a}).scalar() == "любит чай"
        finally:
            trans.rollback()


class _BoundEngine:
    """Подсовывает plan() уже открытое соединение с незакоммиченной миграцией."""
    def __init__(self, conn):
        self.conn = conn

    def connect(self):
        import contextlib
        return contextlib.nullcontext(self.conn)


# ── 5. Аудит «Других имён» ───────────────────────────────────────────────────

def _p(i, name, aliases=(), self_=False, status="confirmed"):
    return {"id": i, "name": name, "aliases": list(aliases), "is_self": self_, "status": status}


def test_audit_finds_self_exact_other_exact_and_token_but_not_own_name():
    from tools.people_import.audit_aliases import audit, group_entries, mutual_pairs
    people_ = [
        _p(1, "Кай Тестова", ["Каюшка"], self_=True),
        _p(2, "Оля Первая", ["Оля", "Кай", "Вадим", "Ильич"]),                 # свой алиас «Оля» ≠ находка
        _p(3, "Вадим Ильич Тестов", ["Вадька"]),
        _p(4, "Маша Вторая", ["Оля Первая"]),
        _p(5, "Другая", ["Маша Вторая"]),
    ]
    found = audit(people_)
    ent = {(e["person"], e["alias_idx"]): e for e in group_entries(found)}
    assert ent[(2, 1)]["self"] is True                                        # «Кай» = имя «Я»
    assert ent[(2, 2)]["exact"] is False and ent[(2, 2)]["others"] == {3}      # «Вадим» — слово чужого имени
    assert ent[(2, 3)]["exact"] is False and ent[(2, 3)]["others"] == {3}      # «Ильич» — слово чужого имени
    assert (2, 0) not in ent                                                  # «Оля» — свой алиас
    assert ent[(4, 0)]["exact"] and ent[(4, 0)]["others"] == {2}               # целиком = чужое имя
    assert all(f["person"] != 1 for f in found)                               # алиасы «Я» не аудируем
    ents = [e for e in group_entries(found) if not e["self"] and e["exact"]]
    assert mutual_pairs(ents) == set()                                         # 4→2, 5→4: взаимных нет


def test_audit_apply_removes_only_self_aliases_and_resyncs_norm():
    from tools.people_import import audit_aliases as au
    me = mk("Кай Тестова", ["Каюшка"], is_self=True)
    x = mk("Оля Первая", ["Кай", "Вадим", "Каюшка"])
    y = mk("Вадим Другой")
    mk_fact(x, status="confirmed", text_="любит чай")
    assert au.main(["--user", MARK]) == 0                                       # dry-run: ничего не меняет
    assert prepo.get_person(MARK, x)["aliases"] == ["Кай", "Вадим", "Каюшка"]
    assert au.main(["--user", MARK, "--apply"]) == 0
    p = prepo.get_person(MARK, x)
    assert p["aliases"] == ["Вадим"]                                           # чужие совпадения остались для ручного разбора
    assert [f["id"] for f in prepo.resolve(MARK, ["Кай"])] == []              # aliases_norm пересчитан, «Я» не резолвится
    assert [r["id"] for r in prepo.resolve(MARK, ["Оле"])] == [x]
    with get_engine().connect() as c:
        rel = c.execute(text("SELECT related_to FROM memories WHERE person_id = :p"), {"p": x}).scalar()
    assert rel == "Оля Первая | Вадим"
    assert prepo.get_person(MARK, y)["aliases"] == []


# ── 6. PR-3b: «запомни, что Оля любит X» ─────────────────────────────────────

async def _store(text_, fact, связь, category="👥 Люди", key="оля_любит"):
    with patch.object(mem, "_parse_fact", AsyncMock(return_value=(fact, category, связь, key))), \
         patch.object(mem, "_resolve_alias", AsyncMock(return_value=None)), \
         patch("core.repos.pg_memory_repo._spawn_index"):
        return await mem.parse_and_store(text_, MARK, "☀️ Nexus")


def _row(mid):
    with get_engine().connect() as c:
        return c.execute(text("SELECT person_id, review_status, source, key_name, fact_text, related_to, category "
                              "FROM memories WHERE id = :i"), {"i": int(mid)}).one()


@pytest.mark.asyncio
async def test_remember_links_fact_to_the_single_resolved_person():
    pid = mk("Оля Зюзина", aliases=["Олька"])
    r = await _store("запомни, что оле нравится зюзячай", "Оля любит зюзячай", "оля")
    row = _row(r["memory_id"])
    assert (row.person_id, row.review_status, row.source) == (pid, "confirmed", "manual")
    assert row.key_name.startswith("person_%d_likes_" % pid)
    assert row.related_to == "Оля Зюзина | Олька" and r["person_id"] == pid
    assert [f["id"] for f in prepo.confirmed_facts(pid)] == [int(r["memory_id"])]   # видно в карточке и контексте ботов
    again = await _store("запомни, что оле нравится зюзячай", "Оля любит зюзячай", "оля")
    assert again["memory_id"] == r["memory_id"] and again["was_updated"]            # идемпотентно


@pytest.mark.asyncio
async def test_remember_resurrects_previously_rejected_fact():
    pid = mk("Оля Зюзина")
    r = await _store("t", "Оля любит зюзячай", "оля")
    prepo.set_fact_status(MARK, pid, int(r["memory_id"]), "rejected")
    await _store("t", "Оля любит зюзячай", "оля")
    assert _row(r["memory_id"]).review_status == "confirmed"


@pytest.mark.asyncio
async def test_remember_without_single_match_stays_a_plain_fact():
    none = await _store("t", "Оля любит чай", "оля")                              # ноль людей
    assert _row(none["memory_id"]).person_id is None and none["person_id"] is None
    mk("Оля Первая")
    mk("Оля Вторая")                                                              # тёзки → неоднозначно
    two = await _store("t", "Оля любит кофе", "оля", key="оля_кофе")
    assert _row(two["memory_id"]).person_id is None


@pytest.mark.asyncio
async def test_remember_ignores_rejected_self_and_other_categories():
    rej = mk("Оля Отклонённая", status="rejected")
    me = mk("Оля Я", is_self=True)
    r = await _store("t", "Оля любит чай", "оля")
    assert _row(r["memory_id"]).person_id is None                                 # rejected и «Я» не резолвятся
    with get_engine().begin() as c:
        c.execute(delete(people).where(people.c.id.in_([rej, me])))
    pid = mk("Оля Зюзина")
    cat = await _store("t", "Оля — кличка кота", "оля", category="🐾 Коты", key="оля_кот")
    assert _row(cat["memory_id"]).person_id is None                               # только категория «Люди»
    assert prepo.confirmed_facts(pid) == []


def test_guess_fact_type():
    assert guess_fact_type("аллергия на орехи") == "important"
    assert guess_fact_type("не любит звонки") == "dislikes"
    assert guess_fact_type("обожает чай") == "likes"
    assert guess_fact_type("работает в банке") == "other"


# ── Спорные «Другие имена» в Mini App ────────────────────────────────────────

def test_alias_disputes_pure():
    from core.people import alias_disputes
    rows = [_p(1, "Кай Тестова", ["Каюшка"], self_=True),
            _p(2, "Оля Первая", ["Оля", "Кай", "Вадима", "Каюшка"]),             # «Оля» — свой; «Вадима» ≈ «Вадим»
            _p(3, "Вадим Второй", []),
            _p(4, "Вадим", [], status="rejected"),                                # rejected не участвует
            _p(5, "Маша", ["Оля Первая"])]
    d = alias_disputes(rows)
    assert 1 not in d                                                              # алиасы «Я» не проверяем
    got = {a["alias"]: [x["id"] for x in a["also"]] for a in d[2]}
    assert got == {"Кай": [1], "Каюшка": [1]}                                      # «Вадима»: «Вадим Второй» — не целиком
    assert [a["also"][0]["is_self"] for a in d[2]] == [True, True]
    assert {a["alias"]: [x["id"] for x in a["also"]] for a in d[5]} == {"Оля Первая": [2]}
    assert 3 not in d and 4 not in d


def test_alias_disputes_stem_match_and_own_name():
    from core.people import alias_disputes
    rows = [_p(1, "Вадим", []), _p(2, "Оля", ["Вадима", "Оля"])]
    assert [a["alias"] for a in alias_disputes(rows)[2]] == ["Вадима"]


def test_duplicates_shared_names_only_when_mutual():
    from core.people import find_duplicates
    rows = [_p(1, "Первый", ["Второй"]), _p(2, "Второй", ["Первый"]),           # взаимно → «общие имена»
            _p(3, "Третий", ["Четвёртый"]), _p(4, "Четвёртый", [])]               # в одну сторону → не shared_names
    pairs = {(d["a"]["id"], d["b"]["id"]): d["reasons"] for d in find_duplicates(rows)}
    assert "shared_names" in pairs[(1, 2)]
    assert "shared_names" not in pairs.get((3, 4), [])


@pytest.fixture
def papi():
    from miniapp.backend.routes import people as route
    app.dependency_overrides[current_user_id] = lambda: TG
    with patch.object(route, "get_user_id", AsyncMock(return_value=MARK)):
        try:
            yield TestClient(app)
        finally:
            app.dependency_overrides.clear()


def test_api_summary_card_remove_reset(papi):
    me = mk("Кай Тестова", is_self=True)
    x = mk("Оля Первая", ["Кай Тестова", "Свой", "Вадим"])
    y = mk("Вадим Второй", ["Вадим"])
    mk_fact(x, status="confirmed", text_="любит зюзячай")
    s = papi.get("/api/people/alias-disputes").json()
    assert s["total"] == 3 and s["people"][0]["id"] == x and (s["people"][0]["disputed"], s["people"][0]["aliases"]) == (2, 3)
    card = papi.get("/api/people/%d" % x).json()
    info = {a["alias"]: [o["id"] for o in a["also"]] for a in card["aliases_info"]}
    assert info == {"Кай Тестова": [me], "Свой": [], "Вадим": [y]}
    r = papi.post("/api/people/%d/aliases/remove" % x, json={"alias": "Кай Тестова"})
    assert r.status_code == 200 and r.json()["aliases"] == ["Свой", "Вадим"]
    assert [p["id"] for p in prepo.resolve(MARK, ["Свой"])] == [x]
    with get_engine().connect() as c:
        assert c.execute(text("SELECT related_to FROM memories WHERE person_id = :p"), {"p": x}).scalar() == "Оля Первая | Свой | Вадим"
    assert papi.get("/api/people/alias-disputes").json()["total"] == 2      # остался взаимный «Вадим» у обоих
    assert papi.post("/api/people/%d/aliases/remove" % x, json={"alias": "нет такого"}).status_code == 404
    assert papi.post("/api/people/%d/aliases/reset" % x).json()["aliases"] == []
    assert prepo.resolve(MARK, ["Свой"]) == []                                     # aliases_norm пересчитан
    assert papi.post("/api/people/%d/aliases/reset" % me).status_code == 409        # «Я» не сбрасываем
    assert papi.post("/api/people/999999999/aliases/reset").status_code == 404
