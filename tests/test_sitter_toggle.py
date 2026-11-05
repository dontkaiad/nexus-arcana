"""tests/test_sitter_toggle.py — переключатель «для ситтера» в Mini App: for_sitter в GET /api/lists,
POST /api/lists/sitter-group (весь чеклист). Реальная БД, вымышленные данные."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, insert, select

from core.config import config
from core.db import get_engine
from core.repos.lists_table import nexus_lists as nl
from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id

MARK, MARK2 = "test-sitter-toggle", "test-sitter-toggle-other"
OWNER = 700000123


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(config, "allowed_ids", [OWNER])
    app.dependency_overrides[current_user_id] = lambda: OWNER
    with patch("miniapp.backend.routes.sitter.get_user_id", AsyncMock(return_value=MARK)), \
         patch("miniapp.backend.routes.lists.get_user_id", AsyncMock(return_value=MARK)):
        yield
    app.dependency_overrides.pop(current_user_id, None)
    with get_engine().begin() as c:
        c.execute(delete(nl).where(nl.c.user_id.in_([MARK, MARK2])))


def mk(name, user=MARK, **kw) -> int:
    with get_engine().begin() as c:
        return c.execute(insert(nl).values(name=name, user_id=user, **kw).returning(nl.c.id)).scalar()


def flag(iid):
    with get_engine().connect() as c:
        return c.execute(select(nl.c.for_sitter).where(nl.c.id == iid)).scalar()


def test_lists_api_exposes_for_sitter_flag():
    on, off = mk("Включён", list_type="покупки", for_sitter=True), mk("Выключен", list_type="покупки")
    items = {i["name"]: i for i in TestClient(app).get("/api/lists?type=buy").json()["items"]}
    assert items["Включён"]["for_sitter"] is True and items["Выключен"]["for_sitter"] is False
    assert on and off


def test_item_toggle_roundtrip_visible_in_lists_api():
    iid = mk("Совок", list_type="покупки")
    c = TestClient(app)
    assert c.post(f"/api/lists/{iid}/sitter", json={"for_sitter": True}).json() == {"ok": True, "for_sitter": True}
    assert [i["for_sitter"] for i in c.get("/api/lists?type=buy").json()["items"] if i["name"] == "Совок"] == [True]
    c.post(f"/api/lists/{iid}/sitter", json={"for_sitter": False})
    assert flag(iid) is False


def test_group_toggle_marks_only_active_items_of_that_checklist():
    a, b = mk("Пункт 1", list_type="чеклист", group_name="Перед отъездом"), mk("Пункт 2", list_type="чеклист", group_name="Перед отъездом")
    arch = mk("Архивный", list_type="чеклист", group_name="Перед отъездом", status="archived")
    other_group = mk("Из другого", list_type="чеклист", group_name="Другой список")
    other_type = mk("Не чеклист", list_type="покупки", group_name="Перед отъездом")
    foreign = mk("Чужой", user=MARK2, list_type="чеклист", group_name="Перед отъездом")
    r = TestClient(app).post("/api/lists/sitter-group", json={"group": "Перед отъездом", "for_sitter": True})
    assert r.json() == {"ok": True, "for_sitter": True, "items": 2}
    assert (flag(a), flag(b)) == (True, True)
    assert [flag(x) for x in (arch, other_group, other_type, foreign)] == [False, False, False, False]
    TestClient(app).post("/api/lists/sitter-group", json={"group": "Перед отъездом", "for_sitter": False})
    assert (flag(a), flag(b)) == (False, False)


def test_group_toggle_unknown_empty_or_foreign_group():
    mk("Чужой", user=MARK2, list_type="чеклист", group_name="Секретный список")
    c = TestClient(app)
    assert c.post("/api/lists/sitter-group", json={"group": "Нет такого", "for_sitter": True}).status_code == 404
    assert c.post("/api/lists/sitter-group", json={"group": "Секретный список", "for_sitter": True}).status_code == 404
    assert c.post("/api/lists/sitter-group", json={"group": "   ", "for_sitter": True}).status_code == 400
    assert c.post("/api/lists/sitter-group", json={"for_sitter": True}).status_code == 422


def test_group_toggle_requires_owner_login():
    app.dependency_overrides.pop(current_user_id, None)
    mk("Пункт", list_type="чеклист", group_name="Список")
    r = TestClient(app).post("/api/lists/sitter-group", json={"group": "Список", "for_sitter": True})
    assert r.status_code in (401, 403)


def test_group_route_does_not_shadow_item_route():
    iid = mk("Пункт", list_type="чеклист", group_name="Список")
    assert TestClient(app).post(f"/api/lists/{iid}/sitter", json={"for_sitter": True}).status_code == 200
    assert flag(iid) is True


def test_group_toggle_leads_to_sitter_endpoint_output(monkeypatch):
    """Сквозняк: пометили чеклист владельцем → ситтер видит его по своему токену."""
    from miniapp.backend import sitter_lists_auth as gate
    tok = "test-only-sitter-" + "d4" * 12
    monkeypatch.setattr(config, "sitter_lists_token", tok)
    monkeypatch.setattr(config, "sitter_lists_owner_tg_id", OWNER)
    monkeypatch.setattr(config, "cats_read_token", "")
    monkeypatch.setattr(gate, "_limiter", gate.FailureLimiter())
    mk("Полить цветы", list_type="чеклист", group_name="Дом")
    c = TestClient(app)
    assert c.get("/api/sitter/lists", headers={"Authorization": f"Bearer {tok}"}).json()["count"] == 0
    c.post("/api/lists/sitter-group", json={"group": "Дом", "for_sitter": True})
    body = c.get("/api/sitter/lists", headers={"Authorization": f"Bearer {tok}"}).json()
    assert body["count"] == 1 and body["lists"][0]["name"] == "Дом" and body["lists"][0]["items"][0]["name"] == "Полить цветы"
