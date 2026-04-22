"""Wave 2b tests — Arcana read endpoints.

/api/arcana/today, /sessions, /sessions/{id}, /clients, /clients/{id},
/rituals, /rituals/{id}, /grimoire, /grimoire/{id}, /stats.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

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


# ── Page builders ───────────────────────────────────────────────────────────

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
    return {
        "id": gid,
        "properties": {
            "Название": {"title": [{"plain_text": name}]},
            "Категория": {"select": {"name": cat}},
            "Тема": {"multi_select": [{"name": t} for t in (themes or [])]},
            "Текст": {"rich_text": [{"plain_text": text}] if text else []},
            "Источник": {"rich_text": [{"plain_text": source}] if source else []},
            "🪪 Пользователи": {"relation": [{"id": FAKE_NOTION_USER}]},
        },
    }


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


# ── /api/arcana/today ───────────────────────────────────────────────────────

def test_arcana_today_happy(client):
    tz = 3
    today = _today_date(tz)
    today_iso = today.isoformat()

    clients_pages = [_client_page("c1", "Анна")]
    sessions = [
        _session_page("s_today", "Что думает Вадим",
                      date=f"{today_iso}T14:00:00+03:00",
                      client_ids=["c1"], area=["Отношения"],
                      spread="🗝️ Кельтский крест"),
        # Старый непроверенный для unchecked_30d
        _session_page(
            "s_old_pending", "Стар Q",
            date=(today - timedelta(days=60)).isoformat(),
            client_ids=["c1"], done="⏳ Не проверено",
        ),
        # Сбылся для accuracy
        _session_page("s_yes", "Сбылось",
                      date=(today - timedelta(days=5)).isoformat(),
                      client_ids=["c1"], done="✅ Да"),
    ]
    works = [_work_page("w1", "Свечи", cat="🕯️ Расходники", deadline=today_iso)]
    # Финансы за месяц: доход 10000, supplies 500
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

    with patch("miniapp.backend.routes.arcana_today.sessions_all",
               AsyncMock(return_value=sessions)), \
         patch("miniapp.backend.routes._arcana_common.arcana_clients_summary",
               AsyncMock(return_value=clients_pages)), \
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


# ── /api/arcana/sessions ────────────────────────────────────────────────────

def test_arcana_sessions_list_and_filter(client):
    tz = 3
    today = _today_date(tz)
    sessions = [
        _session_page("s1", "Вопрос A", date=today.isoformat(),
                      client_ids=["c1"], area=["Отношения"],
                      cards="Шут, Маг, Жрица"),
        _session_page("s2", "Вопрос B", date=today.isoformat(),
                      client_ids=["c2"], area=["Финансы"]),
    ]
    clients_pages = [_client_page("c1", "Анна"), _client_page("c2", "Борис")]

    with patch("miniapp.backend.routes.arcana_sessions.sessions_all",
               AsyncMock(return_value=sessions)), \
         patch("miniapp.backend.routes._arcana_common.arcana_clients_summary",
               AsyncMock(return_value=clients_pages)), \
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
    assert first["cards_brief"] == ["Шут", "Маг", "Жрица"]
    assert first["client"] == "Анна"

    assert r_area.json()["total"] == 1
    assert r_area.json()["sessions"][0]["id"] == "s1"
    assert r_client.json()["total"] == 1
    assert r_client.json()["sessions"][0]["client"] == "Борис"


def test_arcana_sessions_401():
    app.dependency_overrides.clear()
    c = TestClient(app)
    assert c.get("/api/arcana/sessions").status_code == 401


# ── /api/arcana/sessions/{id} ──────────────────────────────────────────────

def test_arcana_session_detail_parses_cards_and_bottom(client):
    tz = 3
    today = _today_date(tz)
    page = _session_page(
        "sX", "Планы Вадима",
        date=today.isoformat(),
        client_ids=["c1"],
        area=["Отношения"], deck="Dark Wood Tarot",
        spread="🗝️ Кельтский крест",
        cards="1. Суть — Туз Мечей\n2. Препятствие — Башня\n3. Совет — Звезда",
        interp="Расклад указывает на...\n🂠 Дно: Король Кубков",
        done="⏳ Не проверено", price=3000, paid=0,
    )

    with patch("miniapp.backend.routes.arcana_sessions.get_page",
               AsyncMock(return_value=page)), \
         patch("miniapp.backend.routes._arcana_common.arcana_clients_summary",
               AsyncMock(return_value=[_client_page("c1", "Кай")])), \
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
    # interpretation очищен от строки "🂠 Дно"
    assert "Дно" not in (data["interpretation"] or "")
    assert "Расклад указывает" in data["interpretation"]
    assert len(data["cards"]) >= 3
    assert data["cards"][0]["pos"] is None
    assert data["cards_raw"] is not None


def test_arcana_session_detail_404_when_wrong_owner(client):
    page = {
        "id": "sX",
        "properties": {
            "Тема": {"title": []},
            "🪪 Пользователи": {"relation": [{"id": "somebody-else"}]},
        },
    }
    with patch("miniapp.backend.routes.arcana_sessions.get_page",
               AsyncMock(return_value=page)), \
         patch("miniapp.backend.routes._arcana_common.arcana_clients_summary",
               AsyncMock(return_value=[])), \
         patch("miniapp.backend.routes.arcana_sessions.today_user_tz",
               AsyncMock(return_value=(_today_date(3), 3))), \
         patch("miniapp.backend.routes.arcana_sessions.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/arcana/sessions/sX")
    assert r.status_code == 404


# ── /api/arcana/clients ─────────────────────────────────────────────────────

def test_arcana_clients_list_aggregates_stats(client):
    clients_pages = [_client_page("c1", "Анна"), _client_page("c2", "Борис")]
    sessions = [
        _session_page("s1", "Q", date="2026-04-05", client_ids=["c1"],
                      price=3000, paid=0),  # debt 3000
        _session_page("s2", "Q", date="2026-04-06", client_ids=["c1"],
                      price=3000, paid=3000),  # paid
    ]
    rituals = [
        _ritual_page("r1", "Защита", date="2026-03-18", client_ids=["c1"],
                     price=5000, paid=5000),
    ]

    with patch("miniapp.backend.routes.arcana_clients.arcana_clients_summary",
               AsyncMock(return_value=clients_pages)), \
         patch("miniapp.backend.routes.arcana_clients.sessions_all",
               AsyncMock(return_value=sessions)), \
         patch("miniapp.backend.routes.arcana_clients.rituals_all",
               AsyncMock(return_value=rituals)), \
         patch("miniapp.backend.routes.arcana_clients.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/arcana/clients")

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"] == 2
    assert data["total_debt"] == 3000
    anna = next(c for c in data["clients"] if c["id"] == "c1")
    assert anna["sessions_count"] == 2
    assert anna["rituals_count"] == 1
    assert anna["debt"] == 3000
    assert anna["total_paid"] == 3000 + 5000
    assert anna["initial"] == "А"


def test_arcana_clients_401():
    app.dependency_overrides.clear()
    c = TestClient(app)
    assert c.get("/api/arcana/clients").status_code == 401


# ── /api/arcana/clients/{id} ────────────────────────────────────────────────

def test_arcana_client_dossier_mixes_history(client):
    cp = _client_page("c1", "Анна", contact="@anna", request="Отношения")
    sessions = [
        _session_page("s1", "Кельтский крест — отношения",
                      date="2026-04-05", client_ids=["c1"],
                      price=3000, paid=0),
    ]
    rituals = [
        _ritual_page("r1", "Ритуал защиты",
                     date="2026-03-18", client_ids=["c1"],
                     price=5000, paid=5000, result="✅ Сработало"),
    ]

    with patch("miniapp.backend.routes.arcana_clients.get_page",
               AsyncMock(return_value=cp)), \
         patch("miniapp.backend.routes.arcana_clients.sessions_all",
               AsyncMock(return_value=sessions)), \
         patch("miniapp.backend.routes.arcana_clients.rituals_all",
               AsyncMock(return_value=rituals)), \
         patch("miniapp.backend.routes.arcana_clients.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/arcana/clients/c1")

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


def test_arcana_client_dossier_404_wrong_owner(client):
    page = {
        "id": "c1",
        "properties": {
            "Имя": {"title": [{"plain_text": "X"}]},
            "🪪 Пользователи": {"relation": [{"id": "somebody-else"}]},
        },
    }
    with patch("miniapp.backend.routes.arcana_clients.get_page",
               AsyncMock(return_value=page)), \
         patch("miniapp.backend.routes.arcana_clients.sessions_all",
               AsyncMock(return_value=[])), \
         patch("miniapp.backend.routes.arcana_clients.rituals_all",
               AsyncMock(return_value=[])), \
         patch("miniapp.backend.routes.arcana_clients.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/arcana/clients/c1")
    assert r.status_code == 404


# ── /api/arcana/rituals ─────────────────────────────────────────────────────

def test_arcana_rituals_list_and_filter_by_goal(client):
    rituals = [
        _ritual_page("r1", "Защита", goal="🛡️ Защита", client_ids=["c1"]),
        _ritual_page("r2", "Очищение", goal="🌊 Очищение"),
        _ritual_page("r3", "Защита для Бориса", goal_multi=["🛡️ Защита"],
                     client_ids=["c2"]),
    ]
    clients_pages = [_client_page("c1", "Анна"), _client_page("c2", "Борис")]

    with patch("miniapp.backend.routes.arcana_rituals.rituals_all",
               AsyncMock(return_value=rituals)), \
         patch("miniapp.backend.routes._arcana_common.arcana_clients_summary",
               AsyncMock(return_value=clients_pages)), \
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


# ── /api/arcana/rituals/{id} ────────────────────────────────────────────────

def test_arcana_ritual_detail_parses_supplies_and_structure(client):
    page = _ritual_page(
        "rX", "Защита для Анны",
        date="2026-04-19", client_ids=["c1"], goal="🛡️ Защита",
        place="🏠 Дома", ritual_type="🤝 Клиентский",
        consumables="Свечи чёрные × 3 — 180\nЛадан — 95\nЧёрная соль — 150",
        structure="Очищение пространства ладаном\nКруг из чёрной соли\nЗажечь свечи",
        offerings="Монеты на перекрёсток — 7 шт",
        powers="Архангел Михаил — щит",
        duration=45, price=5000, paid=5000, result="✅ Сработало",
    )

    with patch("miniapp.backend.routes.arcana_rituals.get_page",
               AsyncMock(return_value=page)), \
         patch("miniapp.backend.routes._arcana_common.arcana_clients_summary",
               AsyncMock(return_value=[_client_page("c1", "Анна")])), \
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
    page = {"id": "rX", "properties": {
        "Название": {"title": []},
        "🪪 Пользователи": {"relation": [{"id": "other"}]},
    }}
    with patch("miniapp.backend.routes.arcana_rituals.get_page",
               AsyncMock(return_value=page)), \
         patch("miniapp.backend.routes._arcana_common.arcana_clients_summary",
               AsyncMock(return_value=[])), \
         patch("miniapp.backend.routes.arcana_rituals.today_user_tz",
               AsyncMock(return_value=(_today_date(3), 3))), \
         patch("miniapp.backend.routes.arcana_rituals.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/arcana/rituals/rX")
    assert r.status_code == 404


# ── /api/arcana/grimoire ────────────────────────────────────────────────────

def test_arcana_grimoire_list_and_cat_filter(client):
    pages = [
        _grim_page("g1", "Заговор на деньги", "📿 Заговор",
                   themes=["💰 Финансы"], text="Первая строка заговора. " * 20),
        _grim_page("g2", "Рецепт масла", "🧴 Рецепт",
                   themes=["🛡️ Защита"], text="Короткий"),
    ]

    with patch("miniapp.backend.routes.arcana_grimoire.query_pages",
               AsyncMock(return_value=pages)), \
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


def test_arcana_grimoire_detail(client):
    page = _grim_page("gX", "Комплексная комбинация",
                     "✨ Комбинация", themes=["💰 Финансы", "🛡️ Защита"],
                     text="Полный текст записи", source="Книга Ламашту")

    with patch("miniapp.backend.routes.arcana_grimoire.get_page",
               AsyncMock(return_value=page)), \
         patch("miniapp.backend.routes.arcana_grimoire.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/arcana/grimoire/gX")

    assert r.status_code == 200
    data = r.json()
    assert data["themes"] == ["💰 Финансы", "🛡️ Защита"]
    assert data["content"] == "Полный текст записи"
    assert data["source"] == "Книга Ламашту"


def test_arcana_grimoire_detail_404_wrong_owner(client):
    page = {"id": "gX", "properties": {
        "Название": {"title": []},
        "🪪 Пользователи": {"relation": [{"id": "other"}]},
    }}
    with patch("miniapp.backend.routes.arcana_grimoire.get_page",
               AsyncMock(return_value=page)), \
         patch("miniapp.backend.routes.arcana_grimoire.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/arcana/grimoire/gX")
    assert r.status_code == 404


# ── /api/arcana/stats ───────────────────────────────────────────────────────

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

    with patch("miniapp.backend.routes.arcana_stats.sessions_all",
               AsyncMock(return_value=sessions)), \
         patch("miniapp.backend.routes._arcana_common.query_pages",
               AsyncMock(return_value=[])), \
         patch("miniapp.backend.routes.arcana_stats.today_user_tz",
               AsyncMock(return_value=(today, tz))), \
         patch("miniapp.backend.routes.arcana_stats.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/arcana/stats")

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["verified_total"] == 3  # 2 yes + 1 no
    assert data["accuracy_overall"] == round(2 / 3 * 100)
    # Текущий месяц первым
    cur = data["months"][0]
    assert cur["month"] == cur_month
    assert cur["total"] == 4
    assert cur["yes"] == 2
    assert cur["pending"] == 1
    assert cur["pct"] == round(2 / 3 * 100)
    assert len(data["months"]) == 6
    assert "practice_finance" in data


def test_arcana_stats_401():
    app.dependency_overrides.clear()
    c = TestClient(app)
    assert c.get("/api/arcana/stats").status_code == 401
