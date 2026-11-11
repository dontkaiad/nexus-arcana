"""tests/test_sitter_cat_care.py — GET /api/sitter/cat-care: паспорта котов для ситтера под читающим токеном.

Реальная БД, вымышленные коты. Главное: что ситтер получает (карточки с четырьмя типами фактов, действующие лекарства без скрытых владельцем, вес и визиты
ТОЛЬКО с флагом), минимальный набор полей (ничего лишнего не утекает), изоляция владельцев, токены, дни от Nexus в поясе владельца."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, insert, text

from core.cats import cat_fact_key
from core.config import config
from core.db import get_engine
from core.repos import pg_cat_health_repo as health
from core.repos import pg_cats_repo as crepo
from core.repos.cats_tables import cats
from core.repos.memories_table import memories
from miniapp.backend import care_write_auth as wgate
from miniapp.backend import cats_read_auth as cgate
from miniapp.backend import cats_write_auth as hgate
from miniapp.backend import sitter_lists_auth as gate
from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id

MARK, OTHER = "test-sitter-cat-care", "test-sitter-cat-care-other"
OWNER = 700000123
READ = "test-only-sitter-" + "a7" * 12
CATS = "test-only-cats-" + "b8" * 12
WRITE = "test-only-write-" + "c9" * 12
HEALTH = "test-only-health-" + "d1" * 12
OK = {"Authorization": f"Bearer {READ}"}
TZ = 3
URL = "/api/sitter/cat-care"


def today() -> date:
    return datetime.now(timezone(timedelta(hours=TZ))).date()


D = lambda n: today() + timedelta(days=n)  # noqa: E731


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k, v in (("sitter_lists_token", READ), ("sitter_lists_owner_tg_id", OWNER), ("cats_read_token", CATS), ("cats_read_owner_tg_id", OWNER),
                 ("care_write_token", WRITE), ("care_write_owner_tg_id", OWNER), ("cats_write_token", HEALTH), ("cats_write_owner_tg_id", OWNER)):
        monkeypatch.setattr(config, k, v)
    monkeypatch.setattr(config, "allowed_ids", [OWNER])
    for m in (gate, cgate, wgate, hgate):
        monkeypatch.setattr(m, "_limiter", m.FailureLimiter())
    app.dependency_overrides.pop(current_user_id, None)
    with patch("miniapp.backend.routes.sitter.get_user_id", AsyncMock(return_value=MARK)), \
         patch("core.cat_health._today_tz", AsyncMock(side_effect=lambda tg: (today(), TZ))):
        yield
    with get_engine().begin() as c:
        c.execute(delete(memories).where(memories.c.user_id.in_([MARK, OTHER])))
        c.execute(delete(cats).where(cats.c.user_id.in_([MARK, OTHER])))


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


def fact(cat_id, text_, kind="other", user=MARK, **kw):
    vals = dict(fact_text=text_, key_name=(cat_fact_key(cat_id, kind, text_) if cat_id is not None else "cats_general_" + str(abs(hash(text_)) % 10**8)), category="🐾 Коты", related_to="", user_id=user, review_status="confirmed", cat_id=cat_id)
    vals.update(kw)
    with get_engine().begin() as c:
        return c.execute(insert(memories).values(**vals).returning(memories.c.id)).scalar_one()


def get(client, headers=OK):
    return client.get(URL, headers=headers)


# ── what the sitter gets ──────────────────────────────────────────────────────────────────────────────────────────────

def test_cards_with_all_four_fact_types_current_only_for_confirmed_cats(client):
    a = crepo.create(MARK, "Тучка", ["Туча"], photo_url="https://example.invalid/t.jpg")
    pending = crepo.create(MARK, "Новенький", status="pending")
    gone = crepo.create(MARK, "Убранный")
    crepo.update_cat(MARK, gone, {"status": "rejected"})
    fact(a, "боится пылесоса", "important")
    fact(a, "рыжая с белой грудкой", "other")
    fact(a, "окно", "likes")
    fact(a, "громкие звуки", "dislikes")
    fact(a, "старое", "other", is_current=False)
    fact(pending, "не должен быть виден", "important")
    r = get(client)
    assert r.status_code == 200
    body = r.json()
    assert [c["name"] for c in body["cats"]] == ["Тучка"]
    assert body["cats"][0]["facts"] == {"important": ["боится пылесоса"], "other": ["рыжая с белой грудкой"], "likes": ["окно"], "dislikes": ["громкие звуки"]}
    assert body["cats"][0]["photo_url"] == "https://example.invalid/t.jpg"
    assert body["today"] == today().isoformat()


def test_texts_are_the_same_as_in_the_owner_cards_and_are_clipped_and_capped(client):
    a = crepo.create(MARK, "Тучка")
    fact(a, "Тучка не ест курицу", "important")
    for i in range(12):
        fact(a, f"внешность {i}", "other")
    fact(a, "я" * 400, "likes")
    f = get(client).json()["cats"][0]["facts"]
    from core.cats import group_facts
    same = [i["text"] for g in group_facts(crepo.list_facts(MARK, a), "Тучка", None) if g["type"] == "important" for i in g["items"]]
    assert f["important"] == same and len(same) == 1                                # the one place that shapes a fact's text (name prefix included)
    assert len(f["other"]) == 8 and len(f["likes"][0]) == 300 and f["likes"][0].endswith("…")


def test_only_current_courses_without_the_hidden_ones(client):
    a = crepo.create(MARK, "Тучка")
    health.add_med(MARK, a, "видно", D(-1), days=5, dose="по 2", times_per_day=2)
    health.add_med(MARK, a, "скрыто", D(-1), days=5, sitter_hidden=True)
    health.add_med(MARK, a, "не начался", D(3), days=5)
    health.add_med(MARK, a, "закончился", D(-10), days=5)
    done = health.add_med(MARK, a, "остановлен", D(-1), days=9)
    health.update_med(MARK, done["id"], {"status": "done"})
    health.add_med(MARK, a, "постоянный", D(-30))
    meds = get(client).json()["meds"]
    assert {m["name"]: (m["dose"], m["times_per_day"], m["ends_on"], m["days_left"]) for m in meds} == {
        "видно": ("по 2", 2, D(3).isoformat(), 3), "постоянный": (None, 1, None, None)}


def test_weight_and_visits_only_with_the_flag(client):
    a = crepo.create(MARK, "Тучка")
    health.add_weight(MARK, a, D(-3), 4100, "скрытая заметка")
    w_open = health.add_weight(MARK, a, D(0), 4200, "заметка")
    health.set_weight_visible(MARK, w_open["id"], True)
    health.add_visit(MARK, a, "скрытый визит", D(5), clinic="Клиника", note="заметка")
    v_open = health.add_visit(MARK, a, "осмотр", D(4), kind="vaccine", clinic="Клиника у дома", note="не показывать", next_due=D(300))
    health.update_visit(MARK, v_open["id"], {"sitter_visible": True})
    body = get(client).json()
    assert body["weights"] == [{"cat_id": a, "weighed_on": D(0).isoformat(), "grams": 4200}]
    assert body["visits"] == [{"cat_id": a, "kind": "vaccine", "title": "осмотр", "visit_on": D(4).isoformat(), "status": "planned", "days_until": 4}]


def test_with_no_flags_there_is_no_weight_and_no_visits(client):
    a = crepo.create(MARK, "Тучка")
    health.add_weight(MARK, a, D(0), 4200)
    health.add_visit(MARK, a, "осмотр", D(4))
    b = get(client).json()
    assert b["weights"] == [] and b["visits"] == []


def test_visit_window_and_status(client):
    a = crepo.create(MARK, "Тучка")
    def add(title, on, status="planned"):
        v = health.add_visit(MARK, a, title, on, status=status, sitter_visible=True)
        return v
    add("давно", D(-90), "done")
    add("недавний", D(-10), "done")
    add("отменён", D(5), "cancelled")
    add("скоро", D(6))
    got = {v["title"]: (v["status"], v["days_until"]) for v in get(client).json()["visits"]}
    assert got == {"недавний": ("done", -10), "скоро": ("planned", 6)}


def test_weights_are_the_latest_twelve_per_cat_newest_first(client):
    a = crepo.create(MARK, "Тучка")
    b = crepo.create(MARK, "Пончик")
    for i in range(15):
        w = health.add_weight(MARK, a, D(-i), 4000 + i)
        health.set_weight_visible(MARK, w["id"], True)
    wb = health.add_weight(MARK, b, D(0), 5000)
    health.set_weight_visible(MARK, wb["id"], True)
    ws = get(client).json()["weights"]
    mine = [w for w in ws if w["cat_id"] == a]
    assert len(mine) == 12 and mine[0]["weighed_on"] == D(0).isoformat() and mine[-1]["weighed_on"] == D(-11).isoformat()
    assert [w["grams"] for w in ws if w["cat_id"] == b] == [5000]


@pytest.mark.parametrize("status", ["rejected", "pending"])
def test_a_removed_or_unconfirmed_cat_hides_its_courses_weight_and_visits(client, status):
    a = crepo.create(MARK, "Тучка")
    health.add_med(MARK, a, "капли", D(0), days=5)
    w = health.add_weight(MARK, a, D(0), 4200); health.set_weight_visible(MARK, w["id"], True)
    health.add_visit(MARK, a, "осмотр", D(3), sitter_visible=True)
    crepo.update_cat(MARK, a, {"status": status})
    b = get(client).json()
    assert b["cats"] == [] and b["meds"] == [] and b["weights"] == [] and b["visits"] == []


# ── exactly which fields leave ────────────────────────────────────────────────────────────────────────────────────────

def test_the_field_set_is_minimal_and_nothing_else_leaks(client):
    a = crepo.create(MARK, "Тучка", ["Секретная кличка"], photo_url="https://example.invalid/t.jpg")
    fact(a, "критично", "important", source="manual")
    health.add_med(MARK, a, "капли", D(-1), days=5, dose="2 капли", note="секретная заметка курса")
    w = health.add_weight(MARK, a, D(0), 4200, "секретная заметка веса"); health.set_weight_visible(MARK, w["id"], True)
    v = health.add_visit(MARK, a, "осмотр", D(3), clinic="Секретная клиника", note="секретная заметка визита", next_due=D(300))
    health.update_visit(MARK, v["id"], {"sitter_visible": True})
    body = get(client).json()
    assert set(body) == {"today", "cats", "general", "meds", "weights", "visits"}
    assert set(body["cats"][0]) == {"id", "name", "photo_url", "facts"} and set(body["cats"][0]["facts"]) == {"important", "other", "likes", "dislikes"}
    assert set(body["meds"][0]) == {"cat_id", "name", "dose", "times_per_day", "ends_on", "days_left"}
    assert set(body["weights"][0]) == {"cat_id", "weighed_on", "grams"}
    assert set(body["visits"][0]) == {"cat_id", "kind", "title", "visit_on", "status", "days_until"}
    blob = json.dumps(body, ensure_ascii=False)
    for secret in ("Секретная", "секретная", "MARK", MARK, "user_id", "task_id", "sitter_visible", "sitter_hidden", "next_due", "clinic", "note", "created_at", "price", "cost"):
        assert secret not in blob


def test_days_are_nexus_own_in_the_owner_zone(client):
    a = crepo.create(MARK, "Тучка")
    v = health.add_visit(MARK, a, "осмотр", D(2), sitter_visible=True)
    m = health.add_med(MARK, a, "капли", D(0), days=4)
    with patch("core.cat_health._today_tz", AsyncMock(return_value=(D(1), TZ))):               # the owner's "today" is tomorrow of the server's
        body = get(client).json()
    assert body["today"] == D(1).isoformat() and body["visits"][0]["days_until"] == 1 and body["meds"][0]["days_left"] == 2
    assert v and m


# ── general facts («Общее про котов») ─────────────────────────────────────────────────────────────────────────────────

def general(text_, user=MARK, **kw):
    return fact(None, text_, "other", user=user, **kw)


def test_general_facts_are_current_texts_only_in_creation_order(client):
    general("правило один")
    general("правило два")
    general("устарело", is_current=False)
    general("архив", is_archived=True)
    general("отклонено", review_status="rejected")
    general("", review_status="confirmed")
    assert get(client).json()["general"] == ["правило один", "правило два"]


def test_general_has_no_cat_facts_no_person_facts_and_no_other_category(client):
    a = crepo.create(MARK, "Тучка")
    fact(a, "факт кота", "important")
    general("общее нормальное")
    general("про человека", person_id=None, related_to="Имя")                      # related_to alone does not hide a general fact
    other_cat = fact(None, "из другой категории", "other", category="💼 Работа")
    with get_engine().begin() as c:
        pid = c.execute(text("INSERT INTO people (user_id, name, is_self) VALUES (:u, 'Кто-то', false) RETURNING id"), {"u": MARK}).scalar_one()
        me = c.execute(text("INSERT INTO people (user_id, name, is_self) VALUES (:u, 'Я', true) RETURNING id"), {"u": MARK}).scalar_one()
    general("факт чужой карточки", person_id=pid)
    general("факт карточки Я", person_id=me)
    try:
        got = get(client).json()["general"]
        assert "факт кота" not in got and "из другой категории" not in got
        assert "факт чужой карточки" not in got and "факт карточки Я" not in got
        assert got == ["общее нормальное", "про человека"]
    finally:
        with get_engine().begin() as c:
            c.execute(delete(memories).where(memories.c.user_id == MARK))
            c.execute(text("DELETE FROM people WHERE user_id = :u"), {"u": MARK})
    assert other_cat


def test_general_is_clipped_capped_and_only_strings(client):
    general("я" * 400)                                                              # first in order, so it is inside the cap
    for i in range(15):
        general(f"общее {i}")
    body = get(client).json()
    assert len(body["general"]) == 12 and all(isinstance(t, str) for t in body["general"])
    from core.sitter_cat_care import FACT_TEXT
    assert all(len(t) <= FACT_TEXT for t in body["general"]) and body["general"][0].endswith("…")


def test_general_leaks_no_ids_keys_or_dates_and_no_other_owner(client):
    general("видно ситтеру", source="manual")
    general("чужое общее", user=OTHER)
    body = get(client).json()
    assert body["general"] == ["видно ситтеру"]
    blob = json.dumps(body, ensure_ascii=False)
    for secret in ("чужое", "cats_general", "cat_", "key_name", "created_at", "updated_at", "source", "review_status", "related_to", "user_id", MARK):
        assert secret not in blob


def test_general_is_empty_list_when_there_are_none(client):
    crepo.create(MARK, "Тучка")
    assert get(client).json()["general"] == []


# ── isolation and tokens ──────────────────────────────────────────────────────────────────────────────────────────────

def test_another_owners_data_is_never_in_the_answer(client):
    mine = crepo.create(MARK, "Тучка")
    theirs = crepo.create(OTHER, "Чужой")
    fact(theirs, "чужой факт", "important", user=OTHER)
    health.add_med(OTHER, theirs, "чужое лекарство", D(0), days=3)
    w = health.add_weight(OTHER, theirs, D(0), 5000); health.set_weight_visible(OTHER, w["id"], True)
    health.add_visit(OTHER, theirs, "чужой визит", D(2), sitter_visible=True)
    body = get(client).json()
    assert [c["name"] for c in body["cats"]] == ["Тучка"] and body["meds"] == [] and body["weights"] == [] and body["visits"] == []
    assert "Чужой" not in json.dumps(body, ensure_ascii=False) and mine


def test_only_the_read_token_works_here(client):
    crepo.create(MARK, "Тучка")
    assert client.get(URL).status_code == 401
    for tok in (CATS, WRITE, HEALTH, READ + "x", ""):
        assert client.get(URL, headers={"Authorization": f"Bearer {tok}"}).status_code == 401, tok[:12]
    assert client.get(URL, headers={"Authorization": READ}).status_code == 401
    assert get(client).status_code == 200
    assert client.post(URL, headers=OK, json={}).status_code == 405                          # read only


def test_the_read_token_does_not_open_the_health_routes_and_health_token_does_not_open_this(client):
    assert client.get("/api/cat-health/upcoming", headers=OK).status_code == 401
    assert client.get(URL, headers={"Authorization": f"Bearer {HEALTH}"}).status_code == 401


def test_nothing_is_written_by_reading(client):
    a = crepo.create(MARK, "Тучка")
    health.add_med(MARK, a, "капли", D(-10), days=3)                                          # ended: reading must not close it or touch it
    before = health.get_med(MARK, health.list_meds(MARK, a)[0]["id"])
    get(client)
    after = health.get_med(MARK, before["id"])
    assert after["status"] == "active" and after["updated_at"] == before["updated_at"]


def test_a_missing_owner_is_404_not_a_leak(client):
    with patch("miniapp.backend.routes.sitter.get_user_id", AsyncMock(return_value=None)):
        assert get(client).status_code == 404


def test_the_gate_is_not_bypassed_by_an_owner_session(client):
    crepo.create(MARK, "Тучка")
    app.dependency_overrides[current_user_id] = lambda: OWNER
    try:
        assert client.get(URL).status_code == 401
    finally:
        app.dependency_overrides.pop(current_user_id, None)
