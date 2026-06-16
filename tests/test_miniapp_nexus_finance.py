"""Mini App — финансы: GET/POST /api/finance, /api/expenses, /api/categories.

Views today/month/limits/goals, дневной бюджет, drill-down по категории,
создание расхода/дохода/практики.

Собрано из wave2a / wave3 / wave5 / wave6 при реорганизации тестов по доменам.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from miniapp.backend import cache
from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id
from core.repos.pg_finance_repo import BudgetEntry
from core.repos.pg_memory_repo import Memory


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


# ── helpers: fake Notion pages ───────────────────────────────────────────────

def _budget_entry(amount, *, cat="🚬 Привычки", type_="💸 Расход", desc="test", eid="fin-1", date="2026-06-01"):
    return BudgetEntry(id=eid, description=desc, amount=amount, category=cat,
                       type_=type_, source="💳 Карта", date=date, user_notion_id="")


def _mem_pg(mid, text, cat=None, key=None):
    return Memory(id=mid, fact=text, category=cat or "", related_to="", key=key or "")


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


# ── GET /api/finance ─────────────────────────────────────────────────────────

def test_finance_view_today(client):
    tz = 3
    entries = [_budget_entry(1500, cat="🚬 Привычки"), _budget_entry(1104, cat="🍜 Продукты")]

    with patch("miniapp.backend.routes.finance._budget_repo.query",
               AsyncMock(return_value=entries)), \
         patch("miniapp.backend.routes.finance._mem_repo.find_by_key",
               AsyncMock(return_value=[])), \
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

    finance_entries = [
        _budget_entry(115000, type_="💰 Доход", cat="", eid="inc"),
        _budget_entry(14200, cat="🚬 Привычки", eid="exp1"),
        _budget_entry(2000, cat="🍜 Продукты", eid="exp2"),
    ]

    with patch("miniapp.backend.routes.finance._budget_repo.query",
               AsyncMock(return_value=finance_entries)), \
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

    finance_entries = [
        _budget_entry(14200, cat="🚬 Привычки"),
        _budget_entry(5000, cat="🍜 Продукты"),  # без лимита — не должна появиться
    ]

    with patch("miniapp.backend.routes.finance._budget_repo.query",
               AsyncMock(return_value=finance_entries)), \
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
        "долги": [{"name": "***", "amount": 50000, "deadline": "апрель",
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
    # При monthly>0 и target>0 API возвращает ETA-строку «~месяц YYYY»
    assert isinstance(goal["after"], str) and goal["after"].startswith("~")


def test_finance_invalid_view(client):
    r = client.get("/api/finance?view=bogus")
    assert r.status_code == 400


def test_finance_401_without_init_data():
    app.dependency_overrides.clear()
    c = TestClient(app)
    assert c.get("/api/finance").status_code == 401


# ── GET /api/finance?view=today — блок budget ───────────────────────────────

def test_finance_today_returns_budget_block(client):
    with patch("miniapp.backend.routes.finance._budget_repo.query",
               AsyncMock(return_value=[])), \
         patch("miniapp.backend.routes.finance._mem_repo.find_by_key",
               AsyncMock(return_value=[])), \
         patch("miniapp.backend.routes.finance.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))), \
         patch("miniapp.backend.routes.finance.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/finance?view=today")

    assert r.status_code == 200
    data = r.json()
    assert "budget" in data
    assert data["budget"]["day"] == 4166  # дефолт
    assert data["budget"]["spent"] == 0
    assert data["budget"]["left"] == 4166
    assert data["budget"]["pct"] == 0


def test_finance_today_budget_reflects_spending(client):
    entries = [_budget_entry(2000, cat="🍜 Продукты", desc="магнит", eid="p1")]

    with patch("miniapp.backend.routes.finance._budget_repo.query",
               AsyncMock(return_value=entries)), \
         patch("miniapp.backend.routes.finance._mem_repo.find_by_key",
               AsyncMock(return_value=[])), \
         patch("miniapp.backend.routes.finance.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))), \
         patch("miniapp.backend.routes.finance.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/finance?view=today")

    assert r.status_code == 200
    b = r.json()["budget"]
    assert b["spent"] == 2000
    assert b["left"] == 2166
    assert b["pct"] == round(2000 / 4166 * 100)


# ── GET /api/finance/category — drill-down ──────────────────────────────────

def test_finance_category_drill_down(client):
    """Wave5.9: /api/finance/category возвращает список трат по категории."""
    entries = [
        _budget_entry(4500, cat="🏠 Ж***", desc="коммуналка", eid="e1", date="2026-04-02"),
        _budget_entry(800, cat="🏠 Ж***", desc="интернет", eid="e2", date="2026-04-18"),
    ]

    with patch("miniapp.backend.routes.finance._budget_repo.query",
               AsyncMock(return_value=entries)), \
         patch("miniapp.backend.routes.finance._mem_repo.find_by_category",
               AsyncMock(return_value=[])), \
         patch("miniapp.backend.routes.finance.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))), \
         patch("miniapp.backend.routes.finance.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/finance/category?cat=🏠%20Ж***&month=2026-04")

    assert r.status_code == 200
    data = r.json()
    assert data["cat"] == "🏠 Ж***"
    assert data["month"] == "2026-04"
    assert data["total"] == 5300
    assert data["count"] == 2
    # Сортировка по дате desc — самая свежая трата сверху.
    assert data["items"][0]["desc"] == "интернет"
    assert data["items"][1]["desc"] == "коммуналка"


# ── POST /api/expenses (deprecated alias) ───────────────────────────────────

def test_expense_create_uses_finance_add(client):
    tz = 3
    today = _today_date(tz)
    with patch("miniapp.backend.routes.writes.finance_add",
               AsyncMock(return_value="fin-id")) as fa, \
         patch("miniapp.backend.routes.writes.today_user_tz",
               AsyncMock(return_value=(today, tz))), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/expenses", json={
            "amount": 1500,
            "cat": "🚬 Привычки",
            "desc": "Chapman",
            "bot": "nexus",
        })
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["id"] == "fin-id"
    kwargs = fa.await_args.kwargs
    assert kwargs["amount"] == 1500
    assert kwargs["category"] == "🚬 Привычки"
    assert kwargs["type_"] == "💸 Расход"
    assert kwargs["bot_label"] == "☀️ Nexus"
    assert kwargs["date"] == today.isoformat()


def test_expense_rejects_zero_amount(client):
    r = client.post("/api/expenses", json={"amount": 0, "cat": "🍜 Продукты"})
    assert r.status_code == 422  # pydantic validation


def test_expenses_alias_still_works(client):
    """Deprecated /api/expenses всё ещё работает через finance_create."""
    captured = {}

    async def fake_finance_add(**kwargs):
        captured.update(kwargs)
        return "legacy-id"

    with patch("miniapp.backend.routes.writes.finance_add", side_effect=fake_finance_add), \
         patch("miniapp.backend.routes.writes.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/expenses", json={
            "amount": 200, "cat": "🍜 Продукты", "desc": "test",
        })

    assert r.status_code == 200
    assert r.json()["type"] == "expense"
    assert captured["type_"] == "💸 Расход"


# ── POST /api/finance (income/expense/practice_income) ──────────────────────

def test_finance_post_expense_routes_to_finance_add(client):
    captured = {}

    async def fake_finance_add(**kwargs):
        captured.update(kwargs)
        return "new-page-id"

    with patch("miniapp.backend.routes.writes.finance_add", side_effect=fake_finance_add), \
         patch("miniapp.backend.routes.writes.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/finance", json={
            "type": "expense",
            "amount": 500,
            "cat": "🍜 Продукты",
            "desc": "Магнит",
        })

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["type"] == "expense"
    assert captured["type_"] == "💸 Расход"
    assert captured["category"] == "🍜 Продукты"
    assert captured["amount"] == 500
    assert captured["bot_label"] == "☀️ Nexus"


def test_finance_post_income_default_category(client):
    captured = {}

    async def fake_finance_add(**kwargs):
        captured.update(kwargs)
        return "inc-id"

    with patch("miniapp.backend.routes.writes.finance_add", side_effect=fake_finance_add), \
         patch("miniapp.backend.routes.writes.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/finance", json={
            "type": "income",
            "amount": 80000,
            "desc": "зарплата",
        })

    assert r.status_code == 200, r.text
    assert captured["type_"] == "💰 Доход"
    assert captured["category"] == "🏦 Прочее"  # дефолт когда cat не указан
    assert captured["amount"] == 80000


def test_finance_post_practice_income_forces_arcana(client):
    captured = {}

    async def fake_finance_add(**kwargs):
        captured.update(kwargs)
        return "practice-id"

    with patch("miniapp.backend.routes.writes.finance_add", side_effect=fake_finance_add), \
         patch("miniapp.backend.routes.writes.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/finance", json={
            "type": "practice_income",
            "amount": 3500,
            "desc": "клиент Анна",
            "bot": "nexus",  # игнорируется, практика всегда Arcana
        })

    assert r.status_code == 200, r.text
    assert captured["bot_label"] == "🌒 Arcana"
    assert captured["type_"] == "💰 Доход"


def test_finance_expense_requires_category(client):
    with patch("miniapp.backend.routes.writes.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/finance", json={
            "type": "expense",
            "amount": 100,
        })
    assert r.status_code == 400
    assert "cat is required" in r.json()["detail"]


# ── GET /api/categories ──────────────────────────────────────────────────────

def test_categories_task_returns_merged_list(client):
    """GET /api/categories?type=task возвращает существующие + дефолты."""
    pages = [
        {"id": "t1", "properties": {"Категория": {"select": {"name": "🐾 Коты"}}}},
        {"id": "t2", "properties": {"Категория": {"select": {"name": "🐾 Коты"}}}},
        {"id": "t3", "properties": {"Категория": {"select": {"name": "💜 Люди"}}}},
        # запись без категории
        {"id": "t4", "properties": {"Категория": {"select": None}}},
    ]

    async def qp(*_, **__):
        return pages

    with patch("miniapp.backend.routes.categories.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.categories.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/categories?type=task")

    assert r.status_code == 200
    data = r.json()
    assert data["type"] == "task"
    assert "🐾 Коты" in data["categories"]
    assert "💜 Люди" in data["categories"]
    # дефолты тоже добавились
    assert "🏠 Дом" in data["categories"]
    assert "💼 Работа" in data["categories"]
    # без дублирования
    assert data["categories"].count("🐾 Коты") == 1


def test_categories_invalid_type(client):
    r = client.get("/api/categories?type=bogus")
    assert r.status_code == 400


def test_categories_income_returns_defaults_when_empty(client):
    async def qp(*_, **__):
        return []

    with patch("miniapp.backend.routes.categories.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.categories.get_user_notion_id",
               AsyncMock(return_value="")):
        r = client.get("/api/categories?type=income")

    assert r.status_code == 200
    cats = r.json()["categories"]
    assert "💼 Зарплата" in cats
    assert "🏦 Прочее" in cats
