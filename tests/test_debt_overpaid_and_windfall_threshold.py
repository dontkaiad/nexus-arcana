"""tests/test_debt_overpaid_and_windfall_threshold.py

Часть 1: reduce_amount() больше не теряет переплату — overpaid проброшен
через _partial_debt_payment/_partial_they_owe_payment до обоих текстовых
хендлеров ("отдала X" / they_owe), которые предупреждают об излишке кнопками.

Часть 2: _distribute_windfall_income с amount >= WINDFALL_MANUAL_THRESHOLD
НЕ распределяет автоматически — показывает предпросмотр + кнопки ручного
выбора; каждая кнопка выполняет своё действие.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nexus.handlers import finance


def _msg(uid: int = 42):
    m = MagicMock()
    m.from_user.id = uid
    m.answer = AsyncMock()
    return m


def _call(uid: int = 42, data: str = ""):
    c = MagicMock()
    c.from_user.id = uid
    c.data = data
    c.message = MagicMock()
    c.message.edit_text = AsyncMock()
    c.answer = AsyncMock()
    return c


# ── Часть 1: overpaid не теряется ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_partial_debt_payment_returns_overpaid_tuple():
    """reduce_amount с payment > amount → overpaid корректно посчитан и
    возвращён, new_amount=0, closed=True (интеграционно, через реальный
    _reduce_amount_sync/SQLite — не мок)."""
    import sqlalchemy as sa
    from sqlalchemy.pool import StaticPool
    import core.repos.pg_debts_repo as drmod

    eng = sa.create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                          poolclass=StaticPool)
    with eng.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE debts (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_notion_id TEXT NOT NULL DEFAULT '', name TEXT NOT NULL, "
            "kind TEXT NOT NULL DEFAULT 'i_owe', amount REAL NOT NULL, "
            "deadline TEXT, strategy TEXT, monthly_payment REAL NOT NULL DEFAULT 0, "
            "is_active INTEGER NOT NULL DEFAULT 1, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        ))
    repo = drmod.PgDebtsRepo()
    with patch("core.repos.pg_debts_repo._get_engine", return_value=eng):
        await repo.upsert("u1", "Аня", "i_owe", amount=5000)
        result = await repo.reduce_amount("u1", "i_owe", "Аня", payment=7000)

    new_amount, closed, overpaid = result
    assert new_amount == 0.0
    assert closed is True
    assert overpaid == 2000.0


@pytest.mark.asyncio
async def test_debt_command_overpaid_shows_buttons():
    """'отдала Ане 7к' на долг 5000₽ → долг закрыт + сообщение о переплате
    2000₽ с кнопками [В подушку] [Оставить так]."""
    import core.repos.pg_debts_repo as drmod

    msg = _msg()
    msg.text = "отдала Ане 7к"
    with patch.object(drmod._repo, "reduce_amount",
                      AsyncMock(return_value=(0.0, True, 2000.0))):
        await finance.handle_debt_command(msg, user_notion_id="u-1")

    calls = [c.args[0] for c in msg.answer.call_args_list]
    assert any("закрыт" in t for t in calls)
    assert any("Переплата 2,000₽" in t for t in calls)
    assert finance._pending_overpaid.get(42) == 2000.0
    finance._pending_overpaid.pop(42, None)


@pytest.mark.asyncio
async def test_they_owe_overpaid_shows_buttons():
    """'Маша вернула 7к' на долг 5000₽ → полностью + переплата 2000₽ кнопками."""
    import core.repos.pg_debts_repo as drmod

    msg = _msg()
    msg.text = "Маша вернула 7к"
    with patch.object(drmod._repo, "reduce_amount",
                      AsyncMock(return_value=(0.0, True, 2000.0))):
        await finance.handle_they_owe_command(msg, user_notion_id="u-1")

    calls = [c.args[0] for c in msg.answer.call_args_list]
    assert any("полностью" in t for t in calls)
    assert any("Переплата 2,000₽" in t for t in calls)
    finance._pending_overpaid.pop(42, None)


@pytest.mark.asyncio
async def test_debt_command_no_overpaid_no_extra_message():
    """Ровно закрыла долг (payment == amount) → без сообщения о переплате."""
    import core.repos.pg_debts_repo as drmod

    msg = _msg()
    msg.text = "отдала Ане 5к"
    with patch.object(drmod._repo, "reduce_amount",
                      AsyncMock(return_value=(0.0, True, 0.0))):
        await finance.handle_debt_command(msg, user_notion_id="u-1")

    assert msg.answer.call_count == 1
    assert "Переплата" not in msg.answer.call_args_list[0].args[0]


@pytest.mark.asyncio
async def test_overpaid_cushion_button_adds_to_balance():
    finance._pending_overpaid[42] = 2000.0
    call = _call(42, "overpaid_cushion")
    with patch("core.repos.pg_cushion_repo._repo.add_to_balance",
               AsyncMock(return_value=9000.0)) as m_cushion:
        await finance.on_overpaid_cushion(call, user_notion_id="u-1")

    m_cushion.assert_awaited_once()
    assert m_cushion.call_args.args[0] == "u-1"
    assert m_cushion.call_args.args[1] == 2000.0
    assert 42 not in finance._pending_overpaid
    assert "9,000" in call.message.edit_text.call_args.args[0]


@pytest.mark.asyncio
async def test_overpaid_keep_button_does_nothing_extra():
    finance._pending_overpaid[42] = 2000.0
    call = _call(42, "overpaid_keep")
    with patch("core.repos.pg_cushion_repo._repo.add_to_balance", AsyncMock()) as m_cushion:
        await finance.on_overpaid_keep(call)

    m_cushion.assert_not_called()
    assert 42 not in finance._pending_overpaid


# ── Часть 2: порог 50 000₽ ───────────────────────────────────────────────────

_BUDGET_NORMAL_NO_DEBT = {
    "доходы": [{"name": "зарплата", "amount": 100000}],
    "постоянные": [{"name": "аренда", "amount": 10000}],
    "долги": [], "цели": [], "лимиты": [],
}
_BUDGET_NORMAL_WITH_DEBTS = {
    "доходы": [{"name": "зарплата", "amount": 100000}],
    "постоянные": [{"name": "аренда", "amount": 10000}],
    "долги": [
        {"name": "Вика", "amount": 20000, "deadline": "2026-12-01",
         "strategy": "", "monthly_payment": 5000},
        {"name": "Банк", "amount": 30000, "deadline": "2027-01-01",
         "strategy": "", "monthly_payment": 3000},
    ],
    "цели": [], "лимиты": [],
}


def _threshold_patches(budget_data):
    return [
        patch.object(finance, "_get_user_tz", AsyncMock(return_value=3)),
        patch.object(finance, "_get_payday", AsyncMock(return_value=1)),
        patch.object(finance, "_load_budget_data", AsyncMock(return_value=budget_data)),
        patch.object(finance, "_get_impulse_windfall_bonus", AsyncMock(return_value=0.0)),
    ]


class _apply:
    def __init__(self, patchers):
        self._patchers = patchers

    def __enter__(self):
        for p in self._patchers:
            p.__enter__()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patchers):
            p.__exit__(*exc)
        return False


@pytest.mark.asyncio
async def test_below_threshold_still_auto_distributes_regression():
    """amount=30000 (< 50000) → работает как раньше, автоматически (подушка,
    т.к. долгов нет)."""
    with _apply(_threshold_patches(_BUDGET_NORMAL_NO_DEBT)), \
         patch("core.repos.pg_cushion_repo._repo.add_to_balance",
               AsyncMock(return_value=30000)) as m_cushion:
        msg = await finance._distribute_windfall_income(30000, uid=1, user_notion_id="u-1")

    assert "распределён" in msg
    assert "Подушка +30,000₽" in msg
    m_cushion.assert_awaited_once()


@pytest.mark.asyncio
async def test_above_threshold_returns_empty_no_auto_distribution():
    """amount=2 500 000 (>= 50000) → _distribute_windfall_income НЕ распределяет,
    возвращает "" (caller должен звать _send_windfall_manual_prompt)."""
    with _apply(_threshold_patches(_BUDGET_NORMAL_WITH_DEBTS)), \
         patch("core.repos.pg_cushion_repo._repo.add_to_balance", AsyncMock()) as m_cushion, \
         patch.object(finance, "_partial_debt_payment", AsyncMock()) as m_debt:
        msg = await finance._distribute_windfall_income(2_500_000, uid=1, user_notion_id="u-1")

    assert msg == ""
    m_cushion.assert_not_called()
    m_debt.assert_not_called()


@pytest.mark.asyncio
async def test_manual_prompt_shows_preview_and_buttons():
    msg = _msg()
    with _apply(_threshold_patches(_BUDGET_NORMAL_WITH_DEBTS)):
        await finance._send_windfall_manual_prompt(msg, 2_500_000, uid=1, user_notion_id="u-1")

    text, kwargs = msg.answer.call_args.args[0], msg.answer.call_args.kwargs
    assert "2,500,000₽" in text
    assert "Автоматически получилось бы" in text
    markup = kwargs["reply_markup"]
    button_texts = [b.text for row in markup.inline_keyboard for b in row]
    assert any("подушку" in t for t in button_texts)
    assert any("долги" in t for t in button_texts)
    assert any("Разделить" in t for t in button_texts)
    assert any("предложено" in t for t in button_texts)
    assert finance._pending_windfall_manual[1]["amount"] == 2_500_000
    finance._pending_windfall_manual.pop(1, None)


@pytest.mark.asyncio
async def test_windfall_all_cushion_button_moves_whole_amount():
    finance._pending_windfall_manual[42] = {
        "amount": 2_500_000, "plan": {"debts": []}, "user_notion_id": "u-1",
    }
    call = _call(42, "windfall_all_cushion")
    with patch("core.repos.pg_cushion_repo._repo.add_to_balance",
               AsyncMock(return_value=2_600_000)) as m_cushion:
        await finance.on_windfall_all_cushion(call)

    m_cushion.assert_awaited_once()
    assert m_cushion.call_args.args[0] == "u-1"
    assert m_cushion.call_args.args[1] == 2_500_000
    assert m_cushion.call_args.kwargs.get("source") == "windfall_income"
    assert 42 not in finance._pending_windfall_manual
    assert "2,500,000₽" in call.message.edit_text.call_args.args[0]


@pytest.mark.asyncio
async def test_windfall_close_debts_button_closes_all_remainder_to_cushion():
    """2 долга (20000 + 30000 = 50000) из 2 500 000₽ → оба закрыты, остаток
    2 450 000₽ уходит в подушку."""
    debts = [
        {"name": "Вика", "amount": 20000},
        {"name": "Банк", "amount": 30000},
    ]
    finance._pending_windfall_manual[42] = {
        "amount": 2_500_000, "plan": {"debts": debts}, "user_notion_id": "u-1",
    }
    call = _call(42, "windfall_close_debts")

    debt_calls = []

    async def fake_partial(name, payment, user_notion_id):
        debt_calls.append((name, payment, user_notion_id))
        return (0, 0.0)

    with patch.object(finance, "_partial_debt_payment", AsyncMock(side_effect=fake_partial)), \
         patch("core.repos.pg_cushion_repo._repo.add_to_balance",
               AsyncMock(return_value=2_450_000)) as m_cushion:
        await finance.on_windfall_close_debts(call)

    assert debt_calls == [("Вика", 20000, "u-1"), ("Банк", 30000, "u-1")]
    m_cushion.assert_awaited_once()
    assert m_cushion.call_args.args[0] == "u-1"
    assert m_cushion.call_args.args[1] == 2_450_000
    text = call.message.edit_text.call_args.args[0]
    assert "Вика" in text and "Банк" in text
    assert "2,450,000₽" in text
    assert 42 not in finance._pending_windfall_manual


@pytest.mark.asyncio
async def test_windfall_split_button_asks_clarifying_question_keeps_pending():
    finance._pending_windfall_manual[42] = {
        "amount": 2_500_000, "plan": {"debts": []}, "user_notion_id": "u-1",
    }
    call = _call(42, "windfall_split")
    await finance.on_windfall_split(call)

    text = call.message.edit_text.call_args.args[0]
    assert "1,250,000" in text
    # split не завершает флоу — pending остаётся для последующего уточнения
    assert 42 in finance._pending_windfall_manual
    finance._pending_windfall_manual.pop(42, None)


@pytest.mark.asyncio
async def test_windfall_asis_button_applies_computed_plan():
    finance._pending_windfall_manual[42] = {
        "amount": 60000,
        "plan": {"period_start": "2026-09-01", "is_tight": False,
                 "to_impulse": 0.0, "remainder": 60000, "debt_name": "", "debts": []},
        "user_notion_id": "u-1",
    }
    call = _call(42, "windfall_asis")
    with patch("core.repos.pg_cushion_repo._repo.add_to_balance",
               AsyncMock(return_value=60000)) as m_cushion:
        await finance.on_windfall_asis(call)

    m_cushion.assert_awaited_once()
    assert m_cushion.call_args.args[1] == 60000
    assert 42 not in finance._pending_windfall_manual
    assert "распределён" in call.message.edit_text.call_args.args[0]


def test_windfall_manual_threshold_constant_value():
    assert finance.WINDFALL_MANUAL_THRESHOLD == 50000.0
