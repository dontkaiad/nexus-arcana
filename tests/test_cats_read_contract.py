"""tests/test_cats_read_contract.py — Cats read API, N2: контракт ответов, который читает приложение heylark_cats.

Золотой файл tests/contracts/cats_read_v1.json (вымышленные данные) — ровно то, что отдают
GET /api/cats?with_facts=1 и GET /api/cats/{id} при чтении по Bearer-токену. Потребитель копирует файл к себе и гоняет
свои тесты на нём; расхождение ломает тесты по обе стороны. Пересоздать после осознанного изменения контракта:
    UPDATE_CONTRACT=1 pytest tests/test_cats_read_contract.py
Id и время нормализуются (в файле — порядковые номера и заглушка времени).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, insert, text

from core.cats import cat_fact_key
from core.config import config
from core.db import get_engine
from core.repos import pg_cats_repo as crepo
from core.repos.cats_tables import cats
from core.repos.memories_table import memories
from miniapp.backend import cats_read_auth as gate
from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id

MARK = "test-cats-read-contract"
TOKEN = "test-only-token-" + "b2" * 12
OWNER = 700000124                                              # вымышленный tg_id
HDR = {"Authorization": f"Bearer {TOKEN}"}
GOLDEN = Path(__file__).resolve().parent / "contracts" / "cats_read_v1.json"
PHOTO = "https://res.cloudinary.com/demo-cloud/image/upload/v1/cats/example-1.jpg"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(config, "cats_read_token", TOKEN)
    monkeypatch.setattr(config, "cats_read_owner_tg_id", OWNER)
    monkeypatch.setattr(config, "allowed_ids", [OWNER])
    monkeypatch.setattr(gate, "_limiter", gate.FailureLimiter())
    app.dependency_overrides.pop(current_user_id, None)
    with patch("miniapp.backend.routes.cats.get_user_id", AsyncMock(return_value=MARK)):
        yield
    with get_engine().begin() as c:
        c.execute(delete(memories).where(memories.c.user_id == MARK))
        c.execute(delete(cats).where(cats.c.user_id == MARK))
        c.execute(text("DELETE FROM tasks WHERE user_id = :u"), {"u": MARK})


def _fact(cat_id, text_, kind):
    with get_engine().begin() as c:
        c.execute(insert(memories).values(
            fact_text=text_, key_name=cat_fact_key(cat_id, kind, text_), category="🐾 Коты", related_to="",
            user_id=MARK, review_status="confirmed", cat_id=cat_id))


def _care_task(title, label, done_by=None):
    with get_engine().begin() as c:
        sid = c.execute(text("SELECT id FROM task_status WHERE code = 'Not started'")).scalar()
        tid = c.execute(text("INSERT INTO tasks (title, status_id, user_id, for_sitter, care_label) VALUES (:t, :s, :u, true, :l) RETURNING id"),
                        {"t": title, "s": sid, "u": MARK, "l": label}).scalar()
        if done_by:
            c.execute(text("INSERT INTO task_completions (task_id, done_by, source) VALUES (:i, 'x', :s)"), {"i": tid, "s": done_by})


@pytest.fixture
def world():
    t = crepo.create(MARK, "Тучка", ["Туча"], photo_url=PHOTO)
    p = crepo.create(MARK, "Пончик")                          # без фото и без кличек
    _fact(t, "Тучка: серая, пушистая, белое пятно на груди", "other")
    _fact(t, "Тучка не ест курицу", "dislikes")
    _fact(t, "Тучка любит окно", "likes")
    _fact(t, "Тучка: прячется под диван при гостях", "important")
    _fact(p, "Пончик рыжий, короткая шерсть", "other")
    with get_engine().begin() as c:                            # факт без привязки к коту — в списке только счётчик
        c.execute(insert(memories).values(fact_text="кошкам нужна свежая вода", key_name="g1", category="🐾 Коты",
                                          user_id=MARK, review_status="confirmed"))
    _care_task("Поменять лоток (метка)", "лотки", done_by="sitter")           # последнее выполнение — от ситтера
    _care_task("Поменять воду (метка)", "вода")                              # ещё ни разу не отмечено
    return {"t": t, "p": p}


def normalize(payload):
    """Заменяет id порядковыми номерами (коты 1.., факты 101..) и время — заглушкой, чтобы файл не зависел от БД."""
    cat_ids, fact_ids = {}, {}

    def walk(x, in_facts=False):
        if isinstance(x, list):
            return [walk(i, in_facts) for i in x]
        if not isinstance(x, dict):
            return x
        out = {}
        for k, v in x.items():
            if k == "id":
                m = fact_ids if in_facts else cat_ids
                base = 100 if in_facts else 0
                out[k] = m.setdefault(v, base + len(m) + 1)
            elif k in ("created_at", "last_done_at"):
                out[k] = "<timestamp>" if v is not None else None
            else:
                out[k] = walk(v, in_facts or k == "facts")
        return out
    return walk(payload)


def build(world):
    c = TestClient(app)
    lst = c.get("/api/cats", params={"with_facts": 1}, headers=HDR)
    card = c.get(f"/api/cats/{world['t']}", headers=HDR)
    assert lst.status_code == 200 and card.status_code == 200
    return {"version": 1, "list_with_facts": normalize(lst.json()), "card": normalize(card.json())}


def test_responses_match_the_golden_contract(world):
    got = build(world)
    if os.environ.get("UPDATE_CONTRACT") == "1":
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(got, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert GOLDEN.exists(), "нет золотого файла: UPDATE_CONTRACT=1 pytest tests/test_cats_read_contract.py"
    assert got == json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_golden_file_is_fictional_and_sane():
    g = json.loads(GOLDEN.read_text(encoding="utf-8"))
    cats_ = g["list_with_facts"]["cats"]
    assert g["version"] == 1 and [c["name"] for c in cats_] == ["Тучка", "Пончик"]
    assert cats_[0]["photo_url"].startswith("https://") and cats_[1]["photo_url"] is None
    assert {gr["type"] for gr in cats_[0]["facts"]} == {"important", "likes", "dislikes", "other"}
    assert g["list_with_facts"]["general_count"] == 1
    assert g["list_with_facts"]["care"] == [{"label": "лотки", "last_done_at": "<timestamp>", "last_done_by": "sitter"},
                                            {"label": "вода", "last_done_at": None, "last_done_by": None}]
    assert cats_[0]["facts_count"] == sum(len(gr["items"]) for gr in cats_[0]["facts"]) == 4
    assert g["card"]["facts"] == cats_[0]["facts"]                      # список с фактами и карточка согласованы


def test_list_without_with_facts_has_no_facts_but_counts(world):
    d = TestClient(app).get("/api/cats", headers=HDR).json()
    assert all("facts" not in c for c in d["cats"]) and [c["facts_count"] for c in d["cats"]] == [4, 1]


@pytest.mark.parametrize("value,has", [("1", True), ("true", True), ("0", False), ("false", False)])
def test_with_facts_flag_values(world, value, has):
    d = TestClient(app).get("/api/cats", params={"with_facts": value}, headers=HDR).json()
    assert ("facts" in d["cats"][0]) is has


def test_with_facts_is_one_query_not_one_per_cat(world):
    """Факты всех котов приходят одним запросом к memories (а не N+1)."""
    from sqlalchemy import event
    seen = []
    eng = get_engine()

    def count(conn, cursor, statement, *a):
        if "FROM memories" in statement and "cat_id = ANY" in statement and "SELECT cat_id," in statement:
            seen.append(statement)
    event.listen(eng, "before_cursor_execute", count)
    try:
        TestClient(app).get("/api/cats", params={"with_facts": 1}, headers=HDR)
    finally:
        event.remove(eng, "before_cursor_execute", count)
    assert len(seen) == 1


def test_with_facts_skips_removed_archived_and_other_owners(world):
    from sqlalchemy import update
    with get_engine().begin() as c:
        c.execute(insert(memories).values(fact_text="чужой факт", key_name="x1", category="🐾 Коты", user_id="someone-else",
                                          review_status="confirmed", cat_id=world["t"]))
        c.execute(insert(memories).values(fact_text="архивный", key_name="x2", category="🐾 Коты", user_id=MARK,
                                          review_status="confirmed", cat_id=world["t"], is_archived=True))
        c.execute(insert(memories).values(fact_text="отклонённый", key_name="x3", category="🐾 Коты", user_id=MARK,
                                          review_status="rejected", cat_id=world["t"]))
    try:
        d = TestClient(app).get("/api/cats", params={"with_facts": 1}, headers=HDR).json()
        texts = [i["text"] for g in d["cats"][0]["facts"] for i in g["items"]]
        assert "чужой факт" not in texts and "архивный" not in texts and "отклонённый" not in texts
        assert d["cats"][0]["facts_count"] == 4
    finally:
        with get_engine().begin() as c:
            c.execute(delete(memories).where(memories.c.user_id == "someone-else"))


def test_removed_cat_is_absent_from_list_and_card(world):
    crepo.update_cat(MARK, world["p"], {"status": "rejected"})
    c = TestClient(app)
    d = c.get("/api/cats", params={"with_facts": 1}, headers=HDR).json()
    assert [x["name"] for x in d["cats"]] == ["Тучка"]
    assert c.get(f"/api/cats/{world['p']}", headers=HDR).status_code == 404
