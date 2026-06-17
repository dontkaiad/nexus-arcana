"""tests/test_arcana_cash_pnl.py — касса Арканы + pay_salary."""
from __future__ import annotations

from decimal import Decimal
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id
from arcana.repos.sessions_repo import TripletEntry
from arcana.repos.rituals_repo import Ritual
from arcana.repos.clients_repo import Client
from core.repos.pg_finance_repo import PnlEntry, BudgetEntry


FAKE_TG_ID = 67686090
FAKE_NOTION_USER = "user-notion-id-42"


@pytest.fixture
def client():
    app.dependency_overrides[current_user_id] = lambda: FAKE_TG_ID
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _session(pid, paid, price, client_id="cli-1", date="2026-05-10"):
    return TripletEntry(
        id=pid, question="", cards="", interpretation="", deck="", session_name="",
        client_id=str(client_id), date=date,
        amount=Decimal(str(price)), paid=Decimal(str(paid)),
    )


def _ritual(pid, paid, price, client_id="cli-1", date="2026-05-10"):
    return Ritual(
        id=pid, name="Ритуал",
        client_id=str(client_id),
        date=datetime.strptime(date, "%Y-%m-%d") if date else None,
        paid=Decimal(str(paid)),
        price=Decimal(str(price)),
    )


def _client(cid, type_code="paid"):
    return Client(id=str(cid), name="", contact="", request="", notes="", since="",
                  type_code=type_code)


def _finance(amount, *, date="2026-05-10", type_="💸 Расход", category="🕯️ Расходники"):
    return PnlEntry(amount=amount, date=date, type_=type_, category=category)


def _salary(amount, date="2026-05-10"):
    return BudgetEntry(amount=amount, date=date, type_="💰 Доход", category="💰 Зарплата")


@pytest.mark.asyncio
async def test_compute_pnl_excludes_self_client_and_subtracts_salary():
    from core.cash_register import compute_pnl

    clients = [_client("cli-1"), _client("self-1", type_code="self")]
    sessions = [
        _session("s1", paid=5000, price=5000, client_id="cli-1"),
        _session("s2", paid=3500, price=3500, client_id="cli-1"),
        _session("self-s", paid=999, price=999, client_id="self-1"),
    ]
    rituals = [_ritual("r1", paid=5000, price=5000, client_id="cli-1")]
    finance = [_finance(1800, category="🕯️ Расходники"), _finance(500, category="📚 Хобби/Учеба")]
    salary = [_salary(20000)]

    with patch("core.cash_register._load_clients", AsyncMock(return_value=clients)), \
         patch("core.cash_register._load_sessions", AsyncMock(return_value=sessions)), \
         patch("core.cash_register._load_rituals", AsyncMock(return_value=rituals)), \
         patch("core.cash_register._load_arcana_finance", AsyncMock(return_value=finance)), \
         patch("core.cash_register._load_salary_records", AsyncMock(return_value=salary)), \
         patch("core.cash_register._count_open_barter", AsyncMock(return_value=2)):
        pnl = await compute_pnl(FAKE_NOTION_USER, 2026, 5)

    # Self исключён → доход 5000+3500+5000 = 13500
    assert pnl["income_month"] == 13500
    assert pnl["income_breakdown"]["sessions"]["amount"] == 8500
    assert pnl["income_breakdown"]["rituals"]["amount"] == 5000
    # Расходы 1800+500
    assert pnl["expenses_month"] == 2300
    cats = {c["name"]: c["amount"] for c in pnl["expenses_by_category"]}
    assert cats["🕯️ Расходники"] == 1800
    # Прибыль = 13500 - 2300 = 11200
    assert pnl["profit_month"] == 11200
    # Касса = 13500 - 2300 - 20000 = -8800 (lifetime, в этом тесте равно month)
    assert pnl["cash_balance"] == -8800
    assert pnl["salary_month"] == 20000
    assert pnl["barter_open_count"] == 2


def test_pay_salary_creates_finance_with_nexus_bot_and_salary_category(client):
    from miniapp.backend.routes import arcana_finance
    fake_pnl = {"cash_balance": 30000, "income_month": 0, "expenses_month": 0,
                "profit_month": 0, "salary_month": 0, "salary_lifetime": 0,
                "income_breakdown": {}, "expenses_by_category": [],
                "debt_money": 0, "barter_open_count": 0,
                "period": {"year": 2026, "month": 5}}
    fa = AsyncMock(return_value="fin-XX")
    with patch("miniapp.backend.routes.arcana_finance.compute_pnl",
               AsyncMock(return_value=fake_pnl)), \
         patch.object(arcana_finance._fin_repo, "add", fa), \
         patch("miniapp.backend.routes.arcana_finance.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/arcana/finance/pay_salary", json={"amount": 20000})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["finance_id"] == "fin-XX"
    assert body["cash_balance_after"] == 10000
    fa.assert_awaited_once()
    kwargs = fa.await_args.kwargs
    assert kwargs["bot_label"] == "☀️ Nexus"
    assert kwargs["category"] == "💰 Зарплата"
    assert kwargs["type_"] == "💰 Доход"
    assert kwargs["amount"] == 20000.0


def test_pay_salary_warns_when_cash_too_low(client):
    from miniapp.backend.routes import arcana_finance
    fake_pnl = {"cash_balance": 5000, "income_month": 0, "expenses_month": 0,
                "profit_month": 0, "salary_month": 0, "salary_lifetime": 0,
                "income_breakdown": {}, "expenses_by_category": [],
                "debt_money": 0, "barter_open_count": 0,
                "period": {"year": 2026, "month": 5}}
    fa = AsyncMock(return_value="fin-X")
    with patch("miniapp.backend.routes.arcana_finance.compute_pnl",
               AsyncMock(return_value=fake_pnl)), \
         patch.object(arcana_finance._fin_repo, "add", fa), \
         patch("miniapp.backend.routes.arcana_finance.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/arcana/finance/pay_salary", json={"amount": 20000})
    body = r.json()
    assert body["ok"] is False
    assert body["warning"] == "low_cash"
    assert body["cash_balance"] == 5000
    fa.assert_not_awaited()
