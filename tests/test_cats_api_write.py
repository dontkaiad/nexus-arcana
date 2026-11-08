"""tests/test_cats_api_write.py — Cats phase 2, step 3: POST/PATCH /api/cats, фото, факты, привязка.

Реальная тестовая БД и TestClient; сеть подменена только на границах (Cloudinary, LLM, эмбеддинг-индексация).
Имена вымышленные; строки помечены MARK.
"""
from __future__ import annotations

import io
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, insert, text

from core import memory as mem
from core.cats import cat_fact_key, cat_key_type
from core.db import get_engine
from core.repos import pg_cats_repo as crepo
from core.repos.cats_tables import cats
from core.repos.memories_table import memories
from core.repos.people_tables import people
from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id

MARK = "test-cats-p2-write"
OTHER = "test-cats-p2-write-other"
TG = 700000099
CATEGORY = "🐾 Коты"
URL = "https://example.invalid/cats/x.jpg"


@pytest.fixture(autouse=True)
def _cleanup():
    with patch("core.repos.pg_memory_repo._spawn_index"):
        yield
    with get_engine().begin() as conn:
        conn.execute(delete(memories).where(memories.c.user_id.in_([MARK, OTHER])))
        conn.execute(delete(cats).where(cats.c.user_id.in_([MARK, OTHER])))
        conn.execute(delete(people).where(people.c.user_id.in_([MARK, OTHER])))


@pytest.fixture
def api():
    app.dependency_overrides[current_user_id] = lambda: TG
    with patch("miniapp.backend.routes.cats.get_user_id", AsyncMock(return_value=MARK)):
        try:
            yield TestClient(app)
        finally:
            app.dependency_overrides.clear()


def mk_fact(cat_id, text_, user=MARK, key="k", category=CATEGORY, **kw):
    vals = dict(fact_text=text_, key_name=key, category=category, related_to="", user_id=user,
                review_status="confirmed", cat_id=cat_id)
    vals.update(kw)
    with get_engine().begin() as c:
        return c.execute(insert(memories).values(**vals).returning(memories.c.id)).scalar_one()


def row(mid):
    with get_engine().connect() as c:
        return c.execute(text("SELECT cat_id, key_name, related_to, fact_text, category, source, review_status, "
                              "is_archived FROM memories WHERE id = :i"), {"i": int(mid)}).one()


# ── POST /api/cats ───────────────────────────────────────────────────────────

def test_create_cat(api):
    r = api.post("/api/cats", json={"name": "  Тучка  ", "aliases": ["Туча", " туча ", "Тучка", "", "Тучик"]})
    assert r.status_code == 200
    d = r.json()
    assert d["name"] == "Тучка" and d["aliases"] == ["Туча", "Тучик"]          # без дублей, без клички = имени
    assert d["status"] == "confirmed" and d["photo_url"] is None and d["facts_count"] == 0
    assert [c["id"] for c in crepo.resolve_cat(MARK, ["Тучке"])] == [d["id"]]   # сразу находится ботом
    assert [c["id"] for c in crepo.resolve_cat(MARK, ["Тучик"])] == [d["id"]]
    assert api.get("/api/cats").json()["total"] == 1


@pytest.mark.parametrize("body", [
    {"name": ""}, {"name": "   "}, {"name": "x" * 61}, {"aliases": ["а"]},
    {"name": "Тучка", "aliases": ["а"] * 11}, {"name": "Тучка", "aliases": ["б" * 41]},
])
def test_create_validation(api, body):
    assert api.post("/api/cats", json=body).status_code == 422
    assert crepo.list_cats(MARK) == []


def test_create_duplicate_name_is_409_case_insensitive(api):
    assert api.post("/api/cats", json={"name": "Тучка"}).status_code == 200
    assert api.post("/api/cats", json={"name": "тучка"}).status_code == 409
    assert len(crepo.list_cats(MARK)) == 1


def test_same_name_for_another_owner_is_allowed(api):
    crepo.create(OTHER, "Тучка")
    assert api.post("/api/cats", json={"name": "Тучка"}).status_code == 200


def test_rejected_name_can_be_reused(api):
    cid = crepo.create(MARK, "Тучка")
    crepo.update_cat(MARK, cid, {"status": "rejected"})
    assert api.post("/api/cats", json={"name": "Тучка"}).status_code == 200


# ── PATCH /api/cats/{id} ─────────────────────────────────────────────────────

def test_patch_rename_resyncs_resolve_and_fact_related_to(api):
    cid = crepo.create(MARK, "Тучка", ["Туча"])
    fid = mk_fact(cid, "Тучка любит окно", related_to="Тучка")
    d = api.patch(f"/api/cats/{cid}", json={"name": "Облако"}).json()
    assert d["name"] == "Облако" and d["facts_count"] == 1
    assert [c["id"] for c in crepo.resolve_cat(MARK, ["Облаку"])] == [cid]
    assert crepo.resolve_cat(MARK, ["Тучка"]) == []                       # старое имя больше не находит кота
    assert row(fid).related_to == "Облако"


def test_patch_aliases_replace_and_drop_name_duplicate(api):
    cid = crepo.create(MARK, "Тучка", ["Туча"])
    d = api.patch(f"/api/cats/{cid}", json={"aliases": ["Тучик", "тучка", "Тучик"]}).json()
    assert d["aliases"] == ["Тучик"]
    assert crepo.resolve_cat(MARK, ["Туча"]) == []
    assert [c["id"] for c in crepo.resolve_cat(MARK, ["Тучик"])] == [cid]


def test_patch_rename_to_existing_name_is_409(api):
    crepo.create(MARK, "Тучка")
    b = crepo.create(MARK, "Пончик")
    assert api.patch(f"/api/cats/{b}", json={"name": "ТУЧКА"}).status_code == 409
    assert crepo.get_cat(MARK, b)["name"] == "Пончик"
    assert api.patch(f"/api/cats/{b}", json={"name": "Пончик"}).status_code == 200          # то же имя — не конфликт с собой


def test_patch_status_reject_and_restore(api):
    cid = crepo.create(MARK, "Тучка")
    assert api.patch(f"/api/cats/{cid}", json={"status": "rejected"}).status_code == 200
    assert api.get("/api/cats").json()["total"] == 0 and api.get(f"/api/cats/{cid}").status_code == 404
    assert crepo.resolve_cat(MARK, ["Тучка"]) == []
    assert api.patch(f"/api/cats/{cid}", json={"status": "confirmed"}).status_code == 200
    assert api.get(f"/api/cats/{cid}").status_code == 200


def test_patch_restore_conflicts_with_new_cat_of_same_name(api):
    old = crepo.create(MARK, "Тучка")
    api.patch(f"/api/cats/{old}", json={"status": "rejected"})
    api.post("/api/cats", json={"name": "Тучка"})
    assert api.patch(f"/api/cats/{old}", json={"status": "confirmed"}).status_code == 409


@pytest.mark.parametrize("body,code", [({}, 422), ({"status": "weird"}, 422), ({"name": ""}, 422),
                                       ({"name": "   "}, 422), ({"aliases": ["б" * 41]}, 422)])
def test_patch_validation(api, body, code):
    cid = crepo.create(MARK, "Тучка")
    assert api.patch(f"/api/cats/{cid}", json=body).status_code == code
    assert crepo.get_cat(MARK, cid)["name"] == "Тучка"


def test_patch_ignores_protected_fields_and_foreign_cat_is_404(api):
    cid = crepo.create(MARK, "Тучка")
    foreign = crepo.create(OTHER, "Чужекот")
    api.patch(f"/api/cats/{cid}", json={"name": "Тучка2", "user_id": OTHER, "photo_url": URL, "id": 1})
    c = crepo.get_cat(MARK, cid)
    assert c["user_id"] == MARK and c["photo_url"] is None
    assert api.patch(f"/api/cats/{foreign}", json={"name": "Взлом"}).status_code == 404
    assert crepo.get_cat(OTHER, foreign)["name"] == "Чужекот"


# ── POST /api/cats/{id}/photo ────────────────────────────────────────────────

def _upload(api, cid, data=b"\xff\xd8jpegbytes", ctype="image/jpeg", name="c.jpg"):
    return api.post(f"/api/cats/{cid}/photo", files={"file": (name, io.BytesIO(data), ctype)})


def test_photo_upload_sets_photo_url(api):
    cid = crepo.create(MARK, "Тучка")
    up = AsyncMock(return_value=URL)
    with patch("miniapp.backend.routes.cats.cloudinary_upload", up):
        r = _upload(api, cid)
    assert r.status_code == 200 and r.json() == {"ok": True, "url": URL}
    assert crepo.get_cat(MARK, cid)["photo_url"] == URL
    assert up.await_args.args[0] == b"\xff\xd8jpegbytes" and up.await_args.kwargs["folder"] == "cats"
    assert api.get("/api/cats").json()["cats"][0]["photo_url"] == URL


def test_photo_replaces_old_one(api):
    cid = crepo.create(MARK, "Тучка", photo_url="https://example.invalid/old.jpg")
    with patch("miniapp.backend.routes.cats.cloudinary_upload", AsyncMock(return_value=URL)):
        _upload(api, cid)
    assert crepo.get_cat(MARK, cid)["photo_url"] == URL


def test_photo_rejects_non_image_empty_and_oversize(api):
    cid = crepo.create(MARK, "Тучка")
    up = AsyncMock(return_value=URL)
    with patch("miniapp.backend.routes.cats.cloudinary_upload", up):
        assert _upload(api, cid, ctype="application/pdf", name="x.pdf").status_code == 415
        assert _upload(api, cid, data=b"").status_code == 422
        assert _upload(api, cid, data=b"x" * (5 * 1024 * 1024 + 1)).status_code == 413
        assert _upload(api, cid, data=b"x" * (5 * 1024 * 1024)).status_code == 200      # ровно 5 МБ проходит
    assert up.await_count == 1


def test_photo_without_cloudinary_is_501_and_keeps_old_url(api):
    cid = crepo.create(MARK, "Тучка", photo_url="https://example.invalid/old.jpg")
    with patch("miniapp.backend.routes.cats.cloudinary_upload", AsyncMock(return_value=None)):
        assert _upload(api, cid).status_code == 501
    assert crepo.get_cat(MARK, cid)["photo_url"] == "https://example.invalid/old.jpg"


def test_photo_foreign_or_rejected_cat_is_404_without_upload(api):
    foreign = crepo.create(OTHER, "Чужекот")
    rej = crepo.create(MARK, "Тучка")
    crepo.update_cat(MARK, rej, {"status": "rejected"})
    up = AsyncMock(return_value=URL)
    with patch("miniapp.backend.routes.cats.cloudinary_upload", up):
        assert _upload(api, foreign).status_code == 404 and _upload(api, rej).status_code == 404
    up.assert_not_awaited()


# ── POST /api/cats/{id}/facts ────────────────────────────────────────────────

def test_add_fact_writes_cat_fact_and_shows_in_card(api):
    cid = crepo.create(MARK, "Тучка")
    r = api.post(f"/api/cats/{cid}/facts", json={"text": "  любит   окно ", "type": "likes"})
    assert r.status_code == 200 and r.json()["was_updated"] is False and r.json()["facts_count"] == 1
    x = row(r.json()["fact_id"])
    assert x.cat_id == cid and x.category == CATEGORY and x.related_to == "Тучка"
    assert x.source == "manual" and x.review_status == "confirmed"
    assert x.fact_text == "Тучка: любит окно" and cat_key_type(x.key_name) == "likes"
    card = api.get(f"/api/cats/{cid}").json()
    assert card["facts"][0]["type"] == "likes" and card["facts"][0]["items"][0]["text"] == "любит окно"


def test_add_fact_is_idempotent_and_name_in_text_not_duplicated(api):
    cid = crepo.create(MARK, "Тучка")
    a = api.post(f"/api/cats/{cid}/facts", json={"text": "Тучка не ест курицу", "type": "dislikes"}).json()
    b = api.post(f"/api/cats/{cid}/facts", json={"text": "Тучка  не ест курицу", "type": "dislikes"}).json()
    assert a["fact_id"] == b["fact_id"] and b["was_updated"] is True and b["facts_count"] == 1
    assert row(a["fact_id"]).fact_text == "Тучка не ест курицу"


@pytest.mark.parametrize("body", [{"text": ""}, {"text": "   "}, {"text": "x", "type": "weird"}, {"text": "я" * 501}])
def test_add_fact_validation(api, body):
    cid = crepo.create(MARK, "Тучка")
    assert api.post(f"/api/cats/{cid}/facts", json=body).status_code == 422
    assert crepo.list_facts(MARK, cid) == []


def test_add_fact_unknown_foreign_or_rejected_cat_is_404(api):
    foreign = crepo.create(OTHER, "Чужекот")
    rej = crepo.create(MARK, "Тучка")
    crepo.update_cat(MARK, rej, {"status": "rejected"})
    for cid in (foreign, rej, 987654321):
        assert api.post(f"/api/cats/{cid}/facts", json={"text": "x"}).status_code == 404
    assert crepo.list_facts(OTHER, foreign) == []


# ── POST /api/cats/{id}/facts/{memory_id}/attach ─────────────────────────────

def test_attach_general_fact(api):
    cid = crepo.create(MARK, "Тучка")
    fid = mk_fact(None, "Тучка не ест курицу", key="тучка_еда", related_to="кот")
    assert [i["id"] for g in api.get("/api/cats/general").json()["facts"] for i in g["items"]] == [fid]
    r = api.post(f"/api/cats/{cid}/facts/{fid}/attach")
    assert r.status_code == 200 and r.json() == {"ok": True, "fact_id": fid, "attached": True, "facts_count": 1}
    x = row(fid)
    assert x.cat_id == cid and x.related_to == "Тучка" and cat_key_type(x.key_name) == "dislikes"   # тип угадан по тексту
    assert api.get("/api/cats/general").json()["total"] == 0
    assert api.get(f"/api/cats/{cid}").json()["facts"][0]["items"][0]["id"] == fid


def test_attach_is_idempotent_for_same_cat(api):
    cid = crepo.create(MARK, "Тучка")
    fid = mk_fact(None, "спит на диване", key="k1")
    api.post(f"/api/cats/{cid}/facts/{fid}/attach")
    r = api.post(f"/api/cats/{cid}/facts/{fid}/attach")
    assert r.status_code == 200 and r.json()["attached"] is False and r.json()["facts_count"] == 1


def test_attach_fact_of_another_cat_or_person_is_409(api):
    a = crepo.create(MARK, "Тучка")
    b = crepo.create(MARK, "Пончик")
    mine_other = mk_fact(b, "Пончик спит", key="k2")
    assert api.post(f"/api/cats/{a}/facts/{mine_other}/attach").status_code == 409
    with get_engine().begin() as c:
        pid = c.execute(insert(people).values(user_id=MARK, name="Человек", status="confirmed")
                        .returning(people.c.id)).scalar_one()
    person_fact = mk_fact(None, "про человека", key="k3", person_id=pid)
    assert api.post(f"/api/cats/{a}/facts/{person_fact}/attach").status_code == 409
    assert row(mine_other).cat_id == b and row(person_fact).cat_id is None


def test_attach_non_cat_foreign_archived_and_missing(api):
    cid = crepo.create(MARK, "Тучка")
    other_cat = mk_fact(None, "про быт", key="k4", category="🏠 Быт")
    foreign = mk_fact(None, "чужое", key="k5", user=OTHER)
    archived = mk_fact(None, "старое", key="k6", is_archived=True)
    assert api.post(f"/api/cats/{cid}/facts/{other_cat}/attach").status_code == 422
    for fid in (foreign, archived, 987654321):
        assert api.post(f"/api/cats/{cid}/facts/{fid}/attach").status_code == 404
    assert row(other_cat).cat_id is None and row(foreign).cat_id is None


def test_attach_duplicate_of_existing_cat_fact_is_409(api):
    cid = crepo.create(MARK, "Тучка")
    api.post(f"/api/cats/{cid}/facts", json={"text": "Тучка любит окно", "type": "likes"})
    dup = mk_fact(None, "Тучка любит окно", key="legacy")
    assert api.post(f"/api/cats/{cid}/facts/{dup}/attach").status_code == 409
    assert row(dup).cat_id is None


def test_attach_to_foreign_or_rejected_cat_is_404(api):
    foreign = crepo.create(OTHER, "Чужекот")
    fid = mk_fact(None, "про кота", key="k7")
    assert api.post(f"/api/cats/{foreign}/facts/{fid}/attach").status_code == 404
    assert row(fid).cat_id is None


# ── факты карточки «Я» в «Общем про котов»: category сменили на «Коты», а person_id остался (баг: «уже привязан к другому коту или человеку») ──

def mk_person(user=MARK, is_self=False, name="Человек"):
    with get_engine().begin() as c:
        return c.execute(insert(people).values(user_id=user, name=name, status="confirmed", is_self=is_self)
                         .returning(people.c.id)).scalar_one()


def self_fact(pid, text_, kind="likes", user=MARK, **kw):
    """Как в проде: авто-факт про владельца (ключ person_<id>_<тип>_<hash>), категорию потом сменили на «Коты»."""
    from core.people import fact_hash
    return mk_fact(None, text_, user=user, key="person_%d_%s_%s" % (pid, kind, fact_hash(text_)), person_id=pid, source="auto", **kw)


def person_row(mid):
    with get_engine().connect() as c:
        return c.execute(text("SELECT person_id, embedding IS NULL AS no_emb FROM memories WHERE id = :i"), {"i": int(mid)}).one()


def with_embedding(mid):
    with get_engine().begin() as c:
        c.execute(text("UPDATE memories SET embedding = CAST(:v AS vector) WHERE id = :i"),
                  {"v": "[" + ",".join(["0.01"] * 1024) + "]", "i": int(mid)})


def test_self_card_fact_in_cats_category_is_attachable_and_leaves_the_self_card(api):
    me = mk_person(is_self=True, name="Владелица")
    cid = crepo.create(MARK, "Тучка")
    fid = self_fact(me, "Тучка любит спать на подоконнике", "likes", review_status="pending")
    with_embedding(fid)
    shown = [i["id"] for g in api.get("/api/cats/general").json()["facts"] for i in g["items"]]
    assert shown == [fid]                                                  # /general его показывает …
    r = api.post(f"/api/cats/{cid}/facts/{fid}/attach")                    # … и теперь attach его принимает
    assert r.status_code == 200 and r.json() == {"ok": True, "fact_id": fid, "attached": True, "facts_count": 1}
    x = row(fid)
    assert x.cat_id == cid and x.related_to == "Тучка" and x.review_status == "confirmed"
    assert cat_key_type(x.key_name) == "likes"                              # тип взят из ключа карточки «Я»
    pr = person_row(fid)
    assert pr.person_id is None and pr.no_emb                               # person_id снят, эмбеддинг сброшен (в нём было имя)
    assert api.get("/api/cats/general").json()["total"] == 0
    assert api.get(f"/api/cats/{cid}").json()["facts"][0]["items"][0]["id"] == fid


def test_batch_of_self_facts_all_attach_and_none_comes_back(api):
    from core.repos import pg_people_repo as prepo
    me = mk_person(is_self=True)
    luna = crepo.create(MARK, "Луна")
    ids = [self_fact(me, "Луна факт номер %d" % i, ["important", "likes", "dislikes", "other"][i % 4]) for i in range(8)]
    assert api.get("/api/cats/general").json()["total"] == 8
    assert [api.post(f"/api/cats/{luna}/facts/{i}/attach").status_code for i in ids] == [200] * 8
    assert all(row(i).cat_id == luna and person_row(i).person_id is None for i in ids)
    assert [cat_key_type(row(i).key_name) for i in ids] == ["important", "likes", "dislikes", "other"] * 2      # тип из ключа «Я», а не из текста
    # «разнос» / смена категории карточки «Я» больше эти факты не трогает: их там уже нет
    assert prepo.set_facts_category(MARK, me, ids, "💼 Работа") == 0
    assert all(row(i).category == CATEGORY for i in ids)
    assert api.get("/api/cats/general").json()["total"] == 0


def test_another_persons_fact_is_not_listed_and_still_409(api):
    other_person = mk_person(is_self=False, name="Мария")
    cid = crepo.create(MARK, "Тучка")
    fid = self_fact(other_person, "про Марию и кота")
    assert api.get("/api/cats/general").json()["total"] == 0                # принадлежит карточке человека: в «Общем» не показываем
    assert api.post(f"/api/cats/{cid}/facts/{fid}/attach").status_code == 409
    assert row(fid).cat_id is None and person_row(fid).person_id == other_person


def test_person_of_another_user_is_not_a_self_card(api):
    foreign_self = mk_person(user=OTHER, is_self=True)                      # битая ссылка: факт владельца указывает на «Я» чужого
    cid = crepo.create(MARK, "Тучка")
    fid = self_fact(foreign_self, "странный факт")
    assert api.get("/api/cats/general").json()["total"] == 0
    assert api.post(f"/api/cats/{cid}/facts/{fid}/attach").status_code == 409
    assert person_row(fid).person_id == foreign_self


def test_foreign_users_self_fact_is_404(api):
    foreign_self = mk_person(user=OTHER, is_self=True)
    cid = crepo.create(MARK, "Тучка")
    fid = self_fact(foreign_self, "чужое про «Я»", user=OTHER)
    assert api.post(f"/api/cats/{cid}/facts/{fid}/attach").status_code == 404
    assert person_row(fid).person_id == foreign_self


def test_self_fact_duplicate_of_cat_fact_is_409_and_untouched(api):
    me = mk_person(is_self=True)
    cid = crepo.create(MARK, "Тучка")
    api.post(f"/api/cats/{cid}/facts", json={"text": "Тучка любит окно", "type": "likes"})
    fid = self_fact(me, "Тучка любит окно", "likes")
    assert api.post(f"/api/cats/{cid}/facts/{fid}/attach").status_code == 409
    assert row(fid).cat_id is None and person_row(fid).person_id == me      # ничего не потеряно: факт остался в «Я»


def test_self_fact_in_other_category_stays_422_and_untouched(api):
    me = mk_person(is_self=True)
    cid = crepo.create(MARK, "Тучка")
    fid = self_fact(me, "про работу", category="💼 Работа")
    assert api.post(f"/api/cats/{cid}/facts/{fid}/attach").status_code == 422
    assert person_row(fid).person_id == me and row(fid).cat_id is None


def test_concurrent_attach_of_one_self_fact_to_two_cats_wins_once():
    from concurrent.futures import ThreadPoolExecutor
    me = mk_person(is_self=True)
    a, b = crepo.create(MARK, "Тучка"), crepo.create(MARK, "Пончик")
    fid = self_fact(me, "общий факт про котов")
    cats_ = [{"id": a, "name": "Тучка"}, {"id": b, "name": "Пончик"}]
    with ThreadPoolExecutor(2) as ex:
        res = sorted(ex.map(lambda c: crepo.attach_fact(MARK, c, fid), cats_))
    assert res == ["ok", "taken"]
    x = row(fid)
    assert x.cat_id in (a, b) and person_row(fid).person_id is None


# ── сквозной путь: бот не знает кота → карточка → привязка ──────────────────

async def test_unknown_cat_then_create_card_then_attach():
    app.dependency_overrides[current_user_id] = lambda: TG
    try:
        with patch("miniapp.backend.routes.cats.get_user_id", AsyncMock(return_value=MARK)):
            api = TestClient(app)
            reply = json.dumps({"fact": "Рыжик не ест рыбу", "category": CATEGORY, "связь": "рыжик", "ключ": "рыжик_еда"},
                               ensure_ascii=False)
            with patch.object(mem, "ask_claude", AsyncMock(return_value=reply)), \
                 patch.object(mem, "_resolve_alias", AsyncMock(return_value=None)):
                r = await mem.parse_and_store("Рыжик не ест рыбу", MARK, "☀️ Nexus")
            assert r["cat_id"] is None and r["cat_unknown"] == "рыжик"              # подсказка «заведи карточку»
            general = api.get("/api/cats/general").json()
            assert general["total"] == 1
            fid = general["facts"][0]["items"][0]["id"]
            cid = api.post("/api/cats", json={"name": "Рыжик"}).json()["id"]
            assert api.post(f"/api/cats/{cid}/facts/{fid}/attach").status_code == 200
            assert api.get("/api/cats/general").json()["total"] == 0
            card = api.get(f"/api/cats/{cid}").json()
            assert card["facts_count"] == 1 and card["facts"][0]["type"] == "dislikes"
            # и новые сообщения про Рыжика теперь привязываются сразу
            with patch.object(mem, "ask_claude", AsyncMock(return_value=reply.replace("не ест рыбу", "любит окно"))), \
                 patch.object(mem, "_resolve_alias", AsyncMock(return_value=None)):
                r2 = await mem.parse_and_store("Рыжик любит окно", MARK, "☀️ Nexus")
            assert r2["cat_id"] == cid
    finally:
        app.dependency_overrides.clear()


# ── доступ ───────────────────────────────────────────────────────────────────

def test_write_endpoints_require_owner():
    app.dependency_overrides[current_user_id] = lambda: TG
    try:
        with patch("miniapp.backend.routes.cats.get_user_id", AsyncMock(return_value=None)):
            c = TestClient(app)
            assert c.post("/api/cats", json={"name": "Тучка"}).status_code == 403
            assert c.patch("/api/cats/1", json={"name": "x"}).status_code == 403
            assert c.post("/api/cats/1/facts", json={"text": "x"}).status_code == 403
            assert c.post("/api/cats/1/facts/1/attach").status_code == 403
            assert _upload(c, 1).status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_write_endpoints_reject_unauthenticated():
    app.dependency_overrides.clear()
    c = TestClient(app)
    for resp in (c.post("/api/cats", json={"name": "Тучка"}), c.patch("/api/cats/1", json={"name": "x"}),
                 c.post("/api/cats/1/facts", json={"text": "x"}), c.post("/api/cats/1/facts/1/attach"), _upload(c, 1)):
        assert resp.status_code in (401, 403)
