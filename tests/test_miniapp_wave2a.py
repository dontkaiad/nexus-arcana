"""Wave 2a tests — /api/tasks, /api/finance, /api/lists, /api/memory, /api/calendar."""
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


def _today_iso(tz: int = 3) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=tz)).strftime("%Y-%m-%d")


def _today_date(tz: int = 3):
    return (datetime.now(timezone.utc) + timedelta(hours=tz)).date()


# ── helpers to build fake Notion pages ──────────────────────────────────────


def _task(task_id, title, *, status="Not started", prio="🔴 Срочно",
          cat="🐾 Коты", deadline=None, reminder=None,
          repeat_time="", repeat=None, bot="☀️ Nexus"):
    return {
        "id": task_id,
        "properties": {
            "Задача": {"title": [{"plain_text": title}]},
            "Статус": {"status": {"name": status}},
            "Приоритет": {"select": {"name": prio}},
            "Категория": {"select": {"name": cat}},
            "Бот": {"select": {"name": bot}},
            "Дедлайн": {"date": {"start": deadline} if deadline else None},
            "Напоминание": {"date": {"start": reminder} if reminder else None},
            "Время повтора": {"rich_text": [{"plain_text": repeat_time}] if repeat_time else []},
            "Повтор": {"select": {"name": repeat} if repeat else None},
        },
    }


def _expense(amount, *, cat="🚬 Привычки", type_="💸 Расход", desc="test", eid="fin-1"):
    return {
        "id": eid,
        "properties": {
            "Описание": {"title": [{"plain_text": desc}]},
            "Сумма": {"number": amount},
            "Категория": {"select": {"name": cat}},
            "Тип": {"select": {"name": type_}},
            "Бот": {"select": {"name": "☀️ Nexus"}},
        },
    }


def _list_item(iid, name, *, type_="🛒 Покупки", status="Not started",
               cat="🍜 Продукты", qty=None, note=None, expires=None, price=None):
    return {
        "id": iid,
        "properties": {
            "Название": {"title": [{"plain_text": name}]},
            "Тип": {"select": {"name": type_}},
            "Статус": {"status": {"name": status}},
            "Категория": {"select": {"name": cat}},
            "Количество": {"number": qty},
            "Цена": {"number": price},
            "Заметка": {"rich_text": [{"plain_text": note}] if note else []},
            "Срок годности": {"date": {"start": expires} if expires else None},
            "Повторяющийся": {"checkbox": False},
            "Группа": {"rich_text": []},
        },
    }


def _mem(mid, text, cat=None, related=None, key=None, actual=True):
    props = {
        "Текст": {"title": [{"plain_text": text}]},
        "Актуально": {"checkbox": actual},
    }
    if cat:
        props["Категория"] = {"select": {"name": cat}}
    if related:
        props["Связь"] = {"rich_text": [{"plain_text": related}]}
    if key:
        props["Ключ"] = {"rich_text": [{"plain_text": key}]}
    return {"id": mid, "properties": props}


def _route_by_filter(mapping):
    """Возвращает side_effect для query_pages, диспатчит по содержимому filter."""
    async def _qp(_db_id, *, filters=None, **__):
        f_str = _json.dumps(filters or {}, ensure_ascii=False)
        for signature, pages in mapping.items():
            if signature in f_str:
                return pages
        return mapping.get("_default", [])
    return _qp


# ── /api/tasks ──────────────────────────────────────────────────────────────

def test_tasks_active_filters_and_sorts(client):
    tz = 3
    today = _today_iso(tz)
    yesterday = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=2)).strftime("%Y-%m-%d")
    tomorrow = (datetime.strptime(today, "%Y-%m-%d") + timedelta(days=3)).strftime("%Y-%m-%d")

    pages = [
        _task("a", "Активная 1", prio="🟡 Важно", deadline=tomorrow),
        _task("b", "Срочная сегодня", prio="🔴 Срочно", deadline=today),
        _task("c", "Потом", prio="⚪ Можно потом", deadline=tomorrow),
        # Просрочка — хоть статус активный, не попадает в active
        _task("d", "Просрочена", prio="🔴 Срочно", deadline=yesterday),
    ]

    async def qp(*_, **__):
        return pages

    with patch("miniapp.backend.routes.tasks.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.tasks.today_user_tz",
               AsyncMock(return_value=(_today_date(tz), tz))), \
         patch("miniapp.backend.routes.tasks.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/tasks?filter=active")

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["filter"] == "active"
    ids = [t["id"] for t in data["tasks"]]
    # d исключена (overdue), b первой (🔴), затем 🟡 a, потом ⚪ c
    assert ids == ["b", "a", "c"]
    assert data["tasks"][0]["prio"] == "🔴"
    assert data["tasks"][0]["cat"] == {"emoji": "🐾", "name": "Коты", "full": "🐾 Коты"}
    assert data["tasks"][0]["streak"] is None


def test_tasks_overdue_filter(client):
    tz = 3
    today = _today_iso(tz)
    yesterday = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=5)).strftime("%Y-%m-%d")

    pages = [_task("x", "Просрочка", deadline=yesterday, prio="🔴 Срочно")]

    async def qp(*_, **__):
        return pages

    with patch("miniapp.backend.routes.tasks.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.tasks.today_user_tz",
               AsyncMock(return_value=(_today_date(tz), tz))), \
         patch("miniapp.backend.routes.tasks.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/tasks?filter=overdue")

    assert r.status_code == 200
    data = r.json()
    assert data["tasks"][0]["status"] == "overdue"


def test_tasks_invalid_filter(client):
    r = client.get("/api/tasks?filter=bogus")
    assert r.status_code == 400


def test_tasks_empty(client):
    tz = 3
    async def qp(*_, **__):
        return []
    with patch("miniapp.backend.routes.tasks.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.tasks.today_user_tz",
               AsyncMock(return_value=(_today_date(tz), tz))), \
         patch("miniapp.backend.routes.tasks.get_user_notion_id",
               AsyncMock(return_value="")):
        r = client.get("/api/tasks")
    assert r.status_code == 200
    assert r.json() == {"filter": "active", "total": 0, "tasks": []}


def test_tasks_401_without_init_data():
    app.dependency_overrides.clear()
    c = TestClient(app)
    assert c.get("/api/tasks").status_code == 401


# ── /api/finance ────────────────────────────────────────────────────────────

def test_finance_view_today(client):
    tz = 3
    pages = [_expense(1500, cat="🚬 Привычки"), _expense(1104, cat="🍜 Продукты")]

    async def qp(*_, **__):
        return pages

    with patch("miniapp.backend.routes.finance.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.finance.today_user_tz",
               AsyncMock(return_value=(_today_date(tz), tz))), \
         patch("miniapp.backend.routes.finance.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/finance?view=today")

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["view"] == "today"
    assert data["total"] == 2604
    assert len(data["items"]) == 2
    assert data["items"][0]["cat"]["emoji"] in {"🚬", "🍜"}


def test_finance_view_month_calculates_income_expense_and_limits(client):
    """income = sum Доход, expense = sum Расход, by_category маппится на лимиты."""
    tz = 3
    month = _today_iso(tz)[:7]

    # Finance records: доход 115000, расход 14200 в 🚬, расход 2000 в 🍜
    finance_pages = [
        _expense(115000, type_="💰 Доход", cat="", eid="inc"),
        _expense(14200, cat="🚬 Привычки", eid="exp1"),
        _expense(2000, cat="🍜 Продукты", eid="exp2"),
    ]
    # Limits page for the 🚬 category
    limit_pages = [_mem(
        "lim", "лимит: 🚬 Привычки — 17685₽/мес",
        cat="💰 Лимит", related="привычки", key="лимит_habits",
    )]

    async def qp(db_id, **__):
        from core.config import config as _cfg
        filters = __.get("filters", {})
        f_str = _json.dumps(filters, ensure_ascii=False)
        if "Категория" in f_str and "Лимит" in f_str:
            return limit_pages
        return finance_pages

    with patch("core.budget.db_query", AsyncMock(return_value=limit_pages), create=True), \
         patch("miniapp.backend.routes.finance.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.finance.get_limits",
               AsyncMock(return_value={"привычки": 17685})), \
         patch("miniapp.backend.routes.finance.today_user_tz",
               AsyncMock(return_value=(_today_date(tz), tz))), \
         patch("miniapp.backend.routes.finance.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get(f"/api/finance?view=month&month={month}")

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["income"] == 115000
    assert data["expense"] == 16200
    assert data["balance"] == 115000 - 16200
    habits = next((c for c in data["by_category"] if c["cat"]["full"] == "🚬 Привычки"), None)
    assert habits is not None
    assert habits["spent"] == 14200
    assert habits["limit"] == 17685
    assert habits["pct"] == round(14200 / 17685 * 100)
    food = next(c for c in data["by_category"] if c["cat"]["full"] == "🍜 Продукты")
    assert food["limit"] is None
    assert food["pct"] is None


def test_finance_view_limits_only_shows_categories_with_limit(client):
    tz = 3
    month = _today_iso(tz)[:7]

    finance_pages = [
        _expense(14200, cat="🚬 Привычки"),
        _expense(5000, cat="🍜 Продукты"),  # без лимита — не должна появиться
    ]

    async def qp(*_, **__):
        return finance_pages

    with patch("miniapp.backend.routes.finance.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.finance.get_limits",
               AsyncMock(return_value={"привычки": 17685})), \
         patch("miniapp.backend.routes.finance.today_user_tz",
               AsyncMock(return_value=(_today_date(tz), tz))), \
         patch("miniapp.backend.routes.finance.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get(f"/api/finance?view=limits&month={month}")

    assert r.status_code == 200
    data = r.json()
    assert len(data["categories"]) == 1
    habits = data["categories"][0]
    assert habits["cat"]["full"] == "🚬 Привычки"
    assert habits["zone"] == "yellow"  # 14200/17685 ≈ 80%


def test_finance_view_goals(client):
    tz = 3
    budget = {
        "доходы": [], "обязательные": [], "лимиты": [],
        "цели": [{"name": "Samsung Flip", "target": 100000, "saving": 8000,
                  "key": "цель_flip", "fact": "цель: Samsung Flip — 100 000₽ · откладываю 8000₽"}],
        "долги": [{"name": "Вика", "amount": 50000, "deadline": "апрель",
                   "strategy": "равными частями", "monthly_payment": 12500,
                   "fact": "...", "key": "долг_vika"}],
    }

    with patch("miniapp.backend.routes.finance.load_budget_data",
               AsyncMock(return_value=budget)), \
         patch("miniapp.backend.routes.finance.today_user_tz",
               AsyncMock(return_value=(_today_date(tz), tz))), \
         patch("miniapp.backend.routes.finance.get_user_notion_id",
               AsyncMock(return_value="")):
        r = client.get("/api/finance?view=goals")

    assert r.status_code == 200
    data = r.json()
    assert len(data["debts"]) == 1
    debt = data["debts"][0]
    assert debt["total"] == debt["left"] == 50000
    assert debt["by"] == "апрель"
    assert "равными" in (debt["note"] or "")
    goal = data["goals"][0]
    assert goal["target"] == 100000
    assert goal["saved"] == 0
    assert goal["monthly"] == 8000
    assert goal["after"] is None


def test_finance_invalid_view(client):
    r = client.get("/api/finance?view=bogus")
    assert r.status_code == 400


def test_finance_401_without_init_data():
    app.dependency_overrides.clear()
    c = TestClient(app)
    assert c.get("/api/finance").status_code == 401


# ── /api/lists ──────────────────────────────────────────────────────────────

def test_lists_buy_returns_items(client):
    pages = [
        _list_item("l1", "Молоко", cat="🍜 Продукты", qty=1, note="Простоквашино"),
        _list_item("l2", "Хлеб", status="Done", cat="🍜 Продукты"),
    ]

    async def qp(*_, **__):
        return pages

    with patch("miniapp.backend.routes.lists.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.lists.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/lists?type=buy")

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["type"] == "buy"
    assert len(data["items"]) == 2
    milk = next(i for i in data["items"] if i["name"] == "Молоко")
    assert milk["cat"]["emoji"] == "🍜"
    assert milk["qty"] == 1
    bread = next(i for i in data["items"] if i["name"] == "Хлеб")
    assert bread["done"] is True


def test_lists_inv_sorts_by_expiry(client):
    soon = (datetime.now().date() + timedelta(days=5)).isoformat()
    later = (datetime.now().date() + timedelta(days=30)).isoformat()
    pages = [
        _list_item("a", "Потом", type_="📦 Инвентарь", expires=later),
        _list_item("b", "Скоро", type_="📦 Инвентарь", expires=soon),
        _list_item("c", "Без срока", type_="📦 Инвентарь", expires=None),
    ]

    async def qp(*_, **__):
        return pages

    with patch("miniapp.backend.routes.lists.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.lists.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/lists?type=inv")

    assert r.status_code == 200
    items = r.json()["items"]
    assert [i["name"] for i in items] == ["Скоро", "Потом", "Без срока"]


def test_lists_q_filter(client):
    pages = [
        _list_item("a", "Молоко", note="3,2%"),
        _list_item("b", "Хлеб", note="бородинский"),
        _list_item("c", "Сыр", note=None),
    ]

    async def qp(*_, **__):
        return pages

    with patch("miniapp.backend.routes.lists.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.lists.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/lists?type=buy&q=мол")

    items = r.json()["items"]
    assert [i["name"] for i in items] == ["Молоко"]


def test_lists_invalid_type(client):
    r = client.get("/api/lists?type=bogus")
    assert r.status_code == 400


def test_lists_empty(client):
    async def qp(*_, **__):
        return []
    with patch("miniapp.backend.routes.lists.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.lists.get_user_notion_id",
               AsyncMock(return_value="")):
        r = client.get("/api/lists?type=check")
    assert r.status_code == 200
    assert r.json() == {"type": "check", "items": []}


def test_lists_401_without_init_data():
    app.dependency_overrides.clear()
    c = TestClient(app)
    assert c.get("/api/lists").status_code == 401


# ── /api/memory ─────────────────────────────────────────────────────────────

def test_memory_excludes_budget_and_adhd_categories(client):
    pages = [
        _mem("m1", "Chapman = сигареты", cat="🛒 Предпочтения", key="chapman"),
        _mem("m2", "Работает техника 2 минут", cat="🧠 СДВГ", key="2min"),
        _mem("m3", "доход: ЗП — 115000₽", cat="📥 Доход", key="income_zp"),
        _mem("m4", "подруга Аня", cat="👥 Люди", key="anya"),
    ]

    async def qp(*_, **__):
        return pages

    with patch("miniapp.backend.routes.memory.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.memory.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/memory")

    assert r.status_code == 200
    data = r.json()
    names = {i["id"] for i in data["items"]}
    assert names == {"m1", "m4"}
    assert set(data["categories"]) == {"🛒 Предпочтения", "👥 Люди"}


def test_memory_cat_filter(client):
    pages = [
        _mem("m1", "A", cat="🛒 Предпочтения"),
        _mem("m2", "B", cat="👥 Люди"),
    ]

    async def qp(*_, **__):
        return pages

    with patch("miniapp.backend.routes.memory.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.memory.get_user_notion_id",
               AsyncMock(return_value="")):
        r = client.get("/api/memory?cat=%F0%9F%91%A5%20%D0%9B%D1%8E%D0%B4%D0%B8")  # 👥 Люди

    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1 and items[0]["id"] == "m2"


def test_memory_adhd_returns_records_and_uses_cache(client):
    pages = [_mem("a1", "Работает техника 2 минут", cat="🧠 СДВГ")]
    sonnet = AsyncMock(return_value="Персональный профиль...")

    async def qp(*_, **__):
        return pages

    with patch("miniapp.backend.routes.memory.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.memory.ask_claude", sonnet), \
         patch("miniapp.backend.routes.memory.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r1 = client.get("/api/memory/adhd")
        r2 = client.get("/api/memory/adhd")

    assert r1.status_code == 200
    assert r1.json()["profile"] == "Персональный профиль..."
    assert len(r1.json()["records"]) == 1
    assert r2.json()["profile"] == "Персональный профиль..."
    # Sonnet должен быть вызван ровно один раз — второй ответ из кэша
    assert sonnet.await_count == 1


def test_memory_401_without_init_data():
    app.dependency_overrides.clear()
    c = TestClient(app)
    assert c.get("/api/memory").status_code == 401


# ── /api/calendar ───────────────────────────────────────────────────────────

def test_calendar_groups_tasks_by_day(client):
    tz = 3
    today = _today_date(tz)
    month = today.strftime("%Y-%m")
    d22 = today.replace(day=22).isoformat()
    d15 = today.replace(day=15).isoformat()

    pages = [
        _task("a", "Лоток", deadline=d22, prio="🟡 Важно"),
        _task("b", "Счёт", deadline=d22, prio="🔴 Срочно"),
        _task("c", "Тренажёрка", deadline=d15, prio="⚪ Можно потом"),
    ]

    async def qp(*_, **__):
        return pages

    with patch("miniapp.backend.routes.calendar.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.calendar.today_user_tz",
               AsyncMock(return_value=(today, tz))), \
         patch("miniapp.backend.routes.calendar.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get(f"/api/calendar?month={month}")

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["month"] == month
    d22_bucket = data["days"]["22"]
    assert d22_bucket["count"] == 2
    assert d22_bucket["has_high_prio"] is True
    assert {t["id"] for t in d22_bucket["tasks"]} == {"a", "b"}
    d1_bucket = data["days"]["1"]
    assert d1_bucket == {"count": 0, "has_overdue": False,
                         "has_high_prio": False, "tasks": []}


def test_calendar_defaults_to_current_month(client):
    tz = 3
    today = _today_date(tz)

    async def qp(*_, **__):
        return []

    with patch("miniapp.backend.routes.calendar.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.calendar.today_user_tz",
               AsyncMock(return_value=(today, tz))), \
         patch("miniapp.backend.routes.calendar.get_user_notion_id",
               AsyncMock(return_value="")):
        r = client.get("/api/calendar")

    assert r.status_code == 200
    assert r.json()["month"] == today.strftime("%Y-%m")


def test_calendar_401_without_init_data():
    app.dependency_overrides.clear()
    c = TestClient(app)
    assert c.get("/api/calendar").status_code == 401
