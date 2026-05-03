"""tests/test_e2e_cash_barter_inventory.py — e2e волны касса/выплата/бартер/инвентарь.

Покрытие:
 1. Полный P&L: сеанс 5000 + ритуал 3000 (не-self), расход 500, зарплата 2000
    → income 8000, expense 500, profit 7500, salary 2000, cash 5500.
 2. Self-client исключён.
 3. Двойная выплата 1000+1000 → cash −2000, salary 2000.
 4. Pay_salary force: cash 500, amount 1000 без force → warning; с force → выплата.
 5. Бартер listing: 3 пункта чеклиста (2 Done, 1 Not started) → only_open=1.
 6. Бартер toggle через /api/lists/{id}/done.
 7. Инвентарь add: POST /api/lists bot=arcana type=inv → пишет Бот=🌒 Arcana, Тип=📦 Инвентарь.
 8. Бот: «выплати 20к», «зарплата 20000», «выплати себе 20к» → все 20000.
 9. /finance в Аркане рендерит P&L формат с inline-кнопкой «💸 Выплатить».

Notion-вызовы мокаются на самом верхнем уровне (query_pages / sessions_all /
rituals_all / arcana_finance_summary / finance_add) — реальной БД нет в CI.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

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


# ── Page fabricators ────────────────────────────────────────────────────────

def _client_page(cid: str, ctype: str = "🤝 Платный") -> dict:
    return {"id": cid, "properties": {"Тип клиента": {"select": {"name": ctype}}}}


def _session(pid: str, paid: float, price: float, client_id: str = "cli-1",
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


def _ritual(pid: str, paid: float, price: float, client_id: str = "cli-1",
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


def _finance(amount: float, *, date: str = "2026-05-10",
             type_: str = "💸 Расход", category: str = "🕯️ Расходники",
             bot: str = "🌒 Arcana") -> dict:
    return {
        "id": f"fin-{amount}-{date}-{category}",
        "properties": {
            "Сумма": {"number": amount},
            "Дата": {"date": {"start": date}},
            "Тип": {"select": {"name": type_}},
            "Категория": {"select": {"name": category}},
            "Бот": {"select": {"name": bot}},
        },
    }


def _list_item(pid: str, name: str, *, status: str = "Not started",
               group: str = "", category: str = "🔄 Бартер") -> dict:
    return {
        "id": pid,
        "properties": {
            "Название": {"title": [{"plain_text": name, "text": {"content": name}}]},
            "Тип": {"select": {"name": "📋 Чеклист"}},
            "Категория": {"select": {"name": category}},
            "Бот": {"select": {"name": "🌒 Arcana"}},
            "Статус": {"status": {"name": status}},
            "Группа": {"rich_text": [{"plain_text": group, "text": {"content": group}}]},
        },
    }


# ── Helper to call compute_pnl with explicit Notion mocks ────────────────────

def _patch_pnl_inputs(*, clients, sessions, rituals, arcana_finance, salary,
                       barter_open=0):
    """Контекст с патчами всех Notion-зависимостей compute_pnl."""
    async def _qp(db_id, **kwargs):
        # порядок вызовов внутри compute_pnl:
        #   _load_clients_map, _load_salary_records
        if not hasattr(_qp, "i"):
            _qp.i = 0
        seq = [clients, salary]
        page = seq[_qp.i]
        _qp.i += 1
        return page

    return [
        patch("core.cash_register.query_pages", AsyncMock(side_effect=_qp)),
        patch("core.cash_register.sessions_all", AsyncMock(return_value=sessions)),
        patch("core.cash_register.rituals_all", AsyncMock(return_value=rituals)),
        patch("core.cash_register._count_open_barter",
              AsyncMock(return_value=barter_open)),
        patch("core.notion_client.arcana_finance_summary",
              AsyncMock(return_value=arcana_finance)),
    ]


# ═══════════════════════════════════════════════════════════════════════════
# 1. Полный P&L флоу
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_full_pnl_flow():
    from core.cash_register import compute_pnl
    clients = [_client_page("cli-1")]
    sessions = [_session("s1", paid=5000, price=5000, client_id="cli-1")]
    rituals = [_ritual("r1", paid=3000, price=3000, client_id="cli-1")]
    arcana_finance = [_finance(500, category="🕯️ Расходники")]
    salary = [_finance(2000, category="💰 Зарплата", type_="💰 Доход", bot="☀️ Nexus")]

    cms = _patch_pnl_inputs(clients=clients, sessions=sessions, rituals=rituals,
                             arcana_finance=arcana_finance, salary=salary,
                             barter_open=3)
    for cm in cms: cm.start()
    try:
        pnl = await compute_pnl(FAKE_NOTION_USER, 2026, 5)
    finally:
        for cm in cms: cm.stop()

    assert pnl["income_month"] == 8000
    assert pnl["income_breakdown"]["sessions"]["amount"] == 5000
    assert pnl["income_breakdown"]["rituals"]["amount"] == 3000
    assert pnl["expenses_month"] == 500
    assert pnl["profit_month"] == 7500
    assert pnl["salary_month"] == 2000
    assert pnl["salary_lifetime"] == 2000
    # cash = доход_lifetime − расход_lifetime − salary_lifetime = 8000-500-2000
    assert pnl["cash_balance"] == 5500
    assert pnl["debt_money"] == 0
    assert pnl["barter_open_count"] == 3


# ═══════════════════════════════════════════════════════════════════════════
# 2. Self-client исключение
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_self_client_excluded_from_pnl():
    from core.cash_register import compute_pnl
    clients = [_client_page("self-1", "🌟 Self"), _client_page("cli-2")]
    sessions = [
        _session("self-s", paid=10000, price=10000, client_id="self-1"),
        _session("paid-s", paid=4000, price=4000, client_id="cli-2"),
    ]
    rituals = []

    cms = _patch_pnl_inputs(clients=clients, sessions=sessions, rituals=rituals,
                             arcana_finance=[], salary=[])
    for cm in cms: cm.start()
    try:
        pnl = await compute_pnl(FAKE_NOTION_USER, 2026, 5)
    finally:
        for cm in cms: cm.stop()

    # Self не вошёл — только 4000 от cli-2.
    assert pnl["income_month"] == 4000
    assert pnl["cash_balance"] == 4000


# ═══════════════════════════════════════════════════════════════════════════
# 3. Двойная выплата pay_salary 1000 + 1000
# ═══════════════════════════════════════════════════════════════════════════

def test_pay_salary_twice_subtracts_from_cash(client):
    """Симулируем: до выплаты cash=10000. После первой 9000, после второй 8000.
    finance_add вызван дважды с amount=1000 и bot=Nexus, cat=Зарплата."""
    state = {"cash": 10000, "salary_lifetime": 0}

    async def fake_pnl(user, y, m):
        return {
            "cash_balance": state["cash"],
            "income_month": 0, "expenses_month": 0, "profit_month": 0,
            "salary_month": state["salary_lifetime"],
            "salary_lifetime": state["salary_lifetime"],
            "income_breakdown": {}, "expenses_by_category": [],
            "debt_money": 0, "barter_open_count": 0,
            "period": {"year": y, "month": m},
        }

    fa = AsyncMock(return_value="fin-OK")
    with patch("miniapp.backend.routes.arcana_finance.compute_pnl", AsyncMock(side_effect=fake_pnl)), \
         patch("miniapp.backend.routes.arcana_finance.finance_add", fa), \
         patch("miniapp.backend.routes.arcana_finance.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r1 = client.post("/api/arcana/finance/pay_salary", json={"amount": 1000})
        assert r1.json()["cash_balance_after"] == 9000
        state["cash"] = 9000
        state["salary_lifetime"] = 1000
        r2 = client.post("/api/arcana/finance/pay_salary", json={"amount": 1000})
        assert r2.json()["cash_balance_after"] == 8000
    assert fa.await_count == 2
    for call in fa.await_args_list:
        assert call.kwargs["bot_label"] == "☀️ Nexus"
        assert call.kwargs["category"] == "💰 Зарплата"
        assert call.kwargs["amount"] == 1000.0


# ═══════════════════════════════════════════════════════════════════════════
# 4. Pay_salary force when cash < amount
# ═══════════════════════════════════════════════════════════════════════════

def test_pay_salary_force_overrides_low_cash(client):
    fake_pnl = {"cash_balance": 500, "income_month": 0, "expenses_month": 0,
                "profit_month": 0, "salary_month": 0, "salary_lifetime": 0,
                "income_breakdown": {}, "expenses_by_category": [],
                "debt_money": 0, "barter_open_count": 0,
                "period": {"year": 2026, "month": 5}}
    fa = AsyncMock(return_value="fin-FORCE")
    with patch("miniapp.backend.routes.arcana_finance.compute_pnl",
               AsyncMock(return_value=fake_pnl)), \
         patch("miniapp.backend.routes.arcana_finance.finance_add", fa), \
         patch("miniapp.backend.routes.arcana_finance.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        # без force — warning
        r1 = client.post("/api/arcana/finance/pay_salary", json={"amount": 1000})
        body1 = r1.json()
        assert body1["ok"] is False
        assert body1["warning"] == "low_cash"
        fa.assert_not_awaited()
        # с force — выплата проходит
        r2 = client.post("/api/arcana/finance/pay_salary",
                          json={"amount": 1000, "force": True})
        body2 = r2.json()
        assert body2["ok"] is True
        assert body2["cash_balance_after"] == -500  # 500 - 1000
        fa.assert_awaited_once()


# ═══════════════════════════════════════════════════════════════════════════
# 5. Бартер listing: 3 пункта (2 Done, 1 Not started) → open_count=1
# ═══════════════════════════════════════════════════════════════════════════

def test_barter_listing_filters_only_open(client):
    pages_open = [
        _list_item("b3", "поездка в беларусь", status="Not started",
                    group="приворот — Оля"),
    ]
    with patch("miniapp.backend.routes.arcana_barter.query_pages",
               AsyncMock(return_value=pages_open)), \
         patch("miniapp.backend.routes.arcana_barter.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/arcana/barter?only_open=true")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["open_count"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["name"] == "поездка в беларусь"
    assert body["items"][0]["done"] is False
    assert body["by_group"][0]["group"] == "приворот — Оля"


# ═══════════════════════════════════════════════════════════════════════════
# 6. Бартер toggle через /api/lists/{id}/done
# ═══════════════════════════════════════════════════════════════════════════

def test_barter_toggle_done_via_lists_endpoint(client):
    page = _list_item("b-toggle", "блок сигарет", status="Not started",
                      group="приворот — Оля")
    with patch("miniapp.backend.routes.writes.get_page",
               AsyncMock(return_value=page)), \
         patch("miniapp.backend.routes.writes.update_page",
               AsyncMock(return_value=None)) as up, \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/lists/b-toggle/done")
    assert r.status_code == 200, r.text
    up.assert_awaited()
    written = up.await_args.args[1]
    assert "Статус" in written
    assert written["Статус"]["status"]["name"] == "Done"


# ═══════════════════════════════════════════════════════════════════════════
# 7. Inventory add: POST /api/lists bot=arcana → Бот=🌒 Arcana, Тип=📦 Инвентарь
# ═══════════════════════════════════════════════════════════════════════════

def test_inventory_add_writes_arcana_label_and_inv_type(client):
    with patch("miniapp.backend.routes.writes.page_create",
               AsyncMock(return_value="inv-NEW")) as pc, \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/lists", json={
            "type": "inv", "name": "соль", "qty": 200,
            "cat": "🕯️ Расходники", "bot": "arcana",
        })
    assert r.status_code == 200
    assert r.json()["id"] == "inv-NEW"
    props = pc.await_args.args[1]
    assert props["Бот"]["select"]["name"] == "🌒 Arcana"
    assert props["Тип"]["select"]["name"] == "📦 Инвентарь"
    assert props["Категория"]["select"]["name"] == "🕯️ Расходники"
    assert props["Количество"]["number"] == 200.0


# ═══════════════════════════════════════════════════════════════════════════
# 8. Бот: разные формулировки выплаты → одинаковые 20000
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("phrase", [
    "выплати 20к",
    "зарплата 20000",
    "выплати себе 20к",
])
@pytest.mark.asyncio
async def test_bot_pay_self_parses_amount(phrase):
    from arcana.handlers.finance import handle_pay_self

    msg = MagicMock()
    msg.from_user.id = 1
    msg.answer = AsyncMock()

    fake_pnl = {"cash_balance": 100000, "income_month": 0, "expenses_month": 0,
                "profit_month": 0, "salary_month": 0, "salary_lifetime": 0,
                "income_breakdown": {}, "expenses_by_category": [],
                "debt_money": 0, "barter_open_count": 0,
                "period": {"year": 2026, "month": 5}}

    fa = AsyncMock(return_value="fin-PHRASE")
    with patch("arcana.handlers.finance.compute_pnl",
               AsyncMock(return_value=fake_pnl)), \
         patch("arcana.handlers.finance.finance_add", fa):
        await handle_pay_self(msg, phrase, user_notion_id=FAKE_NOTION_USER)

    fa.assert_awaited_once()
    kw = fa.await_args.kwargs
    assert kw["amount"] == 20000.0
    assert kw["bot_label"] == "☀️ Nexus"
    assert kw["category"] == "💰 Зарплата"
    msg.answer.assert_awaited()


# ═══════════════════════════════════════════════════════════════════════════
# 9. /finance в Аркане рендерит P&L + кнопку «💸 Выплатить»
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_finance_handler_renders_pnl_with_pay_button():
    from arcana.handlers.finance import handle_arcana_finance

    fake_pnl = {
        "cash_balance": 36700,
        "income_month": 13500,
        "income_breakdown": {
            "sessions": {"amount": 8500, "count": 3},
            "rituals": {"amount": 5000, "count": 1},
        },
        "expenses_month": 2300,
        "expenses_by_category": [
            {"name": "🕯️ Расходники", "amount": 1800},
            {"name": "📚 Хобби/Учеба", "amount": 500},
        ],
        "profit_month": 11200,
        "salary_month": 20000,
        "salary_lifetime": 20000,
        "debt_money": 5000,
        "barter_open_count": 4,
        "period": {"year": 2026, "month": 5},
    }

    msg = MagicMock()
    msg.from_user.id = FAKE_TG_ID
    msg.answer = AsyncMock()

    with patch("arcana.handlers.finance.compute_pnl",
               AsyncMock(return_value=fake_pnl)), \
         patch("core.shared_handlers.get_user_tz",
               AsyncMock(return_value=3)):
        await handle_arcana_finance(msg, user_notion_id=FAKE_NOTION_USER, text="")

    msg.answer.assert_awaited()
    args, kwargs = msg.answer.await_args
    body = args[0]
    # формат
    assert "АРКАНА" in body
    assert "13,500₽" in body
    assert "Сеансы: 8,500₽" in body
    assert "Ритуалы: 5,000₽" in body
    assert "🕯️ Расходники: 1,800₽" in body
    assert "Прибыль" in body and "11,200₽" in body
    assert "Выплачено себе" in body and "20,000₽" in body
    assert "В кассе" in body and "36,700₽" in body
    assert "5,000₽" in body and "4 бартер" in body
    # inline-кнопка
    kb = kwargs.get("reply_markup")
    assert kb is not None
    flat = [b for row in kb.inline_keyboard for b in row]
    assert any("Выплатить" in b.text for b in flat)
    assert any(b.callback_data == "arc_pay_self" for b in flat)
