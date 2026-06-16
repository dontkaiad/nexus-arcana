"""Mini App — Arcana: расклады, клиенты, ритуалы, гримуар, статистика, таро.

GET /api/arcana/{today,sessions,clients,rituals,grimoire,stats,moon-phases},
POST verify/result/clients/photo/object_photo/summarize, tarot helpers.

Собрано из wave2b / wave3 / wave5 / wave6 при реорганизации тестов по доменам.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from miniapp.backend import cache
from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id


FAKE_TG_ID = 67686090
FAKE_NOTION_USER = "user-notion-id-42"


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    db_file = tmp_path / "adhd_cache.db"
    monkeypatch.setattr(cache, "_DB_PATH", str(db_file))
    cache._init_db()
    yield


@pytest.fixture
def client():
    app.dependency_overrides[current_user_id] = lambda: FAKE_TG_ID
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _today_date(tz: int = 3):
    return (datetime.now(timezone.utc) + timedelta(hours=tz)).date()


# ── helpers: fake Notion pages ───────────────────────────────────────────────

def _client_page(cid, name, status="🟢 Активный", contact="", request="", notes="", photo=None):
    props = {
        "Имя": {"title": [{"plain_text": name}]},
        "Статус": {"status": {"name": status}},
        "Контакт": {"rich_text": [{"plain_text": contact}] if contact else []},
        "Запрос": {"rich_text": [{"plain_text": request}] if request else []},
        "Заметки": {"rich_text": [{"plain_text": notes}] if notes else []},
        "🪪 Пользователи": {"relation": [{"id": FAKE_NOTION_USER}]},
    }
    if photo:
        props["Фото"] = {"url": photo}
    return {"id": cid, "properties": props, "created_time": "2026-01-15T00:00:00Z"}


def _session_page(sid, question, *, date=None, session_type="🤝 Клиентский",
                  client_ids=None, area=None, deck=None, spread=None,
                  cards="", interp="", done="⏳ Не проверено",
                  price=0, paid=0, photo=None):
    props = {
        "Тема": {"title": [{"plain_text": question}]},
        "Дата": {"date": {"start": date} if date else None},
        "Тип сеанса": {"select": {"name": session_type}},
        "Тип расклада": {"multi_select": [{"name": spread}] if spread else []},
        "Область": {"multi_select": [{"name": a} for a in (area or [])]},
        "Колоды": {"multi_select": [{"name": deck}] if deck else []},
        "Карты": {"rich_text": [{"plain_text": cards}] if cards else []},
        "Трактовка": {"rich_text": [{"plain_text": interp}] if interp else []},
        "Сбылось": {"select": {"name": done}},
        "Сумма": {"number": price},
        "Оплачено": {"number": paid},
        "👥 Клиенты": {"relation": [{"id": c} for c in (client_ids or [])]},
        "🪪 Пользователи": {"relation": [{"id": FAKE_NOTION_USER}]},
    }
    if photo:
        props["Фото"] = {"url": photo}
    return {"id": sid, "properties": props, "created_time": "2026-04-10T00:00:00Z"}


def _ritual_page(rid, name, *, date=None, client_ids=None, goal=None,
                 place=None, ritual_type="🤝 Клиентский",
                 consumables="", structure="", offerings="", powers="",
                 duration=0, price=0, paid=0, result="⏳ Не проверено",
                 notes="", goal_multi=None):
    props = {
        "Название": {"title": [{"plain_text": name}]},
        "Дата": {"date": {"start": date} if date else None},
        "Тип": {"select": {"name": ritual_type}},
        "Место": {"select": {"name": place}} if place else {"select": None},
        "Расходники": {"rich_text": [{"plain_text": consumables}] if consumables else []},
        "Подношения/Откуп": {"rich_text": [{"plain_text": offerings}] if offerings else []},
        "Силы": {"rich_text": [{"plain_text": powers}] if powers else []},
        "Структура": {"rich_text": [{"plain_text": structure}] if structure else []},
        "Заметки": {"rich_text": [{"plain_text": notes}] if notes else []},
        "Время (мин)": {"number": duration},
        "Цена за ритуал": {"number": price},
        "Оплачено": {"number": paid},
        "Результат": {"select": {"name": result}},
        "👥 Клиенты": {"relation": [{"id": c} for c in (client_ids or [])]},
        "🪪 Пользователи": {"relation": [{"id": FAKE_NOTION_USER}]},
    }
    if goal_multi:
        props["Цель"] = {"multi_select": [{"name": g} for g in goal_multi]}
    elif goal:
        props["Цель"] = {"select": {"name": goal}}
    return {"id": rid, "properties": props, "created_time": "2026-04-10T00:00:00Z"}


def _grim_page(gid, name, cat, themes=None, text="", source=""):
    from arcana.repos.grimoire_repo import GrimoireEntry
    return GrimoireEntry(
        id=gid, title=name, category=cat,
        themes=list(themes or []),
        verified=False, text=text, source=source,
    )


def _make_ritual(rid, name, *, goal=None, place=None, result="unverified",
                 price=0, paid=0, date=None, type_code=None,
                 consumables="", structure="", offerings="", powers="",
                 time_min=None, notes=None, photo_url=None):
    from arcana.repos.rituals_repo import Ritual
    from decimal import Decimal
    from datetime import datetime, timezone
    dt = None
    if date:
        dt = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return Ritual(
        id=rid, name=name, goal=goal, place=place, result=result,
        price=Decimal(str(price)), paid=Decimal(str(paid)), date=dt,
        type_code=type_code, consumables=consumables, structure=structure,
        offerings=offerings, powers=powers, time_min=time_min,
        notes=notes, photo_url=photo_url,
    )


def _mock_rituals_repo(list_all_result=None, find_by_id_result=None, set_result_ok=True,
                       update_photo_ok=True):
    repo = MagicMock()
    repo.list_all = AsyncMock(return_value=list_all_result or [])
    repo.find_by_id = AsyncMock(return_value=find_by_id_result)
    repo.set_result = AsyncMock(return_value=set_result_ok)
    repo.update_photo_url = AsyncMock(return_value=update_photo_ok)
    return repo


def _make_client(cid, name, *, contact="", request="", notes="",
                 type_code="paid", status_code="active", birthday=None,
                 photo_url=None, object_photos=None):
    from arcana.repos.clients_repo import Client
    return Client(
        id=cid, name=name, contact=contact, request=request,
        notes=notes, since="",
        type_code=type_code, status_code=status_code,
        birthday=birthday, photo_url=photo_url, object_photos=object_photos,
    )


def _make_triplet(sid, question, *, client_id=None, date=None, amount=0, paid=0,
                  outcome="unverified", area="", spread_type="", cards="",
                  interpretation="", barter_what="", session_name="",
                  deck="Уэйт", photo_url=None, bottom_card="", triplet_summary=""):
    from arcana.repos.sessions_repo import TripletEntry
    from decimal import Decimal
    return TripletEntry(
        id=sid, question=question, cards=cards, interpretation=interpretation,
        deck=deck, session_name=session_name, client_id=client_id,
        date=date or "", outcome=outcome,
        amount=Decimal(str(amount)), paid=Decimal(str(paid)),
        spread_type=spread_type, area=area, barter_what=barter_what,
        bottom_card=bottom_card, photo_url=photo_url,
        triplet_summary=triplet_summary,
    )


def _mock_clients_repo(list_all_result=None, find_by_id_result=None,
                       add_result=None, get_object_photos_result=""):
    repo = MagicMock()
    repo.list_all = AsyncMock(return_value=list_all_result or [])
    repo.find_by_id = AsyncMock(return_value=find_by_id_result)
    repo.add = AsyncMock(return_value=add_result)
    repo.update_profile = AsyncMock(return_value=None)
    repo.update_object_photos = AsyncMock(return_value=None)
    repo.update_photo_url = AsyncMock(return_value=None)
    return repo


def _mock_sessions_repo_all(all_result=None, find_result=None, slug_result=None):
    repo = MagicMock()
    repo.list_all = AsyncMock(return_value=all_result or [])
    repo.list_by_client = AsyncMock(return_value=[])
    repo.find_by_id = AsyncMock(return_value=find_result)
    repo.list_by_slug = AsyncMock(return_value=slug_result or [])
    repo.set_photo_url = AsyncMock(return_value=True)
    repo.update_summary = AsyncMock(return_value=None)
    repo.set_outcome = AsyncMock(return_value=True)
    return repo


def _mock_grimoire_repo(list_all_result=None, find_by_id_result=None):
    """Helper: MagicMock с нужными AsyncMock-методами для _grimoire_repo."""
    repo = MagicMock()
    repo.list_all = AsyncMock(return_value=list_all_result or [])
    repo.find_by_id = AsyncMock(return_value=find_by_id_result)
    return repo


def _work_page(wid, title, *, cat=None, prio="🟡 Важно", deadline=None):
    return {
        "id": wid,
        "properties": {
            "Работа": {"title": [{"plain_text": title}]},
            "Status": {"status": {"name": "Not started"}},
            "Приоритет": {"select": {"name": prio}},
            "Категория": {"select": {"name": cat}} if cat else {"select": None},
            "Дедлайн": {"date": {"start": deadline} if deadline else None},
            "👥 Клиенты": {"relation": []},
        },
    }


def _page(pid: str, *, owner: str = FAKE_NOTION_USER, extra: dict | None = None) -> dict:
    props = {
        "🪪 Пользователи": {"relation": [{"id": owner}]},
        "Статус": {"status": {"name": "Not started"}},
        "Задача": {"title": [{"plain_text": "Test"}]},
    }
    if extra:
        props.update(extra)
    return {"id": pid, "properties": props}


# ── /api/arcana/today ────────────────────────────────────────────────────────

def test_arcana_today_happy(client):
    tz = 3
    today = _today_date(tz)
    today_iso = today.isoformat()

    sessions_pg = [
        _make_triplet("s_today", "Что думает Вадим",
                      client_id="c1", date=today_iso, area="Отношения",
                      spread_type="🗝️ Кельтский крест", outcome="unverified"),
        _make_triplet("s_old_pending", "Стар Q",
                      client_id="c1",
                      date=(today - timedelta(days=60)).isoformat(),
                      outcome="unverified"),
        _make_triplet("s_yes", "Сбылось",
                      client_id="c1",
                      date=(today - timedelta(days=5)).isoformat(),
                      outcome="yes"),
    ]
    clients_list = [_make_client("c1", "Анна")]
    works = [_work_page("w1", "Свечи", cat="🕯️ Расходники", deadline=today_iso)]
    fin = [
        {"id": "f1", "properties": {
            "Сумма": {"number": 10000},
            "Тип": {"select": {"name": "💰 Доход"}},
            "Категория": {"select": {"name": "🔮 Практика"}},
        }},
        {"id": "f2", "properties": {
            "Сумма": {"number": 500},
            "Тип": {"select": {"name": "💸 Расход"}},
            "Категория": {"select": {"name": "🕯️ Расходники"}},
        }},
    ]
    mock_sess = _mock_sessions_repo_all(all_result=sessions_pg)
    mock_cl = _mock_clients_repo(list_all_result=clients_list)

    with patch("miniapp.backend.routes.arcana_today._pg_sessions_repo", mock_sess), \
         patch("miniapp.backend.routes._arcana_common._common_clients_repo", mock_cl), \
         patch("miniapp.backend.routes.arcana_today.query_pages",
               AsyncMock(return_value=works)), \
         patch("miniapp.backend.routes._arcana_common.query_pages",
               AsyncMock(return_value=fin)), \
         patch("miniapp.backend.routes.arcana_today.today_user_tz",
               AsyncMock(return_value=(today, tz))), \
         patch("miniapp.backend.routes.arcana_today.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/arcana/today")

    assert r.status_code == 200, r.text
    data = r.json()
    for k in ("date", "weekday", "tz_offset", "moon", "sessions_today",
              "works_today", "unchecked_30d", "accuracy", "month_stats"):
        assert k in data
    assert data["date"] == today_iso
    # moon всегда валиден
    assert data["moon"]["idx"] in range(8)
    # один сегодняшний сеанс
    assert len(data["sessions_today"]) == 1
    assert data["sessions_today"][0]["client"] == "Анна"
    # unchecked_30d: один сеанс >30d назад и в pending
    assert data["unchecked_30d"] == 1
    # accuracy: один yes среди одного verified → 100
    assert data["accuracy"] == 100
    # month_stats
    assert data["month_stats"]["income"] == 10000
    assert data["month_stats"]["supplies"] == 500


def test_arcana_today_401():
    app.dependency_overrides.clear()
    c = TestClient(app)
    assert c.get("/api/arcana/today").status_code == 401


# ── /api/arcana/sessions ─────────────────────────────────────────────────────

def test_arcana_sessions_list_and_filter(client):
    tz = 3
    today = _today_date(tz)
    sessions_pg = [
        _make_triplet("s1", "Вопрос A", client_id="c1", area="Отношения",
                      cards="Шут, Маг, Жрица", date=today.isoformat()),
        _make_triplet("s2", "Вопрос B", client_id="c2", area="Финансы",
                      date=today.isoformat()),
    ]
    clients_pg = [_make_client("c1", "Анна"), _make_client("c2", "Борис")]
    mock_sess = _mock_sessions_repo_all(all_result=sessions_pg)
    mock_cl = _mock_clients_repo(list_all_result=clients_pg)

    with patch("miniapp.backend.routes.arcana_sessions._sessions_repo", mock_sess), \
         patch("miniapp.backend.routes.arcana_sessions._clients_repo", mock_cl), \
         patch("miniapp.backend.routes.arcana_sessions.today_user_tz",
               AsyncMock(return_value=(today, tz))), \
         patch("miniapp.backend.routes.arcana_sessions.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r_all = client.get("/api/arcana/sessions")
        r_area = client.get("/api/arcana/sessions?filter=area:Отношения")
        r_client = client.get("/api/arcana/sessions?filter=client_id:c2")

    assert r_all.status_code == 200
    assert r_all.json()["total"] == 2
    first = r_all.json()["sessions"][0]
    assert first["client"] == "Анна"
    assert "ru_title" in first
    assert "status" in first

    assert r_area.json()["total"] == 1
    assert r_area.json()["sessions"][0]["id"] == "s1"
    assert r_client.json()["total"] == 1
    assert r_client.json()["sessions"][0]["client"] == "Борис"


def test_arcana_sessions_401():
    app.dependency_overrides.clear()
    c = TestClient(app)
    assert c.get("/api/arcana/sessions").status_code == 401


# ── /api/arcana/sessions/{id} ───────────────────────────────────────────────

def test_arcana_session_detail_parses_cards_and_bottom(client):
    tz = 3
    today = _today_date(tz)
    triplet = _make_triplet(
        "sX", "Планы Вадима",
        client_id="c1", date=today.isoformat(),
        area="Отношения", deck="Dark Wood Tarot",
        spread_type="🗝️ Кельтский крест",
        cards="1. Суть — Туз Мечей\n2. Препятствие — Башня\n3. Совет — Звезда",
        interpretation="Расклад указывает на...",
        bottom_card="Король Кубков",
        outcome="unverified", amount=3000, paid=0,
    )
    mock_sess = _mock_sessions_repo_all(find_result=triplet)
    mock_cl = _mock_clients_repo(list_all_result=[_make_client("c1", "Кай")])

    with patch("miniapp.backend.routes.arcana_sessions._sessions_repo", mock_sess), \
         patch("miniapp.backend.routes.arcana_sessions._clients_repo", mock_cl), \
         patch("miniapp.backend.routes.arcana_sessions.today_user_tz",
               AsyncMock(return_value=(today, tz))), \
         patch("miniapp.backend.routes.arcana_sessions.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/arcana/sessions/sX")

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["question"] == "Планы Вадима"
    assert data["client"] == "Кай"
    assert data["bottom"] == {"name": "Король Кубков", "icon": None}
    assert "Дно" not in (data["interpretation"] or "")
    assert "Расклад указывает" in data["interpretation"]
    assert len(data["cards"]) >= 3
    assert "en" in data["cards"][0] or "raw" in data["cards"][0]
    assert data["cards_raw"] is not None


def test_arcana_session_detail_404_not_found(client):
    mock_sess = _mock_sessions_repo_all(find_result=None)
    with patch("miniapp.backend.routes.arcana_sessions._sessions_repo", mock_sess), \
         patch("miniapp.backend.routes.arcana_sessions._clients_repo",
               _mock_clients_repo(list_all_result=[])), \
         patch("miniapp.backend.routes.arcana_sessions.today_user_tz",
               AsyncMock(return_value=(_today_date(3), 3))), \
         patch("miniapp.backend.routes.arcana_sessions.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/arcana/sessions/sX")
    assert r.status_code == 404


# ── /api/arcana/clients ──────────────────────────────────────────────────────

def test_arcana_clients_list_aggregates_stats(client):
    clients_list = [_make_client("1", "Анна"), _make_client("2", "Борис")]
    sessions = [
        _make_triplet("s1", "Q", client_id="1", date="2026-04-05", amount=3000, paid=0),
        _make_triplet("s2", "Q", client_id="1", date="2026-04-06", amount=3000, paid=3000),
    ]
    rituals = [_make_ritual("r1", "Защита", date="2026-03-18", price=5000, paid=5000)]
    rituals[0].client_id = "1"

    mock_cl = _mock_clients_repo(list_all_result=clients_list)
    mock_sess = _mock_sessions_repo_all(all_result=sessions)
    mock_rit = _mock_rituals_repo(list_all_result=rituals)

    with patch("miniapp.backend.routes.arcana_clients._clients_repo", mock_cl), \
         patch("miniapp.backend.routes.arcana_clients._sessions_repo", mock_sess), \
         patch("miniapp.backend.routes.arcana_clients._rituals_repo", mock_rit), \
         patch("miniapp.backend.routes.arcana_clients.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/arcana/clients")

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"] == 2
    assert data["total_debt"] == 3000
    anna = next(c for c in data["clients"] if c["id"] == "1")
    assert anna["sessions_count"] == 2
    assert anna["rituals_count"] == 1
    assert anna["debt"] == 3000
    assert anna["total_paid"] == 3000 + 5000
    assert anna["initial"] == "А"


def test_arcana_clients_401():
    app.dependency_overrides.clear()
    c = TestClient(app)
    assert c.get("/api/arcana/clients").status_code == 401


# ── /api/arcana/clients/{id} ─────────────────────────────────────────────────

def test_arcana_client_dossier_mixes_history(client):
    c_obj = _make_client("1", "Анна", contact="@anna", request="Отношения")
    sess = [_make_triplet("s1", "Кельтский крест — отношения",
                          client_id="1", date="2026-04-05", amount=3000, paid=0)]
    rits = [_make_ritual("r1", "Ритуал защиты", date="2026-03-18", price=5000, paid=5000)]

    mock_cl = _mock_clients_repo(find_by_id_result=c_obj)
    mock_sess = _mock_sessions_repo_all(all_result=sess)
    mock_rit = _mock_rituals_repo(list_all_result=rits)
    mock_rit.list_by_client = AsyncMock(return_value=rits)

    with patch("miniapp.backend.routes.arcana_clients._clients_repo", mock_cl), \
         patch("miniapp.backend.routes.arcana_clients._sessions_repo", mock_sess), \
         patch("miniapp.backend.routes.arcana_clients._rituals_repo", mock_rit), \
         patch("miniapp.backend.routes.arcana_clients.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/arcana/clients/1")

    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "Анна"
    assert data["contact"] == "@anna"
    assert data["stats"]["debt"] == 3000
    assert data["stats"]["total_paid"] == 5000
    assert data["since"] == "2026-04-05"
    ids = [h["id"] for h in data["history"]]
    assert ids == ["s1", "r1"]  # DESC по дате
    session_hist = data["history"][0]
    assert session_hist["paid"] is False  # 0 < 3000
    assert data["history"][1]["paid"] is True


def test_arcana_client_dossier_404_not_found(client):
    mock_cl = _mock_clients_repo(find_by_id_result=None)
    with patch("miniapp.backend.routes.arcana_clients._clients_repo", mock_cl), \
         patch("miniapp.backend.routes.arcana_clients.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/arcana/clients/999")
    assert r.status_code == 404


# ── /api/arcana/rituals ──────────────────────────────────────────────────────

def test_arcana_rituals_list_and_filter_by_goal(client):
    entries = [
        _make_ritual("r1", "Защита", goal="protect"),
        _make_ritual("r2", "Очищение", goal="cleanse"),
        _make_ritual("r3", "Защита для Бориса", goal="protect"),
    ]
    with patch("miniapp.backend.routes.arcana_rituals._rituals_repo",
               _mock_rituals_repo(list_all_result=entries)), \
         patch("miniapp.backend.routes.arcana_rituals.today_user_tz",
               AsyncMock(return_value=(_today_date(3), 3))), \
         patch("miniapp.backend.routes.arcana_rituals.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r_all = client.get("/api/arcana/rituals")
        r_filt = client.get("/api/arcana/rituals?goal=%F0%9F%9B%A1%EF%B8%8F%20%D0%97%D0%B0%D1%89%D0%B8%D1%82%D0%B0")

    assert r_all.status_code == 200
    assert r_all.json()["total"] == 3
    assert r_filt.json()["total"] == 2
    ids = {it["id"] for it in r_filt.json()["rituals"]}
    assert ids == {"r1", "r3"}


def test_arcana_rituals_401():
    app.dependency_overrides.clear()
    c = TestClient(app)
    assert c.get("/api/arcana/rituals").status_code == 401


# ── /api/arcana/rituals/{id} ─────────────────────────────────────────────────

def test_arcana_ritual_detail_parses_supplies_and_structure(client):
    entry = _make_ritual(
        "rX", "Защита для Анны",
        date="2026-04-19", goal="protect", place="home", type_code="client",
        consumables="Свечи чёрные × 3 — 180\nЛадан — 95\nЧёрная соль — 150",
        structure="Очищение пространства ладаном\nКруг из чёрной соли\nЗажечь свечи",
        offerings="Монеты на перекрёсток — 7 шт",
        powers="Архангел Михаил — щит",
        time_min=45, price=5000, paid=5000, result="positive",
    )
    with patch("miniapp.backend.routes.arcana_rituals._rituals_repo",
               _mock_rituals_repo(find_by_id_result=entry)), \
         patch("miniapp.backend.routes.arcana_rituals.today_user_tz",
               AsyncMock(return_value=(_today_date(3), 3))), \
         patch("miniapp.backend.routes.arcana_rituals.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/arcana/rituals/rX")

    assert r.status_code == 200
    data = r.json()
    assert data["question"] is None
    assert data["goal"] == "🛡️ Защита"
    assert data["goals"] == ["🛡️ Защита"]
    assert data["place"] == "🏠 Дома"
    assert data["time_min"] == 45
    assert len(data["supplies"]) == 3
    assert data["supplies"][0]["qty"] == "3"
    assert data["supplies"][0]["price"] == 180
    assert data["supplies_total"] == 180 + 95 + 150
    assert len(data["structure"]) == 3
    assert data["result"] == "✅ Сработало"


def test_arcana_ritual_404_wrong_owner(client):
    # find_by_id returns None → 404 (single-user system: no result = not found)
    with patch("miniapp.backend.routes.arcana_rituals._rituals_repo",
               _mock_rituals_repo(find_by_id_result=None)), \
         patch("miniapp.backend.routes.arcana_rituals.today_user_tz",
               AsyncMock(return_value=(_today_date(3), 3))), \
         patch("miniapp.backend.routes.arcana_rituals.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/arcana/rituals/rX")
    assert r.status_code == 404


# ── /api/arcana/grimoire ─────────────────────────────────────────────────────

def test_arcana_grimoire_list_and_cat_filter(client):
    entries = [
        _grim_page("g1", "Заговор на деньги", "📿 Заговор",
                   themes=["💰 Финансы"], text="Первая строка заговора. " * 20),
        _grim_page("g2", "Рецепт масла", "🧴 Рецепт",
                   themes=["🛡️ Защита"], text="Короткий"),
    ]

    with patch("miniapp.backend.routes.arcana_grimoire._grimoire_repo",
               _mock_grimoire_repo(list_all_result=entries)), \
         patch("miniapp.backend.routes.arcana_grimoire.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r_all = client.get("/api/arcana/grimoire")
        r_q = client.get("/api/arcana/grimoire?q=рецепт")

    assert r_all.status_code == 200
    data = r_all.json()
    assert len(data["items"]) == 2
    g1 = next(i for i in data["items"] if i["id"] == "g1")
    assert g1["theme"] == "💰"
    assert g1["themes_count"] == 1
    # preview обрезается до 120 + ...
    assert g1["preview"].endswith("…")

    assert r_q.status_code == 200
    assert len(r_q.json()["items"]) == 1
    assert r_q.json()["items"][0]["id"] == "g2"


def test_arcana_grimoire_search_matches_theme(client):
    # #9: поиск гримуара должен матчить и Тему, не только Название+Текст.
    entries = [
        _grim_page("g1", "Заговор на деньги", "📿 Заговор",
                   themes=["💰 Финансы"], text="строки без нужного слова"),
        _grim_page("g2", "Рецепт масла", "🧴 Рецепт",
                   themes=["🛡️ Защита"], text="короткий"),
    ]
    with patch("miniapp.backend.routes.arcana_grimoire._grimoire_repo",
               _mock_grimoire_repo(list_all_result=entries)), \
         patch("miniapp.backend.routes.arcana_grimoire.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        # «финансы» есть только в Теме g1
        r_theme = client.get("/api/arcana/grimoire?q=финансы")
        # «защита» — только в Теме g2
        r_theme2 = client.get("/api/arcana/grimoire?q=защита")

    assert {i["id"] for i in r_theme.json()["items"]} == {"g1"}
    assert {i["id"] for i in r_theme2.json()["items"]} == {"g2"}


def test_arcana_grimoire_categories_always_returned(client):
    """Backend всегда отдаёт полный набор опций категорий (с count=0)."""
    with patch("miniapp.backend.routes.arcana_grimoire._grimoire_repo",
               _mock_grimoire_repo(list_all_result=[])), \
         patch("miniapp.backend.routes.arcana_grimoire.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/arcana/grimoire")
    assert r.status_code == 200
    cats = r.json()["categories"]
    names = [c["name"] for c in cats]
    assert "📿 Заговор" in names
    assert "🧴 Рецепт" in names
    assert "✨ Комбинация" in names
    assert "📝 Заметка" in names
    assert all(c["count"] == 0 for c in cats)


def test_arcana_grimoire_categories_counts(client):
    entries = [
        _grim_page("g1", "A", "📿 Заговор"),
        _grim_page("g2", "B", "📿 Заговор"),
        _grim_page("g3", "C", "🧴 Рецепт"),
    ]
    with patch("miniapp.backend.routes.arcana_grimoire._grimoire_repo",
               _mock_grimoire_repo(list_all_result=entries)), \
         patch("miniapp.backend.routes.arcana_grimoire.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/arcana/grimoire?cat=%F0%9F%93%BF%20%D0%97%D0%B0%D0%B3%D0%BE%D0%B2%D0%BE%D1%80")
    assert r.status_code == 200
    body = r.json()
    cats = {c["name"]: c["count"] for c in body["categories"]}
    assert cats["📿 Заговор"] == 2
    assert cats["🧴 Рецепт"] == 1
    assert cats["📝 Заметка"] == 0
    # фильтр cat применяется к items, но counts остаются глобальными
    assert len(body["items"]) == 2


def test_arcana_grimoire_detail(client):
    entry = _grim_page("gX", "Комплексная комбинация",
                       "✨ Комбинация", themes=["💰 Финансы", "🛡️ Защита"],
                       text="Полный текст записи", source="Книга Ламашту")

    with patch("miniapp.backend.routes.arcana_grimoire._grimoire_repo",
               _mock_grimoire_repo(find_by_id_result=entry)), \
         patch("miniapp.backend.routes.arcana_grimoire.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/arcana/grimoire/gX")

    assert r.status_code == 200
    data = r.json()
    assert data["themes"] == ["💰 Финансы", "🛡️ Защита"]
    assert data["content"] == "Полный текст записи"
    assert data["source"] == "Книга Ламашту"


def test_arcana_grimoire_detail_404_wrong_owner(client):
    # find_by_id возвращает None когда user_notion_id не совпадает
    with patch("miniapp.backend.routes.arcana_grimoire._grimoire_repo",
               _mock_grimoire_repo(find_by_id_result=None)), \
         patch("miniapp.backend.routes.arcana_grimoire.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/arcana/grimoire/gX")
    assert r.status_code == 404


# ── /api/arcana/stats ────────────────────────────────────────────────────────

def test_arcana_stats_computes_overall_and_months(client):
    tz = 3
    today = _today_date(tz)
    cur_month = today.strftime("%Y-%m")

    sessions = [
        _session_page("s1", "Q", date=f"{cur_month}-05", done="✅ Да"),
        _session_page("s2", "Q", date=f"{cur_month}-06", done="✅ Да"),
        _session_page("s3", "Q", date=f"{cur_month}-07", done="❌ Нет"),
        _session_page("s4", "Q", date=f"{cur_month}-08", done="⏳ Не проверено"),
    ]

    sessions_pg = [
        _make_triplet("s1", "Q", date=f"{cur_month}-05", outcome="yes"),
        _make_triplet("s2", "Q", date=f"{cur_month}-06", outcome="yes"),
        _make_triplet("s3", "Q", date=f"{cur_month}-07", outcome="no"),
        _make_triplet("s4", "Q", date=f"{cur_month}-08", outcome="unverified"),
    ]
    mock_sess = _mock_sessions_repo_all(all_result=sessions_pg)

    # /arcana/stats обслуживает arcana_today (расширенная статистика).
    with patch("miniapp.backend.routes.arcana_today._pg_sessions_repo", mock_sess), \
         patch("miniapp.backend.routes.arcana_today.rituals_all",
               AsyncMock(return_value=[])), \
         patch("miniapp.backend.routes.arcana_today.load_clients_map",
               AsyncMock(return_value={})), \
         patch("miniapp.backend.routes.arcana_today.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/arcana/stats")

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total_sessions"] == 4
    assert data["checked_triplets"] == 3  # 2 yes + 1 no
    assert data["accuracy_pct_sessions"] == round(2 / 3 * 100)
    cur = next((m for m in data["by_month"] if m["month"] == cur_month), None)
    assert cur is not None
    assert cur["sessions_total"] == 4
    assert cur["sessions_yes"] == 2
    assert cur["sessions_no"] == 1


def test_arcana_stats_401():
    app.dependency_overrides.clear()
    c = TestClient(app)
    assert c.get("/api/arcana/stats").status_code == 401


# ── POST /api/arcana/sessions/{id}/verify ───────────────────────────────────

def test_session_verify_updates_select(client):
    triplet = _make_triplet("s-1", "Q", outcome="unverified")
    mock_repo = MagicMock()
    mock_repo.find_by_id = AsyncMock(return_value=triplet)
    mock_repo.set_outcome = AsyncMock(return_value=True)
    with patch("miniapp.backend.routes.writes._sessions_pg_repo", mock_repo), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/arcana/sessions/s-1/verify", json={"status": "✅ Да"})
    assert r.status_code == 200
    mock_repo.set_outcome.assert_awaited_once_with("s-1", "yes")


def test_session_verify_rejects_unknown_status(client):
    with patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/arcana/sessions/s-1/verify", json={"status": "😀 bogus"})
    assert r.status_code == 400


# ── POST /api/arcana/rituals/{id}/result ────────────────────────────────────

def test_ritual_result_updates_select(client):
    ritual = _make_ritual("r-1", "Тест ритуал")
    mock_repo = _mock_rituals_repo(find_by_id_result=ritual)
    with patch("miniapp.backend.routes.writes._rituals_pg_repo", mock_repo), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/arcana/rituals/r-1/result",
                        json={"status": "✅ Сработало"})
    assert r.status_code == 200
    mock_repo.set_result.assert_awaited_once_with("r-1", "✅ Сработало")


# ── POST /api/arcana/clients (create/edit) ──────────────────────────────────

def test_arcana_client_create(client):
    mock_cr = _mock_clients_repo(add_result="7")
    with patch("miniapp.backend.routes.writes._clients_repo", mock_cr), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/arcana/clients", json={
            "name": "Анна",
            "contact": "@anna_tarot",
            "request": "Отношения",
            "status": "🟢 Активный",
        })
    assert r.status_code == 200
    assert r.json() == {"ok": True, "id": "7"}
    kwargs = mock_cr.add.await_args.kwargs
    assert kwargs["name"] == "Анна"
    assert kwargs["contact"] == "@anna_tarot"


def test_arcana_client_create_with_type_and_notes(client):
    mock_cr = _mock_clients_repo(add_result="8")
    with patch("miniapp.backend.routes.writes._clients_repo", mock_cr), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/arcana/clients", json={
            "name": "Лиза",
            "type": "🎁 Бесплатный",
            "notes": "первый бесплатный сеанс",
        })
    assert r.status_code == 200
    assert mock_cr.add.await_args.kwargs["client_type"] == "🎁 Бесплатный"
    mock_cr.update_profile.assert_awaited_once()
    assert mock_cr.update_profile.await_args.kwargs["notes"] == "первый бесплатный сеанс"


def test_arcana_client_edit_updates_fields(client):
    c_obj = _make_client("3", "Клиент", type_code="paid")
    mock_cr = _mock_clients_repo(find_by_id_result=c_obj)
    with patch("miniapp.backend.routes.writes._clients_repo", mock_cr), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/arcana/clients/3/edit", json={
            "notes": "новая заметка",
            "request": "карьера",
            "type": "🎁 Бесплатный",
        })
    assert r.status_code == 200
    kwargs = mock_cr.update_profile.await_args.kwargs
    assert kwargs["notes"] == "новая заметка"
    assert kwargs["request"] == "карьера"
    assert kwargs["type_code"] == "free"


def test_arcana_client_edit_self_blocks_type(client):
    c_obj = _make_client("99", "Self", type_code="self")
    mock_cr = _mock_clients_repo(find_by_id_result=c_obj)
    with patch("miniapp.backend.routes.writes._clients_repo", mock_cr), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/arcana/clients/99/edit", json={
            "notes": "ok",
            "type": "🤝 Платный",
        })
    assert r.status_code == 200
    kwargs = mock_cr.update_profile.await_args.kwargs
    assert kwargs.get("type_code") is None  # self — тип менять нельзя
    assert kwargs["notes"] == "ok"


def test_client_create_with_birthday(client):
    mock_cr = _mock_clients_repo(add_result="10")
    with patch("miniapp.backend.routes.writes._clients_repo", mock_cr), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/arcana/clients", json={
            "name": "Аня",
            "birthday": "2000-10-02",
        })
    assert r.status_code == 200
    mock_cr.add.assert_awaited_once()
    mock_cr.update_profile.assert_awaited_once()
    assert mock_cr.update_profile.await_args.kwargs["birthday"] == "2000-10-02"


# ── POST /api/arcana/sessions/{id}/photo ────────────────────────────────────

def test_session_photo_upload_writes_url(client):
    triplet = _make_triplet("sess-1", "Q")
    mock_repo = MagicMock()
    mock_repo.find_by_id = AsyncMock(return_value=triplet)
    mock_repo.set_photo_url = AsyncMock(return_value=True)
    fake_url = "https://res.cloudinary.com/x/y.jpg"
    with patch("miniapp.backend.routes.writes._sessions_pg_repo", mock_repo), \
         patch("miniapp.backend.routes.writes._cloudinary_upload",
               AsyncMock(return_value=fake_url)) as cu, \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post(
            "/api/arcana/sessions/sess-1/photo",
            files={"file": ("card.jpg", b"FAKEJPG", "image/jpeg")},
        )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "url": fake_url}
    cu.assert_awaited_once()
    mock_repo.set_photo_url.assert_awaited_once_with("sess-1", fake_url)


def test_session_photo_upload_rejects_non_image(client):
    triplet = _make_triplet("sess-2", "Q")
    mock_repo = MagicMock()
    mock_repo.find_by_id = AsyncMock(return_value=triplet)
    with patch("miniapp.backend.routes.writes._sessions_pg_repo", mock_repo), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post(
            "/api/arcana/sessions/sess-2/photo",
            files={"file": ("note.txt", b"hello", "text/plain")},
        )
    assert r.status_code == 415


# ── POST /api/arcana/rituals/{id}/photo ─────────────────────────────────────

def test_ritual_photo_upload_writes_url(client):
    ritual = _make_ritual("rit-1", "Ритуал")
    fake_url = "https://res.cloudinary.com/x/r.jpg"
    mock_repo = _mock_rituals_repo(find_by_id_result=ritual)
    with patch("miniapp.backend.routes.writes._rituals_pg_repo", mock_repo), \
         patch("miniapp.backend.routes.writes._cloudinary_upload_folder",
               AsyncMock(return_value=fake_url)) as cu, \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post(
            "/api/arcana/rituals/rit-1/photo",
            files={"file": ("ritual.jpg", b"FAKE", "image/jpeg")},
        )
    assert r.status_code == 200
    cu.assert_awaited_once()
    assert cu.await_args.args[2] == "arcana-rituals"
    mock_repo.update_photo_url.assert_awaited_once_with("rit-1", fake_url)


# ── /api/arcana/clients/{id}/object_photo ───────────────────────────────────

def test_client_object_photo_appends_url(client):
    c_obj = _make_client("7", "Клиент", object_photos="https://old.example/a.jpg")
    fake_url = "https://res.cloudinary.com/x/o.jpg"
    mock_cr = _mock_clients_repo(find_by_id_result=c_obj)
    with patch("miniapp.backend.routes.writes._clients_repo", mock_cr), \
         patch("miniapp.backend.routes.writes._cloudinary_upload_folder",
               AsyncMock(return_value=fake_url)) as cu, \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post(
            "/api/arcana/clients/7/object_photo",
            files={"file": ("obj.jpg", b"FAKE", "image/jpeg")},
            data={"note": "Игорь, начальник, ДР 5 марта"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["url"] == fake_url
    assert body["note"] == "Игорь, начальник, ДР 5 марта"
    urls = [p["url"] for p in body["photos"]]
    assert "https://old.example/a.jpg" in urls
    assert fake_url in urls
    cu.assert_awaited_once()
    assert cu.await_args.args[2] == "arcana-client-objects"
    # update_object_photos called with new serialized string
    mock_cr.update_object_photos.assert_awaited_once()
    saved_raw = mock_cr.update_object_photos.await_args.args[1]
    assert f"{fake_url} | Игорь" in saved_raw


def test_client_object_photo_edit_note(client):
    c_obj = _make_client("9", "Клиент",
                         object_photos="https://e/1.jpg | старая\nhttps://e/2.jpg")
    mock_cr = _mock_clients_repo(find_by_id_result=c_obj)
    with patch("miniapp.backend.routes.writes._clients_repo", mock_cr), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.patch(
            "/api/arcana/clients/9/object_photo/1",
            json={"note": "мама"},
        )
    assert r.status_code == 200
    photos = r.json()["photos"]
    assert photos[0]["note"] == "старая"
    assert photos[1]["note"] == "мама"


def test_client_object_photo_delete(client):
    c_obj = _make_client("11", "Клиент",
                         object_photos="https://e/1.jpg | a\nhttps://e/2.jpg | b\nhttps://e/3.jpg | c")
    mock_cr = _mock_clients_repo(find_by_id_result=c_obj)
    with patch("miniapp.backend.routes.writes._clients_repo", mock_cr), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.delete("/api/arcana/clients/11/object_photo/1")
    assert r.status_code == 200
    photos = r.json()["photos"]
    assert len(photos) == 2
    assert [p["note"] for p in photos] == ["a", "c"]


def test_client_object_photo_index_404(client):
    page = _page("cli-z", extra={
        "Фото объектов": {"rich_text": [{"plain_text": "https://e/1.jpg"}]},
    })
    with patch("miniapp.backend.routes.writes.get_page", AsyncMock(return_value=page)), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.delete("/api/arcana/clients/cli-z/object_photo/99")
    assert r.status_code == 404


# ── POST /api/arcana/sessions/{id}/summarize ────────────────────────────────

def test_summarize_returns_cached_when_ai_summary_exists(client):
    """Если у сеанса уже есть triplet_summary — возвращаем без вызова Claude."""
    triplet = _make_triplet("s1", "Q",
                            triplet_summary="Короткая суть уже была.",
                            interpretation="<b>Долгая трактовка...</b>")
    mock_repo = MagicMock()
    mock_repo.find_by_id = AsyncMock(return_value=triplet)
    mock_repo.update_summary = AsyncMock(return_value=None)
    claude_mock = AsyncMock(return_value="НЕ должен вызываться")

    with patch("miniapp.backend.routes.writes._sessions_pg_repo", mock_repo), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)), \
         patch("core.claude_client.ask_claude", claude_mock):
        r = client.post("/api/arcana/sessions/s1/summarize")

    assert r.status_code == 200
    data = r.json()
    assert data["cached"] is True
    assert data["summary"] == "Короткая суть уже была."
    assert claude_mock.await_count == 0


def test_summarize_generates_when_empty(client):
    triplet = _make_triplet("s2", "Q",
                            interpretation="Очень длинная трактовка про шута и дорогу")
    mock_repo = MagicMock()
    mock_repo.find_by_id = AsyncMock(return_value=triplet)
    mock_repo.update_summary = AsyncMock(return_value=None)
    claude_mock = AsyncMock(return_value="Вывод: путь начинается сегодня.")

    with patch("miniapp.backend.routes.writes._sessions_pg_repo", mock_repo), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)), \
         patch("core.claude_client.ask_claude", claude_mock):
        r = client.post("/api/arcana/sessions/s2/summarize")

    assert r.status_code == 200
    data = r.json()
    assert data["cached"] is False
    assert data["summary"] == "Вывод: путь начинается сегодня."
    assert claude_mock.await_count == 1


# ── /api/arcana/moon-phases ──────────────────────────────────────────────────

def test_moon_phases_endpoint_returns_upcoming(client):
    r = client.get("/api/arcana/moon-phases?count=4")
    assert r.status_code == 200
    data = r.json()
    assert "current" in data and "upcoming" in data
    assert len(data["upcoming"]) == 4
    # Каждая фаза имеет idx из крупных, дату и глиф
    for p in data["upcoming"]:
        assert p["idx"] in (0, 2, 4, 6)
        assert p["glyph"] in {"🌑", "🌓", "🌕", "🌗"}
        assert len(p["date"]) == 10  # YYYY-MM-DD


def test_moon_next_phases_chronological():
    """Фазы возвращаются в хронологическом порядке."""
    from miniapp.backend._moon import next_phases
    start = datetime(2026, 4, 22, 0, 0, tzinfo=timezone.utc)
    phases = next_phases(count=6, start=start)
    dates = [p["date"] for p in phases]
    assert dates == sorted(dates)
    # Все в будущем относительно start
    for p in phases:
        assert p["date"] >= "2026-04-22"


# ── tarot.py — deck registry, card matcher, canonical_card ──────────────────

def test_tarot_find_card_exact_en():
    from miniapp.backend.tarot import find_card
    c = find_card("rider-waite", "The Fool")
    assert c is not None
    assert c["en"] == "The Fool"
    assert c["ru"] == "Шут"
    assert c["file"] == "00_fool.jpg"


def test_tarot_find_card_exact_ru():
    from miniapp.backend.tarot import find_card
    c = find_card("rider-waite", "Жрица")
    assert c is not None
    assert c["en"] == "The High Priestess"


def test_tarot_find_card_alias():
    from miniapp.backend.tarot import find_card
    c = find_card("rider-waite", "волшебник")
    assert c is not None
    assert c["en"] == "The Magician"


def test_tarot_find_card_case_insensitive():
    from miniapp.backend.tarot import find_card
    c = find_card("rider-waite", "ИЕРОФАНТ")
    assert c is not None
    assert c["en"] == "The Hierophant"


def test_tarot_canonical_card_matched():
    from miniapp.backend.tarot import canonical_card
    c = canonical_card("rider-waite", "Шут")
    assert c["matched"] is True
    assert c["en"] == "The Fool"
    assert c["file"] == "00_fool.jpg"
    assert c["deck_id"] == "rider-waite"


def test_tarot_canonical_card_not_matched():
    from miniapp.backend.tarot import canonical_card
    c = canonical_card("rider-waite", "несуществующая карта")
    assert c["matched"] is False
    assert c["raw"] == "несуществующая карта"
    assert c["deck_id"] == "rider-waite"


def test_tarot_parse_cards_raw_comma():
    from miniapp.backend.tarot import parse_cards_raw
    cards = parse_cards_raw("Шут, Маг, Жрица", "rider-waite")
    assert len(cards) == 3
    assert cards[0]["en"] == "The Fool"
    assert cards[1]["en"] == "The Magician"
    assert cards[2]["en"] == "The High Priestess"


def test_tarot_resolve_deck_id():
    from miniapp.backend.tarot import resolve_deck_id
    assert resolve_deck_id("Таро Уэйта") == "rider-waite"
    assert resolve_deck_id("Rider-Waite") == "rider-waite"
    assert resolve_deck_id("Ленорман") == "lenormand"
    assert resolve_deck_id(None) == "rider-waite"
    assert resolve_deck_id("") == "rider-waite"
    # fallback
    assert resolve_deck_id("какая-то неизвестная колода") == "rider-waite"
