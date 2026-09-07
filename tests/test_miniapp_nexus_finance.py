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
FAKE_USER_ID = "user-notion-id-42"


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
                       type_=type_, source="💳 Карта", date=date, user_id="")


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
         patch("miniapp.backend.routes.finance._mem_repo.find_by_exact_key",
               AsyncMock(return_value=[])), \
         patch("miniapp.backend.routes.finance.today_user_tz",
               AsyncMock(return_value=(_today_date(tz), tz))), \
         patch("miniapp.backend.routes.finance.get_user_id",
               AsyncMock(return_value=FAKE_USER_ID)):
        r = client.get("/api/finance?view=today")

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["view"] == "today"
    assert data["total"] == 2604
    assert len(data["items"]) == 2
    assert data["items"][0]["cat"]["emoji"] in {"🚬", "🍜"}


def test_finance_view_today_explicit_date_navigates_not_real_today(client):
    """?view=today&date=YYYY-MM-DD — total/items за ЭТУ дату, не за реальное
    сегодня. «Бюджет дня» (budget.spent/left/pct) при этом остаётся про
    реальное сегодня — не переезжает с датой навигации."""
    tz = 3
    today_iso = _today_iso(tz)
    other_date = "2020-01-15"  # заведомо не сегодня

    other_day_entries = [_budget_entry(700, cat="🍜 Продукты", eid="e-other", date=other_date)]
    today_entries = [_budget_entry(300, cat="🚕 Транспорт", eid="e-today", date=today_iso)]

    calls = []

    async def fake_query(**kw):
        calls.append(kw)
        if kw.get("date_from") == other_date:
            return other_day_entries
        if kw.get("date_from") == today_iso:
            return today_entries
        return []

    with patch("miniapp.backend.routes.finance._budget_repo.query",
               AsyncMock(side_effect=fake_query)), \
         patch("miniapp.backend.routes.finance._mem_repo.find_by_exact_key",
               AsyncMock(return_value=[])), \
         patch("miniapp.backend.routes.finance.today_user_tz",
               AsyncMock(return_value=(_today_date(tz), tz))), \
         patch("miniapp.backend.routes.finance.get_user_id",
               AsyncMock(return_value=FAKE_USER_ID)):
        r = client.get(f"/api/finance?view=today&date={other_date}")

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["date"] == other_date
    assert data["total"] == 700
    assert len(data["items"]) == 1
    assert data["items"][0]["desc"] == "test"
    assert data["items"][0]["amt"] == 700

    # budget.spent — за реальное сегодня (300), НЕ за просматриваемую дату (700).
    assert data["budget"]["spent"] == 300

    # Два отдельных запроса: за просматриваемую дату и за реальное сегодня.
    date_froms = {c["date_from"] for c in calls}
    assert date_froms == {other_date, today_iso}


def test_finance_view_today_no_date_param_defaults_to_today(client):
    """Без ?date= — поведение как раньше: одна дата, один запрос."""
    tz = 3
    entries = [_budget_entry(500, cat="🍜 Продукты")]

    calls = []

    async def fake_query(**kw):
        calls.append(kw)
        return entries

    with patch("miniapp.backend.routes.finance._budget_repo.query",
               AsyncMock(side_effect=fake_query)), \
         patch("miniapp.backend.routes.finance._mem_repo.find_by_exact_key",
               AsyncMock(return_value=[])), \
         patch("miniapp.backend.routes.finance.today_user_tz",
               AsyncMock(return_value=(_today_date(tz), tz))), \
         patch("miniapp.backend.routes.finance.get_user_id",
               AsyncMock(return_value=FAKE_USER_ID)):
        r = client.get("/api/finance?view=today")

    data = r.json()
    assert data["date"] == _today_iso(tz)
    assert data["total"] == 500
    assert data["budget"]["spent"] == 500
    assert len(calls) == 1  # без навигации — один запрос, как раньше


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
         patch("miniapp.backend.routes.finance.get_user_id",
               AsyncMock(return_value=FAKE_USER_ID)):
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
         patch("miniapp.backend.routes.finance.get_user_id",
               AsyncMock(return_value=FAKE_USER_ID)):
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
        "доходы": [], "постоянные": [], "лимиты": [],
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
         patch("miniapp.backend.routes.finance.get_user_id",
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


def test_finance_view_goals_no_cushion_field(client):
    """Подушка живёт в своей вкладке (view=cushion), не в view=goals."""
    tz = 3
    budget = {
        "доходы": [], "постоянные": [], "лимиты": [], "долги": [],
        "цели": [{"name": "Телефон", "target": 100000, "saving": 0,
                  "key": "цель_телефон", "fact": "цель: Телефон — 100000₽ · откладываю 0₽"}],
        "подушка": {"balance": 42000, "target": 300000, "planned_contribution": 5000},
    }
    with patch("miniapp.backend.routes.finance.load_budget_data",
               AsyncMock(return_value=budget)), \
         patch("miniapp.backend.routes.finance._mem_repo.find_by_category",
               AsyncMock(return_value=[])), \
         patch("core.repos.pg_debts_repo._repo.list_closed", AsyncMock(return_value=[])), \
         patch("miniapp.backend.routes.finance.today_user_tz",
               AsyncMock(return_value=(_today_date(tz), tz))), \
         patch("miniapp.backend.routes.finance.get_user_id",
               AsyncMock(return_value="")):
        r = client.get("/api/finance?view=goals")

    assert r.status_code == 200
    data = r.json()
    assert "cushion" not in data
    names = [g["name"] for g in data["goals"]] + [g["name"] for g in data["closed_goals"]]
    assert not any("одушк" in n for n in names)


def test_finance_view_cushion_structure(client):
    """view=cushion: баланс/цель/взнос + постраничный лог пополнений."""
    from core.repos.pg_cushion_repo import Cushion, CushionTx
    c = Cushion(balance=42000, target=300000, planned_contribution=5000)
    txs = [
        CushionTx(amount=5000, source="payday_auto", note="взнос за период 2026-08",
                  created_at="2026-08-01T00:00:00+00:00"),
        CushionTx(amount=3000, source="manual", note="", created_at="2026-07-15T10:00:00+00:00"),
    ]
    with patch("core.repos.pg_cushion_repo._repo.get", AsyncMock(return_value=c)), \
         patch("core.repos.pg_cushion_repo._repo.list_transactions",
               AsyncMock(return_value=(txs, False))), \
         patch("miniapp.backend.routes.finance.today_user_tz",
               AsyncMock(return_value=(_today_date(3), 3))), \
         patch("miniapp.backend.routes.finance.get_user_id", AsyncMock(return_value="u")):
        r = client.get("/api/finance?view=cushion")

    assert r.status_code == 200
    d = r.json()
    assert d["view"] == "cushion"
    assert d["balance"] == 42000.0 and d["target"] == 300000.0
    assert d["planned_contribution"] == 5000.0
    assert d["has_more"] is False and d["page"] == 0
    assert d["transactions"][0] == {
        "amount": 5000.0, "source": "payday_auto",
        "note": "взнос за период 2026-08", "created_at": "2026-08-01",
    }


def test_finance_cushion_set_target_endpoint(client):
    """POST /finance/cushion/target меняет только target, баланс не трогает."""
    with patch("core.repos.pg_cushion_repo._repo.set_target", AsyncMock()) as m_set, \
         patch("core.repos.pg_cushion_repo._repo.add_to_balance", AsyncMock()) as m_add, \
         patch("miniapp.backend.routes.writes.get_user_id", AsyncMock(return_value="u")):
        r = client.post("/api/finance/cushion/target", json={"target": 250000})

    assert r.status_code == 200
    assert r.json() == {"ok": True, "target": 250000.0}
    m_set.assert_awaited_once()
    assert m_set.await_args.args[1] == 250000.0
    m_add.assert_not_awaited()


def test_finance_closed_goal_from_goals_table(client):
    """#205: закрытые цели — из таблицы goals (status achieved|dropped)."""
    from core.repos.pg_goals_repo import Goal
    tz = 3
    closed = [
        Goal(id="1", name="Samsung Flip", target=100000, monthly=8000,
             saved=100000, status="achieved", closed_at="2026-08-15T00:00:00"),
        Goal(id="2", name="Ноутбук", target=200000, monthly=0, saved=1000,
             status="dropped", closed_at="2026-07-01T00:00:00"),
    ]
    with patch("miniapp.backend.routes.finance.load_budget_data",
               AsyncMock(return_value={"доходы": [], "постоянные": [], "лимиты": [],
                                       "цели": [], "долги": []})), \
         patch("core.repos.pg_goals_repo._repo.list_closed", AsyncMock(return_value=closed)), \
         patch("core.repos.pg_debts_repo._repo.list_closed", AsyncMock(return_value=[])), \
         patch("core.repos.pg_debts_repo._repo.list_active", AsyncMock(return_value=[])), \
         patch("miniapp.backend.routes.finance._find_debt_taken_dates", AsyncMock(return_value={})), \
         patch("miniapp.backend.routes.finance.today_user_tz",
               AsyncMock(return_value=(_today_date(tz), tz))), \
         patch("miniapp.backend.routes.finance.get_user_id",
               AsyncMock(return_value=FAKE_USER_ID)):
        r = client.get("/api/finance?view=goals")

    assert r.status_code == 200, r.text
    cg = {g["name"]: g for g in r.json()["closed_goals"]}
    assert cg["Samsung Flip"]["status"] == "achieved" and cg["Samsung Flip"]["saved"] == 100000
    assert cg["Ноутбук"]["status"] == "dropped"


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
         patch("miniapp.backend.routes.finance.budget_day_limit_from_plan",
               AsyncMock(return_value=4166)), \
         patch("miniapp.backend.routes.finance.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))), \
         patch("miniapp.backend.routes.finance.get_user_id",
               AsyncMock(return_value=FAKE_USER_ID)):
        r = client.get("/api/finance?view=today")

    assert r.status_code == 200
    data = r.json()
    assert "budget" in data
    assert data["budget"]["day"] == 4166
    assert data["budget"]["spent"] == 0
    assert data["budget"]["left"] == 4166
    assert data["budget"]["pct"] == 0


def test_finance_today_budget_reflects_spending(client):
    entries = [_budget_entry(2000, cat="🍜 Продукты", desc="магнит", eid="p1")]

    with patch("miniapp.backend.routes.finance._budget_repo.query",
               AsyncMock(return_value=entries)), \
         patch("miniapp.backend.routes.finance.budget_day_limit_from_plan",
               AsyncMock(return_value=4166)), \
         patch("miniapp.backend.routes.finance.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))), \
         patch("miniapp.backend.routes.finance.get_user_id",
               AsyncMock(return_value=FAKE_USER_ID)):
        r = client.get("/api/finance?view=today")

    assert r.status_code == 200
    b = r.json()["budget"]
    assert b["spent"] == 2000
    assert b["left"] == 2166
    assert b["pct"] == round(2000 / 4166 * 100)


def test_finance_today_excludes_parallel_categories_from_day_budget(client):
    """📦 Разовые / 🔒 Фикс — свой лимит на период, не дневная норма: видны в
    items, но не в total/budget.spent/left/pct."""
    entries = [
        _budget_entry(1000, cat="🍜 Продукты", desc="магнит", eid="p1"),
        _budget_entry(8000, cat="📦 Разовые", desc="коммуналка гай", eid="r1"),
        _budget_entry(20000, cat="🔒 Фикс", desc="аренда", eid="f1"),
    ]
    with patch("miniapp.backend.routes.finance._budget_repo.query",
               AsyncMock(return_value=entries)), \
         patch("miniapp.backend.routes.finance.budget_day_limit_from_plan",
               AsyncMock(return_value=4166)), \
         patch("miniapp.backend.routes.finance.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))), \
         patch("miniapp.backend.routes.finance.get_user_id",
               AsyncMock(return_value=FAKE_USER_ID)):
        r = client.get("/api/finance?view=today")

    data = r.json()
    # в дневной бюджет — только обычная категория
    assert data["total"] == 1000
    assert data["budget"]["spent"] == 1000
    assert data["budget"]["left"] == 4166 - 1000
    assert data["budget"]["pct"] == round(1000 / 4166 * 100)
    # но в истории транзакций видно всё
    cats = {i["cat"]["full"] for i in data["items"]}
    assert cats == {"🍜 Продукты", "📦 Разовые", "🔒 Фикс"}


# ── GET /api/finance/category — drill-down ──────────────────────────────────

def test_finance_category_drill_down(client):
    """Wave5.9: /api/finance/category возвращает список трат по категории."""
    entries = [
        _budget_entry(4500, cat="🏠 Жильё", desc="коммуналка", eid="e1", date="2026-04-02"),
        _budget_entry(800, cat="🏠 Жильё", desc="интернет", eid="e2", date="2026-04-18"),
    ]

    with patch("miniapp.backend.routes.finance._budget_repo.query",
               AsyncMock(return_value=entries)), \
         patch("miniapp.backend.routes.finance._mem_repo.find_by_category",
               AsyncMock(return_value=[])), \
         patch("miniapp.backend.routes.finance.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))), \
         patch("miniapp.backend.routes.finance.get_user_id",
               AsyncMock(return_value=FAKE_USER_ID)):
        r = client.get("/api/finance/category?cat=🏠%20Жильё&month=2026-04")

    assert r.status_code == 200
    data = r.json()
    assert data["cat"] == "🏠 Жильё"
    assert data["month"] == "2026-04"
    assert data["total"] == 5300
    assert data["count"] == 2
    # Сортировка по дате asc — от старой к новой.
    assert data["items"][0]["desc"] == "коммуналка"
    assert data["items"][1]["desc"] == "интернет"


def test_finance_category_same_day_sorted_by_creation_order(client):
    """Три траты ОДНОГО дня, разное время создания (эмулируется через id —
    autoincrement, совпадает с порядком создания) → идут от старой к новой
    по id, не в порядке "как вернул SQL" (тут — намеренно перемешанный)."""
    entries = [
        _budget_entry(300, cat="🏠 Жильё", desc="третья", eid="30", date="2026-04-10"),
        _budget_entry(100, cat="🏠 Жильё", desc="первая", eid="10", date="2026-04-10"),
        _budget_entry(200, cat="🏠 Жильё", desc="вторая", eid="20", date="2026-04-10"),
    ]

    with patch("miniapp.backend.routes.finance._budget_repo.query",
               AsyncMock(return_value=entries)), \
         patch("miniapp.backend.routes.finance._mem_repo.find_by_category",
               AsyncMock(return_value=[])), \
         patch("miniapp.backend.routes.finance.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))), \
         patch("miniapp.backend.routes.finance.get_user_id",
               AsyncMock(return_value=FAKE_USER_ID)):
        r = client.get("/api/finance/category?cat=🏠%20Жильё&month=2026-04")

    data = r.json()
    assert [i["desc"] for i in data["items"]] == ["первая", "вторая", "третья"]
    assert "_sort_key" not in data["items"][0]


# ── POST /api/expenses (deprecated alias) ───────────────────────────────────

def test_expense_create_uses_finance_add(client):
    from miniapp.backend.routes import writes as _writes_mod
    tz = 3
    today = _today_date(tz)
    fa = AsyncMock(return_value="fin-id")
    with patch.object(_writes_mod._fin_repo, "add", fa), \
         patch("miniapp.backend.routes.writes.today_user_tz",
               AsyncMock(return_value=(today, tz))), \
         patch("miniapp.backend.routes.writes.get_user_id",
               AsyncMock(return_value=FAKE_USER_ID)):
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
    from miniapp.backend.routes import writes as _writes_mod
    captured = {}

    async def fake_add(**kwargs):
        captured.update(kwargs)
        return "legacy-id"

    fa = AsyncMock(side_effect=fake_add)
    with patch.object(_writes_mod._fin_repo, "add", fa), \
         patch("miniapp.backend.routes.writes.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))), \
         patch("miniapp.backend.routes.writes.get_user_id",
               AsyncMock(return_value=FAKE_USER_ID)):
        r = client.post("/api/expenses", json={
            "amount": 200, "cat": "🍜 Продукты", "desc": "test",
        })

    assert r.status_code == 200
    assert r.json()["type"] == "expense"
    assert captured["type_"] == "💸 Расход"


# ── POST /api/finance (income/expense/practice_income) ──────────────────────

def test_finance_post_expense_routes_to_finance_add(client):
    from miniapp.backend.routes import writes as _writes_mod
    captured = {}

    async def fake_add(**kwargs):
        captured.update(kwargs)
        return "new-page-id"

    fa = AsyncMock(side_effect=fake_add)
    with patch.object(_writes_mod._fin_repo, "add", fa), \
         patch("miniapp.backend.routes.writes.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))), \
         patch("miniapp.backend.routes.writes.get_user_id",
               AsyncMock(return_value=FAKE_USER_ID)):
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
    from miniapp.backend.routes import writes as _writes_mod
    captured = {}

    async def fake_add(**kwargs):
        captured.update(kwargs)
        return "inc-id"

    fa = AsyncMock(side_effect=fake_add)
    with patch.object(_writes_mod._fin_repo, "add", fa), \
         patch("miniapp.backend.routes.writes.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))), \
         patch("miniapp.backend.routes.writes.get_user_id",
               AsyncMock(return_value=FAKE_USER_ID)):
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
    from miniapp.backend.routes import writes as _writes_mod
    captured = {}

    async def fake_add(**kwargs):
        captured.update(kwargs)
        return "practice-id"

    fa = AsyncMock(side_effect=fake_add)
    with patch.object(_writes_mod._fin_repo, "add", fa), \
         patch("miniapp.backend.routes.writes.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))), \
         patch("miniapp.backend.routes.writes.get_user_id",
               AsyncMock(return_value=FAKE_USER_ID)):
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
         patch("miniapp.backend.routes.writes.get_user_id",
               AsyncMock(return_value=FAKE_USER_ID)):
        r = client.post("/api/finance", json={
            "type": "expense",
            "amount": 100,
        })
    assert r.status_code == 400
    assert "cat is required" in r.json()["detail"]


def test_finance_bot_arcana_sets_arcana_label(client):
    """Регресс: body.bot=arcana → bot_label='🌒 Arcana' (arcana_pnl, не nexus_budget)."""
    from miniapp.backend.routes import writes as _writes_mod
    captured = {}

    async def fake_add(**kwargs):
        captured.update(kwargs)
        return "arc-id"

    fa = AsyncMock(side_effect=fake_add)
    with patch.object(_writes_mod._fin_repo, "add", fa), \
         patch("miniapp.backend.routes.writes.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))), \
         patch("miniapp.backend.routes.writes.get_user_id",
               AsyncMock(return_value=FAKE_USER_ID)):
        r = client.post("/api/finance", json={
            "type": "expense",
            "amount": 300,
            "cat": "🕯️ Расходники",
            "bot": "arcana",
        })

    assert r.status_code == 200, r.text
    assert captured["bot_label"] == "🌒 Arcana"   # → arcana_pnl
    assert captured["type_"] == "💸 Расход"
    assert captured["amount"] == 300

    # Контрольная группа: bot=nexus → BOT_NEXUS
    fa2 = AsyncMock(side_effect=fake_add)
    with patch.object(_writes_mod._fin_repo, "add", fa2), \
         patch("miniapp.backend.routes.writes.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))), \
         patch("miniapp.backend.routes.writes.get_user_id",
               AsyncMock(return_value=FAKE_USER_ID)):
        r2 = client.post("/api/finance", json={
            "type": "expense", "amount": 100, "cat": "🍜 Продукты", "bot": "nexus",
        })
    assert r2.status_code == 200, r2.text
    assert captured["bot_label"] == "☀️ Nexus"    # → nexus_budget


@pytest.mark.asyncio
async def test_finance_barter_guard_nexus_sanitizes_source():
    """FinanceRepo._guard_source: source=🔄 Бартер + BOT_NEXUS → sanitized to '💳 Карта'.

    Гард не даёт бартерному source попасть в nexus_budget.
    _nexus_repo и _arcana_repo — модульные синглтоны в finance_repo.
    """
    import core.repos.finance_repo as _fin_mod
    captured = {}

    async def fake_add_entry(**kwargs):
        captured.update(kwargs)
        return "123"

    with patch.object(_fin_mod._nexus_repo, "add_entry", side_effect=fake_add_entry):
        from core.repos.finance_repo import FinanceRepo
        await FinanceRepo().add(
            date="2026-06-17",
            amount=100.0,
            category="🍜 Продукты",
            type_="💸 Расход",
            source="🔄 Бартер",    # бартер с nexus → должен санитайзиться
            bot_label="☀️ Nexus",
        )

    assert captured["source"] == "💳 Карта"       # sanitized, не 🔄 Бартер


# ── GET /api/categories ──────────────────────────────────────────────────────

def test_categories_task_returns_merged_list(client):
    """GET /api/categories?type=task возвращает коды из PG task_category."""
    fake_cats = ["🐾 Коты", "💜 Люди", "🏠 Дом", "💼 Работа"]

    with patch("miniapp.backend.routes.categories._task_categories_sync",
               return_value=fake_cats):
        r = client.get("/api/categories?type=task")

    assert r.status_code == 200
    data = r.json()
    assert data["type"] == "task"
    assert data["categories"] == fake_cats


def test_categories_invalid_type(client):
    r = client.get("/api/categories?type=bogus")
    assert r.status_code == 400


def test_categories_income_returns_defaults_when_empty(client):
    """GET /api/categories?type=income возвращает INCOME_CATEGORIES из config."""
    r = client.get("/api/categories?type=income")

    assert r.status_code == 200
    cats = r.json()["categories"]
    assert "💰 Зарплата" in cats
    assert "💳 Прочее" in cats


def test_view_today_query_uses_today_as_date_to(client):
    """_view_today не включает завтрашние траты — date_to=today (#140)."""
    today_dt = _today_date()
    today_str = today_dt.isoformat()
    tomorrow_str = (today_dt + timedelta(days=1)).isoformat()

    query_mock = AsyncMock(return_value=[])

    with patch("miniapp.backend.routes.finance._budget_repo.query", query_mock), \
         patch("miniapp.backend.routes.finance.budget_day_limit_from_plan",
               AsyncMock(return_value=0)), \
         patch("miniapp.backend.routes.finance.today_user_tz",
               AsyncMock(return_value=(today_dt, 3))), \
         patch("miniapp.backend.routes.finance.get_user_id",
               AsyncMock(return_value=FAKE_USER_ID)):
        r = client.get("/api/finance?view=today")

    assert r.status_code == 200
    kw = query_mock.call_args.kwargs
    assert kw["date_to"] == today_str, f"date_to должен быть today={today_str!r}, не tomorrow={tomorrow_str!r}"
    assert kw["date_to"] != tomorrow_str


# ── POST /api/finance/goal{,/close,/contribute} (#44/#205) ───────────────────

def test_finance_goal_create(client):
    with patch("core.repos.pg_goals_repo._repo.upsert", AsyncMock()) as m_up, \
         patch("core.repos.pg_goals_repo._repo.set_status", AsyncMock()) as m_ss, \
         patch("miniapp.backend.routes.writes.notify_user", AsyncMock()), \
         patch("miniapp.backend.routes.writes.get_user_id", AsyncMock(return_value="u")):
        r = client.post("/api/finance/goal", json={"name": "Ноутбук", "target": 200000, "monthly": 15000})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "Ноутбук" and body["target"] == 200000 and body["monthly"] == 15000
    _, kw = m_up.await_args
    assert m_up.await_args.args[1] == "Ноутбук" and kw["target"] == 200000.0 and kw["monthly"] == 15000.0
    m_ss.assert_not_awaited()


def test_finance_goal_rename_drops_old(client):
    with patch("core.repos.pg_goals_repo._repo.upsert", AsyncMock()), \
         patch("core.repos.pg_goals_repo._repo.set_status", AsyncMock(return_value=True)) as m_ss, \
         patch("miniapp.backend.routes.writes.notify_user", AsyncMock()), \
         patch("miniapp.backend.routes.writes.get_user_id", AsyncMock(return_value="u")):
        r = client.post("/api/finance/goal", json={
            "name": "Новое имя", "target": 100000, "monthly": 0, "prev_name": "Старое имя",
        })
    assert r.status_code == 200, r.text
    m_ss.assert_awaited_once_with("u", "Старое имя", "dropped")


def test_finance_goal_close(client):
    with patch("core.repos.pg_goals_repo._repo.set_status", AsyncMock(return_value=True)) as m_ss, \
         patch("miniapp.backend.routes.writes.notify_user", AsyncMock()), \
         patch("miniapp.backend.routes.writes.get_user_id", AsyncMock(return_value="u")):
        r = client.post("/api/finance/goal/close", json={"name": "Ноутбук", "achieved": True})
    assert r.status_code == 200, r.text
    m_ss.assert_awaited_once_with("u", "Ноутбук", "achieved")


def test_finance_goal_close_404_when_missing(client):
    with patch("core.repos.pg_goals_repo._repo.set_status", AsyncMock(return_value=False)), \
         patch("miniapp.backend.routes.writes.get_user_id", AsyncMock(return_value="u")):
        r = client.post("/api/finance/goal/close", json={"name": "Нет такой"})
    assert r.status_code == 404


def test_finance_goal_contribute(client):
    with patch("core.repos.pg_goals_repo._repo.add_saved", AsyncMock(return_value=55000.0)) as m_add, \
         patch("miniapp.backend.routes.writes.notify_user", AsyncMock()), \
         patch("miniapp.backend.routes.writes.get_user_id", AsyncMock(return_value="u")):
        r = client.post("/api/finance/goal/contribute", json={"name": "Ноутбук", "amount": 5000})
    assert r.status_code == 200, r.text
    assert r.json()["saved"] == 55000
    m_add.assert_awaited_once_with("u", "Ноутбук", 5000.0)


def test_finance_goal_contribute_404(client):
    with patch("core.repos.pg_goals_repo._repo.add_saved", AsyncMock(return_value=None)), \
         patch("miniapp.backend.routes.writes.get_user_id", AsyncMock(return_value="u")):
        r = client.post("/api/finance/goal/contribute", json={"name": "Нет", "amount": 1})
    assert r.status_code == 404


def test_finance_view_goals_serializes_saved_and_progress(client):
    """#205: goals view отдаёт реальный saved + ETA от остатка."""
    from core.repos.pg_goals_repo import Goal
    tz = 3
    budget = {"доходы": [], "постоянные": [], "лимиты": [], "долги": [],
              "цели": [{"id": "7", "name": "X", "target": 100000, "saving": 8000,
                        "monthly": 8000, "saved": 40000}]}
    with patch("miniapp.backend.routes.finance.load_budget_data", AsyncMock(return_value=budget)), \
         patch("core.repos.pg_goals_repo._repo.list_closed", AsyncMock(return_value=[])), \
         patch("core.repos.pg_debts_repo._repo.list_closed", AsyncMock(return_value=[])), \
         patch("core.repos.pg_debts_repo._repo.list_active", AsyncMock(return_value=[])), \
         patch("miniapp.backend.routes.finance._find_debt_taken_dates", AsyncMock(return_value={})), \
         patch("miniapp.backend.routes.finance.today_user_tz", AsyncMock(return_value=(_today_date(tz), tz))), \
         patch("miniapp.backend.routes.finance.get_user_id", AsyncMock(return_value="")):
        r = client.get("/api/finance?view=goals")
    assert r.status_code == 200, r.text
    g = r.json()["goals"][0]
    assert g["saved"] == 40000 and g["target"] == 100000 and g["id"] == "7"
    assert "fact" not in g and "key" not in g


# ── POST /api/finance/debt — 4 directions + close (#123) ─────────────────────

def test_debt_borrowed_creates_i_owe(client):
    with patch("core.repos.pg_debts_repo._repo.upsert", AsyncMock()) as m_up, \
         patch("miniapp.backend.routes.writes.notify_user", AsyncMock()), \
         patch("miniapp.backend.routes.writes.get_user_id", AsyncMock(return_value="u")):
        r = client.post("/api/finance/debt", json={"name": "Аня", "amount": 5000, "deadline": "до июня"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "direction": "borrowed", "kind": "i_owe"}
    args, kw = m_up.await_args
    assert args[1] == "Аня" and args[2] == "i_owe"
    assert kw["amount"] == 5000.0 and kw["deadline"] == "до июня"


def test_debt_lent_creates_they_owe(client):
    with patch("core.repos.pg_debts_repo._repo.upsert", AsyncMock()) as m_up, \
         patch("miniapp.backend.routes.writes.notify_user", AsyncMock()), \
         patch("miniapp.backend.routes.writes.get_user_id", AsyncMock(return_value="u")):
        r = client.post("/api/finance/debt", json={"name": "Петя", "amount": 3000, "direction": "lent"})
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "they_owe"
    assert m_up.await_args.args[2] == "they_owe"


def test_debt_repaid_reduces_and_reports_remaining(client):
    with patch("core.repos.pg_debts_repo._repo.reduce_amount",
               AsyncMock(return_value=(2000.0, False, 0.0))) as m_red, \
         patch("miniapp.backend.routes.writes.notify_user", AsyncMock()), \
         patch("miniapp.backend.routes.writes.get_user_id", AsyncMock(return_value="u")):
        r = client.post("/api/finance/debt", json={"name": "Аня", "amount": 3000, "direction": "repaid"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["closed"] is False and body["remaining"] == 2000
    assert m_red.await_args.args[1:] == ("i_owe", "Аня", 3000.0)


def test_debt_repaid_404_when_no_such_debt(client):
    with patch("core.repos.pg_debts_repo._repo.reduce_amount", AsyncMock(return_value=None)), \
         patch("miniapp.backend.routes.writes.get_user_id", AsyncMock(return_value="u")):
        r = client.post("/api/finance/debt", json={"name": "Нет", "amount": 100, "direction": "repaid"})
    assert r.status_code == 404


def test_debt_received_reduces_they_owe(client):
    with patch("core.repos.pg_debts_repo._repo.reduce_amount",
               AsyncMock(return_value=(0.0, True, 500.0))) as m_red, \
         patch("miniapp.backend.routes.writes.notify_user", AsyncMock()), \
         patch("miniapp.backend.routes.writes.get_user_id", AsyncMock(return_value="u")):
        r = client.post("/api/finance/debt", json={"name": "Петя", "amount": 3500, "direction": "received"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["closed"] is True and body["overpaid"] == 500
    assert m_red.await_args.args[1] == "they_owe"


def test_debt_bad_direction_400(client):
    with patch("miniapp.backend.routes.writes.get_user_id", AsyncMock(return_value="u")):
        r = client.post("/api/finance/debt", json={"name": "X", "amount": 1, "direction": "nope"})
    assert r.status_code == 400


def test_debt_close_deactivates(client):
    with patch("core.repos.pg_debts_repo._repo.deactivate", AsyncMock(return_value=True)) as m_de, \
         patch("miniapp.backend.routes.writes.notify_user", AsyncMock()), \
         patch("miniapp.backend.routes.writes.get_user_id", AsyncMock(return_value="u")):
        r = client.post("/api/finance/debt/close", json={"name": "Аня", "kind": "i_owe"})
    assert r.status_code == 200, r.text
    m_de.assert_awaited_once_with("u", "i_owe", "Аня")


def test_debt_close_404_when_missing(client):
    with patch("core.repos.pg_debts_repo._repo.deactivate", AsyncMock(return_value=False)), \
         patch("miniapp.backend.routes.writes.get_user_id", AsyncMock(return_value="u")):
        r = client.post("/api/finance/debt/close", json={"name": "Нет", "kind": "they_owe"})
    assert r.status_code == 404


def test_finance_view_goals_includes_incoming_debts(client):
    from core.repos.pg_debts_repo import Debt
    tz = 3
    inc = Debt(name="Петя", kind="they_owe", amount=4000, is_active=True)
    budget = {"доходы": [], "постоянные": [], "лимиты": [], "долги": [], "цели": []}
    with patch("miniapp.backend.routes.finance.load_budget_data", AsyncMock(return_value=budget)), \
         patch("miniapp.backend.routes.finance._mem_repo.find_by_category", AsyncMock(return_value=[])), \
         patch("core.repos.pg_debts_repo._repo.list_closed", AsyncMock(return_value=[])), \
         patch("core.repos.pg_debts_repo._repo.list_active", AsyncMock(return_value=[inc])), \
         patch("miniapp.backend.routes.finance._find_debt_taken_dates", AsyncMock(return_value={})), \
         patch("miniapp.backend.routes.finance.today_user_tz", AsyncMock(return_value=(_today_date(tz), tz))), \
         patch("miniapp.backend.routes.finance.get_user_id", AsyncMock(return_value="u")):
        r = client.get("/api/finance?view=goals")
    assert r.status_code == 200, r.text
    di = r.json()["debts_incoming"]
    assert len(di) == 1 and di[0]["name"] == "Петя" and di[0]["total"] == 4000


def test_finance_cushion_deposit_increments_balance(client):
    with patch("core.repos.pg_cushion_repo._repo.add_to_balance",
               AsyncMock(return_value=47000.0)) as m_add, \
         patch("miniapp.backend.routes.writes.notify_user", AsyncMock()), \
         patch("miniapp.backend.routes.writes.get_user_id", AsyncMock(return_value="u")):
        r = client.post("/api/finance/cushion/deposit",
                        json={"amount": 5000, "source": "debt_overpaid", "note": "переплата по долгу"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "amount": 5000, "balance": 47000}
    _, kw = m_add.await_args
    assert kw["source"] == "debt_overpaid"


def test_debt_repaid_overpaid_reported(client):
    """repaid с переплатой → фронт получает overpaid, дальше сам решает про подушку."""
    with patch("core.repos.pg_debts_repo._repo.reduce_amount",
               AsyncMock(return_value=(0.0, True, 1500.0))), \
         patch("miniapp.backend.routes.writes.notify_user", AsyncMock()), \
         patch("miniapp.backend.routes.writes.get_user_id", AsyncMock(return_value="u")):
        r = client.post("/api/finance/debt", json={"name": "Аня", "amount": 6500, "direction": "repaid"})
    assert r.status_code == 200, r.text
    assert r.json()["closed"] is True and r.json()["overpaid"] == 1500
