"""Wave 6 tests."""
from __future__ import annotations

import json as _json
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


def _today_date():
    return (datetime.now(timezone.utc) + timedelta(hours=3)).date()


def _list_page(iid, *, name="item", type_="📋 Чеклист", status="Not started", bot_name="☀️ Nexus"):
    props = {
        "Название": {"title": [{"plain_text": name}]},
        "Тип": {"select": {"name": type_}},
        "Статус": {"status": {"name": status}},
        "Категория": {"select": None},
        "Количество": {"number": None},
        "Цена": {"number": None},
        "Заметка": {"rich_text": []},
        "Срок годности": {"date": None},
        "Повторяющийся": {"checkbox": False},
        "Группа": {"rich_text": []},
    }
    if bot_name is not None:
        props["Бот"] = {"select": {"name": bot_name}}
    else:
        props["Бот"] = {"select": None}
    return {"id": iid, "properties": props}


# ═════════════════════════════════════════════════════════════════════════════
# Stage 1.1: lists loading — client-side type matching + emoji tolerance
# ═════════════════════════════════════════════════════════════════════════════

def test_lists_check_loads_with_exact_emoji(client):
    pages = [
        _list_page("c1", name="купить молоко", type_="📋 Чеклист"),
        _list_page("c2", name="старое покупочное", type_="🛒 Покупки"),
    ]

    async def qp(*_, **__):
        return pages

    with patch("miniapp.backend.routes.lists.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.lists.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/lists?type=check")

    assert r.status_code == 200
    ids = [i["id"] for i in r.json()["items"]]
    assert "c1" in ids
    assert "c2" not in ids


def test_lists_check_loads_when_type_has_diff_spacing(client):
    """Если в Notion тип записан как '📋  Чеклист' с 2 пробелами — client-side match ловит."""
    pages = [
        _list_page("c1", name="план дня", type_="📋  Чеклист"),
        _list_page("c2", name="утро ритуал", type_="📋 Чеклист "),  # trailing space
    ]

    async def qp(*_, **__):
        return pages

    with patch("miniapp.backend.routes.lists.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.lists.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/lists?type=check")

    assert r.status_code == 200
    ids = {i["id"] for i in r.json()["items"]}
    assert ids == {"c1", "c2"}


def test_lists_inv_matches_partial_keyword(client):
    pages = [
        _list_page("i1", name="гречка", type_="📦 Инвентарь"),
        _list_page("i2", name="перец", type_="📦  Инвентарь"),  # double space
    ]

    async def qp(*_, **__):
        return pages

    with patch("miniapp.backend.routes.lists.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.lists.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/lists?type=inv")

    assert r.status_code == 200
    assert len(r.json()["items"]) == 2


def test_lists_archived_filtered_out(client):
    pages = [
        _list_page("a1", name="активное", type_="📋 Чеклист", status="Not started"),
        _list_page("a2", name="архив", type_="📋 Чеклист", status="Archived"),
    ]

    async def qp(*_, **__):
        return pages

    with patch("miniapp.backend.routes.lists.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.lists.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/lists?type=check")

    ids = [i["id"] for i in r.json()["items"]]
    assert ids == ["a1"]


# ═════════════════════════════════════════════════════════════════════════════
# Stage 1.3: /api/finance?view=today возвращает блок budget
# ═════════════════════════════════════════════════════════════════════════════

def test_finance_today_returns_budget_block(client):
    async def qp(*_, **__):
        return []  # нет расходов

    with patch("miniapp.backend.routes.finance.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.finance.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))), \
         patch("miniapp.backend.routes.finance.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)), \
         patch("core.notion_client.memory_get", AsyncMock(return_value=None)):
        r = client.get("/api/finance?view=today")

    assert r.status_code == 200
    data = r.json()
    assert "budget" in data
    assert data["budget"]["day"] == 4166  # дефолт
    assert data["budget"]["spent"] == 0
    assert data["budget"]["left"] == 4166
    assert data["budget"]["pct"] == 0


# ═════════════════════════════════════════════════════════════════════════════
# Stage 4: tarot.py — deck registry, card matcher, canonical_card
# ═════════════════════════════════════════════════════════════════════════════

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


# ═════════════════════════════════════════════════════════════════════════════
# Stage 3: /api/streaks + /api/streaks/week
# ═════════════════════════════════════════════════════════════════════════════

def test_streaks_endpoint_returns_current_and_best(client):
    with patch("nexus.handlers.streaks.get_streak",
               return_value={"streak": 12, "best": 30, "last_activity_date": "2026-04-21",
                             "rest_day_date": None, "rest_days_used": 0,
                             "streak_start_date": "2026-04-10"}), \
         patch("nexus.handlers.streaks.is_rest_day_available", return_value=True):
        r = client.get("/api/streaks")
    assert r.status_code == 200
    data = r.json()
    assert data["current"] == 12
    assert data["best"] == 30
    assert data["rest_day_available"] is True
    assert "per_task" in data


def test_weather_route_is_registered():
    """hotfix: /api/weather должен быть в списке роутов FastAPI app."""
    from miniapp.backend.app import app
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/weather" in paths


def test_weather_returns_cached_or_fetches(client, tmp_path, monkeypatch):
    """Cache ключ — tg_id; при первом запросе — вызов Open-Meteo; при повторном — из кэша."""
    import miniapp.backend.routes.weather as w

    # direct in-test call: fake tz + fake openmeteo
    async def fake_memory_get(key):
        return "Europe/Moscow" if key.startswith("tz_") else None

    fetch_call_count = {"n": 0}

    async def fake_fetch(city):
        fetch_call_count["n"] += 1
        return {"city": city, "temp": 12, "code": 0, "kind": "clear", "description": "Ясно"}

    with patch("miniapp.backend.routes.weather.memory_get", side_effect=fake_memory_get), \
         patch("miniapp.backend.routes.weather._fetch_openmeteo", side_effect=fake_fetch):
        r1 = client.get("/api/weather")
        r2 = client.get("/api/weather")

    assert r1.status_code == 200
    assert r1.json()["city"] == "Moscow"
    assert r1.json()["temp"] == 12
    assert r1.json()["kind"] == "clear"
    # второй запрос — из кэша (не второй fetch)
    assert fetch_call_count["n"] == 1


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

    from unittest.mock import AsyncMock as AM, MagicMock
    claude_mock = AM(return_value="НЕ должен вызываться")

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
    from unittest.mock import AsyncMock as AM
    claude_mock = AM(return_value="Вывод: путь начинается сегодня.")

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


def test_streaks_week_returns_7_days(client):
    with patch("miniapp.backend.routes.streaks.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))), \
         patch("miniapp.backend.routes.streaks._has_activity_on", return_value=False):
        r = client.get("/api/streaks/week")
    assert r.status_code == 200
    days = r.json()["days"]
    assert len(days) == 7
    # последний день — сегодня
    assert days[-1]["is_today"] is True
    # все имеют weekday
    for d in days:
        assert d["weekday"] in ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


def test_finance_today_budget_reflects_spending(client):
    pages = [
        {
            "id": "p1",
            "properties": {
                "Сумма": {"number": 2000},
                "Описание": {"title": [{"plain_text": "магнит"}]},
                "Тип": {"select": {"name": "💸 Расход"}},
                "Категория": {"select": {"name": "🍜 Продукты"}},
            },
        },
    ]

    async def qp(*_, **__):
        return pages

    with patch("miniapp.backend.routes.finance.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.finance.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))), \
         patch("miniapp.backend.routes.finance.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)), \
         patch("core.notion_client.memory_get", AsyncMock(return_value=None)):
        r = client.get("/api/finance?view=today")

    assert r.status_code == 200
    b = r.json()["budget"]
    assert b["spent"] == 2000
    assert b["left"] == 2166
    assert b["pct"] == round(2000 / 4166 * 100)
