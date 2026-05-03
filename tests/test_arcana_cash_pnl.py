"""tests/test_arcana_cash_pnl.py — касса Арканы + pay_salary."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id


FAKE_TG_ID = 67686090
FAKE_NOTION_USER = "user-notion-id-42"


@pytest.fixture
def client():
    app.dependency_overrides[current_user_id] = lambda: FAKE_TG_ID
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _client_page(cid: str, ctype: str = "🤝 Платный") -> dict:
    return {"id": cid, "properties": {"Тип клиента": {"select": {"name": ctype}}}}


def _session_page(pid: str, paid: float, price: float, client_id: str = "cli-1",
                   date: str = "2026-05-10") -> dict:
    return {
        "id": pid,
        "properties": {
            "Сумма": {"number": price},
            "Оплачено": {"number": paid},
            "Дата": {"date": {"start": date}},
            "👥 Клиенты": {"relation": [{"id": client_id}]},
        },
    }


def _ritual_page(pid: str, paid: float, price: float, client_id: str = "cli-1",
                  date: str = "2026-05-10") -> dict:
    return {
        "id": pid,
        "properties": {
            "Цена за ритуал": {"number": price},
            "Оплачено": {"number": paid},
            "Дата": {"date": {"start": date}},
            "👥 Клиенты": {"relation": [{"id": client_id}]},
        },
    }


def _finance_record(amount: float, date: str = "2026-05-10",
                    type_: str = "💸 Расход", category: str = "🕯️ Расходники",
                    bot: str = "🌒 Arcana") -> dict:
    return {
        "id": f"fin-{amount}-{date}",
        "properties": {
            "Сумма": {"number": amount},
            "Дата": {"date": {"start": date}},
            "Тип": {"select": {"name": type_}},
            "Категория": {"select": {"name": category}},
            "Бот": {"select": {"name": bot}},
        },
    }


@pytest.mark.asyncio
async def test_compute_pnl_excludes_self_client_and_subtracts_salary():
    from core.cash_register import compute_pnl

    clients = [_client_page("cli-1"), _client_page("self-1", "🌟 Self")]
    sessions = [
        _session_page("s1", paid=5000, price=5000, client_id="cli-1"),
        _session_page("s2", paid=3500, price=3500, client_id="cli-1"),
        _session_page("self-s", paid=999, price=999, client_id="self-1"),
    ]
    rituals = [_ritual_page("r1", paid=5000, price=5000, client_id="cli-1")]
    finance = [
        _finance_record(1800, category="🕯️ Расходники"),
        _finance_record(500, category="📚 Хобби/Учеба"),
    ]
    salary = [_finance_record(20000, category="💰 Зарплата", type_="💰 Доход", bot="☀️ Nexus")]

    async def _qp(db_id, **kwargs):
        # порядок: clients_map, salary
        if not hasattr(_qp, "i"):
            _qp.i = 0
        seq = [clients, salary]
        page = seq[_qp.i]
        _qp.i += 1
        return page

    with patch("core.cash_register.query_pages", AsyncMock(side_effect=_qp)), \
         patch("core.cash_register.sessions_all", AsyncMock(return_value=sessions)), \
         patch("core.cash_register.rituals_all", AsyncMock(return_value=rituals)), \
         patch("core.cash_register._count_open_barter", AsyncMock(return_value=2)), \
         patch("core.notion_client.arcana_finance_summary",
               AsyncMock(return_value=finance)):
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
    fake_pnl = {"cash_balance": 30000, "income_month": 0, "expenses_month": 0,
                "profit_month": 0, "salary_month": 0, "salary_lifetime": 0,
                "income_breakdown": {}, "expenses_by_category": [],
                "debt_money": 0, "barter_open_count": 0,
                "period": {"year": 2026, "month": 5}}
    with patch("miniapp.backend.routes.arcana_finance.compute_pnl",
               AsyncMock(return_value=fake_pnl)), \
         patch("miniapp.backend.routes.arcana_finance.finance_add",
               AsyncMock(return_value="fin-XX")) as fa, \
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
    fake_pnl = {"cash_balance": 5000, "income_month": 0, "expenses_month": 0,
                "profit_month": 0, "salary_month": 0, "salary_lifetime": 0,
                "income_breakdown": {}, "expenses_by_category": [],
                "debt_money": 0, "barter_open_count": 0,
                "period": {"year": 2026, "month": 5}}
    with patch("miniapp.backend.routes.arcana_finance.compute_pnl",
               AsyncMock(return_value=fake_pnl)), \
         patch("miniapp.backend.routes.arcana_finance.finance_add",
               AsyncMock(return_value="fin-X")) as fa, \
         patch("miniapp.backend.routes.arcana_finance.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/arcana/finance/pay_salary", json={"amount": 20000})
    body = r.json()
    assert body["ok"] is False
    assert body["warning"] == "low_cash"
    assert body["cash_balance"] == 5000
    fa.assert_not_awaited()
