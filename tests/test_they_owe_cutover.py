"""tests/test_they_owe_cutover.py — they_owe regression (#8 шаг 5).

4 mock tests (unit):
  1. "дала Маше 5к до июня" → upsert(they_owe, "Маше", 5000, "июня")
  2. "Маша вернула 2к" → reduce_amount(they_owe, "Маша", 2000)
  3. "Маша вернула долг" → deactivate(they_owe, "Маша")
  4. "мне должны" → list_active(they_owe) → formatted text

2 коллизия/гард tests:
  5. COLLISION: "отдала Маше 2к" / "погасила Ане 5к" — НЕ матчит _THEY_OWE_CMD_RE
  6. GUARD: "я вернула 2к" — не матчит классификатор; "мне вернули 2к" — матчит,
            но гард в хендлере не допускает запись

2 SQLite integration tests:
  7. CONSISTENCY: дала → list_active(they_owe) видит; load_budget_data НЕ видит
  8. CLOSED: дала → вернула → list_closed(they_owe) видит
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool


def _make_engine():
    eng = sa.create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with eng.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE debts ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_notion_id TEXT NOT NULL DEFAULT '', "
            "name TEXT NOT NULL, "
            "kind TEXT NOT NULL DEFAULT 'i_owe', "
            "amount REAL NOT NULL, "
            "deadline TEXT, "
            "strategy TEXT, "
            "monthly_payment REAL NOT NULL DEFAULT 0, "
            "is_active INTEGER NOT NULL DEFAULT 1, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        ))
    return eng


def _make_msg(text: str):
    msg = MagicMock()
    msg.text = text
    msg.answer = AsyncMock()
    return msg


# ── Test 1: "дала Маше 5к до июня" → upsert(they_owe) ───────────────────────

@pytest.mark.asyncio
async def test_save_they_owe_calls_upsert():
    import core.repos.pg_debts_repo as drmod
    from nexus.handlers.finance import handle_they_owe_command

    msg = _make_msg("дала Маше 5к до июня")
    with patch.object(drmod._repo, "upsert", new_callable=AsyncMock) as mock_upsert:
        await handle_they_owe_command(msg, user_notion_id="uid1")

    mock_upsert.assert_called_once()
    args, kwargs = mock_upsert.call_args
    assert args[0] == "uid1"
    assert args[1] == "Маше"
    assert args[2] == "they_owe"
    assert kwargs["amount"] == 5000.0
    assert kwargs["deadline"] == "июня"
    msg.answer.assert_called_once()


# ── Test 2: "Маша вернула 2к" → reduce_amount(they_owe) ─────────────────────

@pytest.mark.asyncio
async def test_return_partial_calls_reduce_amount():
    import core.repos.pg_debts_repo as drmod
    from nexus.handlers.finance import handle_they_owe_command

    msg = _make_msg("Маша вернула 2к")
    with patch.object(drmod._repo, "reduce_amount",
                      new_callable=AsyncMock, return_value=(3000.0, False)) as mock_reduce:
        await handle_they_owe_command(msg, user_notion_id="uid1")

    mock_reduce.assert_called_once()
    args, _ = mock_reduce.call_args
    assert args[0] == "uid1"
    assert args[1] == "they_owe"
    assert args[2] == "Маша"
    assert args[3] == 2000.0
    msg.answer.assert_called_once()
    assert "Остаток" in msg.answer.call_args[0][0]


# ── Test 3: "Маша вернула долг" → deactivate(they_owe) ──────────────────────

@pytest.mark.asyncio
async def test_return_full_calls_deactivate():
    import core.repos.pg_debts_repo as drmod
    from nexus.handlers.finance import handle_they_owe_command

    msg = _make_msg("Маша вернула долг")
    with patch.object(drmod._repo, "deactivate",
                      new_callable=AsyncMock, return_value=True) as mock_deact:
        await handle_they_owe_command(msg, user_notion_id="uid1")

    mock_deact.assert_called_once_with("uid1", "they_owe", "Маша")
    msg.answer.assert_called_once()
    assert "вернул" in msg.answer.call_args[0][0]


# ── Test 4: "мне должны" → list_active(they_owe) ─────────────────────────────

@pytest.mark.asyncio
async def test_view_they_owe_list():
    import core.repos.pg_debts_repo as drmod
    from core.repos.pg_debts_repo import Debt
    from nexus.handlers.finance import handle_they_owe_command

    fake = [
        Debt(id="1", user_notion_id="uid1", name="Маша", kind="they_owe",
             amount=5000.0, deadline="июнь", strategy="", monthly_payment=0.0,
             is_active=True, created_at="", updated_at=""),
        Debt(id="2", user_notion_id="uid1", name="Петя", kind="they_owe",
             amount=3000.0, deadline="", strategy="", monthly_payment=0.0,
             is_active=True, created_at="", updated_at=""),
    ]
    msg = _make_msg("мне должны")
    with patch.object(drmod._repo, "list_active",
                      new_callable=AsyncMock, return_value=fake) as mock_la:
        await handle_they_owe_command(msg, user_notion_id="uid1")

    mock_la.assert_called_once_with("uid1", kind="they_owe")
    reply = msg.answer.call_args[0][0]
    assert "Маша" in reply
    assert "Петя" in reply
    assert "8" in reply  # 8 000₽ итого (5000+3000)


# ── Test 5: COLLISION — "отдала"/"погасила" не матчит _THEY_OWE_CMD_RE ───────

def test_collision_i_owe_not_captured_by_they_owe_re():
    from core.classifier import _THEY_OWE_CMD_RE

    assert not _THEY_OWE_CMD_RE.search("отдала Маше 2к"), \
        "'отдала' содержит 'дала' но \\b не даёт матч внутри слова"
    assert not _THEY_OWE_CMD_RE.search("погасила Ане 5к"), \
        "'погасила' не должна матчить they_owe"
    assert not _THEY_OWE_CMD_RE.search("закрыла долг Маша"), \
        "i_owe close не должна матчить they_owe"
    assert not _THEY_OWE_CMD_RE.search("новый долг Маша 10к"), \
        "i_owe new не должна матчить they_owe"


def test_pogasila_routes_to_debt_command_not_they_owe():
    """'погасила Ане 5к' → не they_owe, но матчит _DEBT_CMD_RE → i_owe роутинг цел."""
    from core.classifier import _THEY_OWE_CMD_RE, _DEBT_CMD_RE

    text = "погасила Ане 5к"
    assert not _THEY_OWE_CMD_RE.search(text)
    assert _DEBT_CMD_RE.search(text)


# ── Test 6: GUARD — "я вернула 2к" и "мне вернули 2к" не пишут they_owe ─────

def test_guard_single_char_pronoun_not_matched_by_classifier():
    """'я вернула 2к' — 'я' (1 char) не матчит \\S{2,} → классификатор молчит."""
    from core.classifier import _THEY_OWE_CMD_RE
    assert not _THEY_OWE_CMD_RE.search("я вернула 2к")


@pytest.mark.asyncio
async def test_guard_pronoun_name_no_db_write():
    """'мне вернули 2к' → классификатор матчит мне\\s+вернул, но хендлер не пишет в БД."""
    import core.repos.pg_debts_repo as drmod
    from nexus.handlers.finance import handle_they_owe_command

    msg = _make_msg("мне вернули 2к")
    with patch.object(drmod._repo, "reduce_amount", new_callable=AsyncMock) as mock_reduce:
        with patch.object(drmod._repo, "deactivate", new_callable=AsyncMock) as mock_deact:
            with patch.object(drmod._repo, "upsert", new_callable=AsyncMock) as mock_upsert:
                await handle_they_owe_command(msg, user_notion_id="uid1")

    mock_reduce.assert_not_called()
    mock_deact.assert_not_called()
    mock_upsert.assert_not_called()
    msg.answer.assert_called_once()  # получила help-сообщение


# ── Test 7: CONSISTENCY via SQLite ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_save_they_owe_visible_in_list_active_invisible_in_budget():
    """дала → list_active(they_owe) видит; load_budget_data НЕ видит (kind='i_owe' хардкод)."""
    import core.repos.pg_debts_repo as drmod
    import core.repos.memory_repo as mrmod
    from nexus.handlers.finance import _save_they_owe
    from core.repos.pg_debts_repo import PgDebtsRepo
    from core.budget import load_budget_data

    eng = _make_engine()

    with patch.object(drmod, "_get_engine", return_value=eng):
        with patch.object(mrmod._repo, "find_by_key_prefixes", AsyncMock(return_value=[])):
            await _save_they_owe("Маша", 5000, "июнь", user_notion_id="uid_cons")

            repo = PgDebtsRepo()
            they_owe_list = await repo.list_active("uid_cons", kind="they_owe")
            budget = await load_budget_data("uid_cons")

    assert len(they_owe_list) == 1
    assert they_owe_list[0].name == "Маша"
    assert they_owe_list[0].kind == "they_owe"

    assert budget["долги"] == [], "they_owe не должен попасть в бюджетные долги (kind='i_owe')"


# ── Test 8: CLOSED via SQLite ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_deactivated_they_owe_visible_in_list_closed():
    """дала → вернула → list_closed(they_owe) видит; list_active пуста."""
    import core.repos.pg_debts_repo as drmod
    from nexus.handlers.finance import _save_they_owe, _deactivate_they_owe
    from core.repos.pg_debts_repo import PgDebtsRepo

    eng = _make_engine()

    with patch.object(drmod, "_get_engine", return_value=eng):
        repo = PgDebtsRepo()
        await _save_they_owe("Петя", 10000, "октябрь", user_notion_id="uid_cl")
        await _deactivate_they_owe("Петя", user_notion_id="uid_cl")

        active = await repo.list_active("uid_cl", kind="they_owe")
        closed = await repo.list_closed("uid_cl", kind="they_owe")

    assert active == []
    assert len(closed) == 1
    assert closed[0].name == "Петя"
    assert closed[0].is_active is False
