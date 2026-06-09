"""Mini App — Arcana: расклады, клиенты, ритуалы, гримуар, статистика, таро.

GET /api/arcana/{today,sessions,clients,rituals,grimoire,stats,moon-phases},
POST verify/result/clients/photo/object_photo/summarize, tarot helpers.

Собрано из wave2b / wave3 / wave5 / wave6 при реорганизации тестов по доменам.
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


# ── /api/arcana/sessions ─────────────────────────────────────────────────────

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
    # API список отдаёт мета-поля группы, без карт; карты — в detail-эндпоинте.
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
    # wave6.4: cards теперь canonical с en/ru/file/matched
    assert "en" in data["cards"][0] or "raw" in data["cards"][0]
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


# ── /api/arcana/clients ──────────────────────────────────────────────────────

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


# ── /api/arcana/clients/{id} ─────────────────────────────────────────────────

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


# ── /api/arcana/rituals ──────────────────────────────────────────────────────

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


# ── /api/arcana/rituals/{id} ─────────────────────────────────────────────────

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


# ── /api/arcana/grimoire ─────────────────────────────────────────────────────

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


def test_arcana_grimoire_search_matches_theme(client):
    # #9: поиск гримуара должен матчить и Тему, не только Название+Текст.
    pages = [
        _grim_page("g1", "Заговор на деньги", "📿 Заговор",
                   themes=["💰 Финансы"], text="строки без нужного слова"),
        _grim_page("g2", "Рецепт масла", "🧴 Рецепт",
                   themes=["🛡️ Защита"], text="короткий"),
    ]
    with patch("miniapp.backend.routes.arcana_grimoire.query_pages",
               AsyncMock(return_value=pages)), \
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
    with patch("miniapp.backend.routes.arcana_grimoire.query_pages",
               AsyncMock(return_value=[])), \
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
    pages = [
        _grim_page("g1", "A", "📿 Заговор"),
        _grim_page("g2", "B", "📿 Заговор"),
        _grim_page("g3", "C", "🧴 Рецепт"),
    ]
    with patch("miniapp.backend.routes.arcana_grimoire.query_pages",
               AsyncMock(return_value=pages)), \
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

    # /arcana/stats теперь обслуживает arcana_today (расширенная статистика
    # с разделением sessions/rituals). Старый узкий arcana_stats роут
    # перекрыт более широким, тест проверяет реальный shape.
    with patch("miniapp.backend.routes.arcana_today.sessions_all",
               AsyncMock(return_value=sessions)), \
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
    page = _page("s-1")
    with patch("miniapp.backend.routes.writes.get_page", AsyncMock(return_value=page)), \
         patch("miniapp.backend.routes.writes.update_page_select",
               AsyncMock(return_value=True)) as ups, \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/arcana/sessions/s-1/verify", json={"status": "✅ Да"})
    assert r.status_code == 200
    ups.assert_awaited_once_with("s-1", "Сбылось", "✅ Да")


def test_session_verify_rejects_unknown_status(client):
    with patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/arcana/sessions/s-1/verify", json={"status": "😀 bogus"})
    assert r.status_code == 400


# ── POST /api/arcana/rituals/{id}/result ────────────────────────────────────

def test_ritual_result_updates_select(client):
    page = _page("r-1")
    with patch("miniapp.backend.routes.writes.get_page", AsyncMock(return_value=page)), \
         patch("miniapp.backend.routes.writes.update_page_select",
               AsyncMock(return_value=True)) as ups, \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/arcana/rituals/r-1/result",
                        json={"status": "✅ Сработало"})
    assert r.status_code == 200
    ups.assert_awaited_once_with("r-1", "Результат", "✅ Сработало")


# ── POST /api/arcana/clients (create/edit) ──────────────────────────────────

def test_arcana_client_create(client):
    with patch("miniapp.backend.routes.writes.client_add",
               AsyncMock(return_value="cli-id")) as ca, \
         patch("miniapp.backend.routes.writes.update_page_select",
               AsyncMock(return_value=True)), \
         patch("miniapp.backend.routes.writes.update_page",
               AsyncMock(return_value=None)), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/arcana/clients", json={
            "name": "Анна",
            "contact": "@anna_tarot",
            "request": "Отношения",
            "status": "🟢 Активный",
        })
    assert r.status_code == 200
    assert r.json() == {"ok": True, "id": "cli-id"}
    kwargs = ca.await_args.kwargs
    assert kwargs["name"] == "Анна"
    assert kwargs["contact"] == "@anna_tarot"


def test_arcana_client_create_with_type_and_notes(client):
    with patch("miniapp.backend.routes.writes.client_add",
               AsyncMock(return_value="cli-2")) as ca, \
         patch("miniapp.backend.routes.writes.update_page_select",
               AsyncMock(return_value=True)), \
         patch("miniapp.backend.routes.writes.update_page",
               AsyncMock(return_value=None)) as up, \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/arcana/clients", json={
            "name": "Лиза",
            "type": "🎁 Бесплатный",
            "notes": "первый бесплатный сеанс",
        })
    assert r.status_code == 200
    assert ca.await_args.kwargs["client_type"] == "🎁 Бесплатный"
    # update_page called with notes
    notes_call = [c for c in up.await_args_list if "Заметки" in c.args[1]]
    assert notes_call, "ожидался update_page для Заметки"


def test_arcana_client_edit_updates_fields(client):
    page = _page("cli-3", extra={"Тип клиента": {"select": {"name": "🤝 Платный"}}})
    with patch("miniapp.backend.routes.writes.get_page", AsyncMock(return_value=page)), \
         patch("miniapp.backend.routes.writes.update_page",
               AsyncMock(return_value=None)) as up, \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/arcana/clients/cli-3/edit", json={
            "notes": "новая заметка",
            "request": "карьера",
            "type": "🎁 Бесплатный",
        })
    assert r.status_code == 200
    props = up.await_args.args[1]
    assert "Заметки" in props and "Запрос" in props and "Тип клиента" in props


def test_arcana_client_edit_self_blocks_type(client):
    page = _page("cli-self", extra={"Тип клиента": {"select": {"name": "🌟 Self"}}})
    with patch("miniapp.backend.routes.writes.get_page", AsyncMock(return_value=page)), \
         patch("miniapp.backend.routes.writes.update_page",
               AsyncMock(return_value=None)) as up, \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/arcana/clients/cli-self/edit", json={
            "notes": "ok",
            "type": "🤝 Платный",
        })
    assert r.status_code == 200
    props = up.await_args.args[1]
    assert "Тип клиента" not in props
    assert "Заметки" in props


def test_client_create_with_birthday(client):
    with patch("miniapp.backend.routes.writes.client_add",
               AsyncMock(return_value="cli-bday")) as ca, \
         patch("miniapp.backend.routes.writes.update_page_select",
               AsyncMock(return_value=True)), \
         patch("miniapp.backend.routes.writes.update_page",
               AsyncMock(return_value=None)) as up, \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/arcana/clients", json={
            "name": "Аня",
            "birthday": "2000-10-02",
        })
    assert r.status_code == 200
    ca.assert_awaited_once()
    bday_call = [c for c in up.await_args_list if "День рождения" in c.args[1]]
    assert bday_call


# ── POST /api/arcana/sessions/{id}/photo ────────────────────────────────────

def test_session_photo_upload_writes_url(client):
    page = _page("sess-1")
    fake_url = "https://res.cloudinary.com/x/y.jpg"
    with patch("miniapp.backend.routes.writes.get_page", AsyncMock(return_value=page)), \
         patch("miniapp.backend.routes.writes._cloudinary_upload",
               AsyncMock(return_value=fake_url)) as cu, \
         patch("miniapp.backend.routes.writes.update_page",
               AsyncMock(return_value=None)) as up, \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post(
            "/api/arcana/sessions/sess-1/photo",
            files={"file": ("card.jpg", b"FAKEJPG", "image/jpeg")},
        )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "url": fake_url}
    cu.assert_awaited_once()
    props = up.await_args.args[1]
    assert props == {"Фото": {"url": fake_url}}


def test_session_photo_upload_rejects_non_image(client):
    page = _page("sess-2")
    with patch("miniapp.backend.routes.writes.get_page", AsyncMock(return_value=page)), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post(
            "/api/arcana/sessions/sess-2/photo",
            files={"file": ("note.txt", b"hello", "text/plain")},
        )
    assert r.status_code == 415


# ── POST /api/arcana/rituals/{id}/photo ─────────────────────────────────────

def test_ritual_photo_upload_writes_url(client):
    page = _page("rit-1")
    fake_url = "https://res.cloudinary.com/x/r.jpg"
    with patch("miniapp.backend.routes.writes.get_page", AsyncMock(return_value=page)), \
         patch("miniapp.backend.routes.writes._cloudinary_upload_folder",
               AsyncMock(return_value=fake_url)) as cu, \
         patch("miniapp.backend.routes.writes.update_page",
               AsyncMock(return_value=None)) as up, \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post(
            "/api/arcana/rituals/rit-1/photo",
            files={"file": ("ritual.jpg", b"FAKE", "image/jpeg")},
        )
    assert r.status_code == 200
    cu.assert_awaited_once()
    assert cu.await_args.args[2] == "arcana-rituals"
    up.assert_awaited_once()
    assert up.await_args.args[1] == {"Фото": {"url": fake_url}}


# ── /api/arcana/clients/{id}/object_photo ───────────────────────────────────

def test_client_object_photo_appends_url(client):
    page = _page("cli-7", extra={
        "Фото объектов": {"rich_text": [{"plain_text": "https://old.example/a.jpg"}]},
    })
    fake_url = "https://res.cloudinary.com/x/o.jpg"
    with patch("miniapp.backend.routes.writes.get_page", AsyncMock(return_value=page)), \
         patch("miniapp.backend.routes.writes._cloudinary_upload_folder",
               AsyncMock(return_value=fake_url)) as cu, \
         patch("miniapp.backend.routes.writes.update_page",
               AsyncMock(return_value=None)) as up, \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post(
            "/api/arcana/clients/cli-7/object_photo",
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
    written = up.await_args.args[1]["Фото объектов"]
    serialized = "".join(rt.get("text", {}).get("content") or "" for rt in written["rich_text"])
    assert f"{fake_url} | Игорь" in serialized


def test_client_object_photo_edit_note(client):
    page = _page("cli-9", extra={
        "Фото объектов": {"rich_text": [{"plain_text": "https://e/1.jpg | старая\nhttps://e/2.jpg"}]},
    })
    with patch("miniapp.backend.routes.writes.get_page", AsyncMock(return_value=page)), \
         patch("miniapp.backend.routes.writes.update_page",
               AsyncMock(return_value=None)) as up, \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.patch(
            "/api/arcana/clients/cli-9/object_photo/1",
            json={"note": "мама"},
        )
    assert r.status_code == 200
    photos = r.json()["photos"]
    assert photos[0]["note"] == "старая"
    assert photos[1]["note"] == "мама"


def test_client_object_photo_delete(client):
    page = _page("cli-d", extra={
        "Фото объектов": {"rich_text": [{"plain_text": "https://e/1.jpg | a\nhttps://e/2.jpg | b\nhttps://e/3.jpg | c"}]},
    })
    with patch("miniapp.backend.routes.writes.get_page", AsyncMock(return_value=page)), \
         patch("miniapp.backend.routes.writes.update_page",
               AsyncMock(return_value=None)) as up, \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.delete("/api/arcana/clients/cli-d/object_photo/1")
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
    """Если у сеанса уже есть AI_Summary — возвращаем его без вызова Claude."""
    page = {
        "id": "s1",
        "properties": {
            "🪪 Пользователи": {"relation": [{"id": FAKE_NOTION_USER}]},
            "AI_Summary": {"rich_text": [{"plain_text": "Короткая суть уже была."}]},
            "Трактовка": {"rich_text": [{"plain_text": "<b>Долгая трактовка...</b>"}]},
        },
    }

    claude_mock = AsyncMock(return_value="НЕ должен вызываться")

    with patch("miniapp.backend.routes.writes.get_page",
               AsyncMock(return_value=page)), \
         patch("miniapp.backend.routes.writes.update_page",
               AsyncMock(return_value={"ok": True})), \
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
    page = {
        "id": "s2",
        "properties": {
            "🪪 Пользователи": {"relation": [{"id": FAKE_NOTION_USER}]},
            "AI_Summary": {"rich_text": []},
            "Трактовка": {"rich_text": [{"plain_text": "Очень длинная трактовка про шута и дорогу"}]},
        },
    }
    claude_mock = AsyncMock(return_value="Вывод: путь начинается сегодня.")

    with patch("miniapp.backend.routes.writes.get_page",
               AsyncMock(return_value=page)), \
         patch("miniapp.backend.routes.writes.update_page",
               AsyncMock(return_value={"ok": True})), \
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
