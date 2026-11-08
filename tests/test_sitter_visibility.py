"""tests/test_sitter_visibility.py — инвентарь виден ситтеру по умолчанию (sitter_hidden скрывает), покупки и чеклисты по for_sitter.

Одно условие `sitter_visible_clause` для чтения ситтера и предпросмотра владельца. Реальная БД, вымышленные данные.
"""
from __future__ import annotations

import importlib.util
import itertools
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, insert, inspect, select, text

from core.config import config
from core.db import get_engine
from core.repos import pg_sitter_lists_repo as repo
from core.repos.lists_table import nexus_lists as nl
from miniapp.backend import cats_read_auth as cats_gate
from miniapp.backend import sitter_lists_auth as gate
from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id

REPO = Path(__file__).resolve().parent.parent
MARK, MARK2 = "test-sitter-visibility", "test-sitter-visibility-other"
TOKEN = "test-only-sitter-" + "e5" * 12
OWNER = 700000123
OK = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(config, "sitter_lists_token", TOKEN)
    monkeypatch.setattr(config, "sitter_lists_owner_tg_id", OWNER)
    monkeypatch.setattr(config, "cats_read_token", "")
    monkeypatch.setattr(config, "allowed_ids", [OWNER])
    monkeypatch.setattr(gate, "_limiter", gate.FailureLimiter())
    monkeypatch.setattr(cats_gate, "_limiter", cats_gate.FailureLimiter())
    app.dependency_overrides.pop(current_user_id, None)
    with patch("miniapp.backend.routes.sitter.get_user_id", AsyncMock(return_value=MARK)):
        yield
    app.dependency_overrides.pop(current_user_id, None)
    with get_engine().begin() as c:
        c.execute(delete(nl).where(nl.c.user_id.in_([MARK, MARK2])))


def mk(name, list_type, user=MARK, **kw) -> int:
    with get_engine().begin() as c:
        return c.execute(insert(nl).values(name=name, list_type=list_type, user_id=user, **kw).returning(nl.c.id)).scalar()


def sitter():
    return TestClient(app).get("/api/sitter/lists", headers=OK).json()


def names(body):
    return sorted([i["name"] for l in body["lists"] for i in l["items"]] + [i["name"] for c in body["inventory"] for i in c["items"]])


# ── миграция ──────────────────────────────────────────────────────────────────

def _mig():
    path = next((REPO / "alembic" / "versions").glob("*_nexus_lists_sitter_hidden.py"))
    spec = importlib.util.spec_from_file_location("_mig_hidden", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_migration_default_visible_and_reversible_keeps_rows():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    mig = _mig()
    cols = {c["name"]: c for c in inspect(get_engine()).get_columns("nexus_lists")}
    assert cols["sitter_hidden"]["nullable"] is False
    iid = mk("Пункт", "инвентарь")
    with get_engine().begin() as conn:
        assert conn.execute(select(nl.c.sitter_hidden).where(nl.c.id == iid)).scalar() is False
        with Operations.context(MigrationContext.configure(conn)):
            mig.downgrade()
            assert "sitter_hidden" not in {c["name"] for c in inspect(conn).get_columns("nexus_lists")}
            assert conn.execute(text("SELECT count(*) FROM nexus_lists WHERE id = :i"), {"i": iid}).scalar() == 1
            mig.upgrade()


# ── условие видимости: полная матрица ────────────────────────────────────────

TYPES = ("инвентарь", "покупки", "чеклист", "прочее")


def expected_visible(list_type, for_sitter, hidden, status, owner_ok):
    if not owner_ok or status == "archived":
        return False
    if list_type == "инвентарь":
        return not hidden
    if list_type in ("покупки", "чеклист"):
        return for_sitter
    return False


def test_visibility_matrix_every_combination():
    expected = set()
    for lt, fs, hd, st, own in itertools.product(TYPES, (False, True), (False, True), ("not_started", "done", "archived"), (True, False)):
        name = "|".join([lt, str(fs), str(hd), st, str(own)])
        mk(name, lt, user=MARK if own else MARK2, for_sitter=fs, sitter_hidden=hd, status=st,
           category="Кат" if lt == "инвентарь" else "", group_name="Гр" if lt != "инвентарь" else "")
        if expected_visible(lt, fs, hd, st, own):
            expected.add(name)
    got = set(names(sitter()))
    assert got == expected and len(expected) > 10
    assert {n for n in got if n.startswith("прочее")} == set()


def test_new_inventory_item_is_visible_immediately_without_any_flag_write():
    assert sitter()["inventory"] == []
    mk("Новый пункт", "инвентарь", category="Здоровье")
    assert names(sitter()) == ["Новый пункт"]


def test_new_purchase_and_checklist_are_not_visible_until_flagged():
    a, b = mk("Покупка", "покупки"), mk("Чек", "чеклист", group_name="Г")
    assert names(sitter()) == []
    repo.set_flag(MARK, a, True)
    repo.set_flag(MARK, b, True)
    assert names(sitter()) == sorted(["Чек", "Покупка"])


def test_for_sitter_flag_does_not_hide_or_force_inventory_and_hidden_does_not_affect_purchases():
    mk("Инв с for_sitter=false", "инвентарь", for_sitter=False)
    mk("Покупка скрытая-флагом", "покупки", sitter_hidden=True, for_sitter=True)
    mk("Инв скрытый и с for_sitter", "инвентарь", sitter_hidden=True, for_sitter=True)
    assert names(sitter()) == sorted(["Инв с for_sitter=false", "Покупка скрытая-флагом"])


# ── ответ ─────────────────────────────────────────────────────────────────────

def test_inventory_grouped_by_category_with_minimal_fields_and_no_leaks():
    mk("Аптечка", "инвентарь", category="Дом", note="верхний ящик", quantity=1, price_actual=777, price_plan=555,
       store="Магазин-секрет", task_id="TASK-SECRET", works_id="WORK-SECRET", priority="срочно", shared=True)
    mk("Свеча", "инвентарь", category="Дом", quantity=3)
    mk("Плед", "инвентарь", category="Спальня")
    body = sitter()
    assert [(c["category"], [i["name"] for i in c["items"]]) for c in body["inventory"]] == [("Дом", ["Аптечка", "Свеча"]), ("Спальня", ["Плед"])]
    assert body["inventory_count"] == 3 and body["count"] == 0
    item = body["inventory"][0]["items"][0]
    assert set(item) == {"name", "note", "quantity", "location"} and item["quantity"] == 1.0
    flat = str(body)
    for secret in ("777", "555", "Магазин-секрет", "TASK-SECRET", "WORK-SECRET", "срочно", MARK):
        assert secret not in flat


def test_lists_key_never_contains_inventory_and_inventory_key_never_contains_lists():
    mk("Инв", "инвентарь", category="К")
    mk("Покупка", "покупки", for_sitter=True)
    body = sitter()
    assert [i["name"] for l in body["lists"] for i in l["items"]] == ["Покупка"]
    assert [i["name"] for c in body["inventory"] for i in c["items"]] == ["Инв"]


def test_texts_are_clipped_and_cleaned_in_inventory():
    mk("н" * 500, "инвентарь", category="к" * 200, note="з" * 900 + "\x1b\x07")
    body = sitter()
    c = body["inventory"][0]
    assert len(c["category"]) == 60 and len(c["items"][0]["name"]) == 120 and len(c["items"][0]["note"]) == 300


# ── скрытие владельцем ────────────────────────────────────────────────────────

def owner():
    app.dependency_overrides[current_user_id] = lambda: OWNER
    return TestClient(app)


def test_owner_hides_and_unhides_inventory_item():
    iid = mk("Пункт", "инвентарь", category="К")
    c = owner()
    assert c.post(f"/api/lists/{iid}/sitter-hidden", json={"hidden": True}).json() == {"ok": True, "hidden": True}
    assert names(sitter()) == []
    c.post(f"/api/lists/{iid}/sitter-hidden", json={"hidden": False})
    assert names(sitter()) == ["Пункт"]


def test_hide_endpoint_only_for_own_inventory_items():
    purchase, foreign = mk("Покупка", "покупки"), mk("Чужой", "инвентарь", user=MARK2)
    c = owner()
    assert c.post(f"/api/lists/{purchase}/sitter-hidden", json={"hidden": True}).status_code == 404
    assert c.post(f"/api/lists/{foreign}/sitter-hidden", json={"hidden": True}).status_code == 404
    assert c.post("/api/lists/999999999/sitter-hidden", json={"hidden": True}).status_code == 404
    with get_engine().connect() as conn:
        assert conn.execute(select(nl.c.sitter_hidden).where(nl.c.id.in_([purchase, foreign]))).scalars().all() == [False, False]


def test_hide_endpoint_rejects_sitter_token_and_anonymous():
    iid = mk("Пункт", "инвентарь")
    assert TestClient(app).post(f"/api/lists/{iid}/sitter-hidden", json={"hidden": True}, headers=OK).status_code in (401, 403)
    assert TestClient(app).post(f"/api/lists/{iid}/sitter-hidden", json={"hidden": True}).status_code in (401, 403)


def test_lists_api_exposes_sitter_hidden_for_the_toggle():
    iid = mk("Пункт", "инвентарь", sitter_hidden=True)
    with patch("miniapp.backend.routes.lists.get_user_id", AsyncMock(return_value=MARK)):
        body = owner().get("/api/lists?type=inv").json()
    assert [i["sitter_hidden"] for i in body["items"] if i["id"] == str(iid) or i["id"] == iid] == [True]


# ── предпросмотр владельца ────────────────────────────────────────────────────

def strip_ids(x):
    if isinstance(x, dict):
        return {k: strip_ids(v) for k, v in x.items() if k != "id"}
    if isinstance(x, list):
        return [strip_ids(v) for v in x]
    return x


def test_preview_is_exactly_what_the_sitter_gets_plus_ids():
    mk("Инв", "инвентарь", category="Здоровье", note="н", quantity=2)
    mk("Скрытый", "инвентарь", category="Здоровье", sitter_hidden=True)
    mk("Покупка", "покупки", for_sitter=True, group_name="Г")
    mk("Чужое", "инвентарь", user=MARK2)
    prev = owner().get("/api/lists/sitter-preview").json()
    assert strip_ids(prev) == sitter()
    assert all("id" in i for c in prev["inventory"] for i in c["items"])


def test_preview_requires_owner_login_not_the_sitter_token():
    mk("Инв", "инвентарь")
    assert TestClient(app).get("/api/lists/sitter-preview", headers=OK).status_code in (401, 403)
    assert TestClient(app).get("/api/lists/sitter-preview").status_code in (401, 403)


def test_preview_route_does_not_shadow_other_list_routes():
    r = owner().get("/api/lists?type=buy")
    assert r.status_code == 200


# ── Mini App: переключатели не должны появляться на чужих экранах (другая таблица, id не совпадают) ──

def _block(src: str, start: str) -> str:
    a = src.index(start)
    b = src.find("\nfunction ", a + 10)
    return src[a:b if b > 0 else len(src)]


def test_sitter_toggles_are_wired_only_to_nexus_lists_screen():
    src = (REPO / "miniapp" / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    arcana = _block(src, "function ArArcanaPurchases")
    assert "<ListItemSheet" in arcana and "sitterMode" not in arcana                 # arcana_inventory: переключателей нет
    nx = _block(src, "function NxLists")
    assert 'sitterMode={tab === "inv" ? "hide" : "show"}' in nx and "SitterPreview" in nx
    sheet = _block(src, "function ListItemSheet")
    assert 'sitterMode === "show"' in sheet and 'sitterMode === "hide"' in sheet
    assert "/sitter-hidden" in sheet and "/sitter`" in sheet


def test_location_field_only_for_nexus_inventory_sheet():
    src = (REPO / "miniapp" / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    sheet = _block(src, "function ListItemSheet")
    assert 'if (sitterMode === "hide") body.location' in sheet           # в тело уходит только с экрана Nexus-инвентаря
    assert "Где лежит" in sheet and "setLocation" in sheet
    ad = (REPO / "miniapp" / "frontend" / "src" / "adapters.js").read_text(encoding="utf-8")
    assert "location: x.location" in ad
