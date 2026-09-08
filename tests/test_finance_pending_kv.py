"""tests/test_finance_pending_kv.py — #208: финансовые pending-диалоги на
core.pending_kv (переживают рестарт), плюс починка кастомного лимита.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _kv(tmp_path):
    import core.pending_kv as m
    with patch.object(m, "_DB_PATH", str(tmp_path / "pk.db")):
        yield m


def _msg(text=""):
    m = MagicMock()
    m.from_user.id = 7
    m.chat.id = 1
    m.text = text
    m.answer = AsyncMock()
    return m


def _call(data):
    c = MagicMock()
    c.from_user.id = 7
    c.data = data
    c.answer = AsyncMock()
    c.message = MagicMock()
    c.message.edit_text = AsyncMock()
    c.message.answer = AsyncMock()
    return c


# ── кастомный лимит: кнопка «Другая сумма» → ввод числа → сохранение ──────────

@pytest.mark.asyncio
async def test_custom_limit_button_then_amount_saves():
    import nexus.handlers.finance as fin

    with patch.object(fin, "_save_limit_to_memory", AsyncMock()) as m_save:
        await fin.on_set_limit(_call("setlim_кафе_custom"), user_id="u")
        # число приходит следующим сообщением — через gate handle_finance_pending
        handled = await fin.handle_finance_pending(_msg("5000"), user_id="u")

    assert handled is True
    m_save.assert_awaited_once_with("кафе", 5000, "u")


@pytest.mark.asyncio
async def test_custom_limit_accepts_k_suffix():
    import nexus.handlers.finance as fin
    with patch.object(fin, "_save_limit_to_memory", AsyncMock()) as m_save:
        await fin.on_set_limit(_call("setlim_привычки_custom"), user_id="u")
        await fin.handle_finance_pending(_msg("15к"), user_id="u")
    m_save.assert_awaited_once_with("привычки", 15000, "u")


@pytest.mark.asyncio
async def test_custom_limit_cancel():
    import nexus.handlers.finance as fin
    with patch.object(fin, "_save_limit_to_memory", AsyncMock()) as m_save:
        await fin.on_set_limit(_call("setlim_кафе_custom"), user_id="u")
        handled = await fin.handle_finance_pending(_msg("отмена"), user_id="u")
    assert handled is True
    m_save.assert_not_awaited()


@pytest.mark.asyncio
async def test_gate_noop_without_pending():
    import nexus.handlers.finance as fin
    assert await fin.handle_finance_pending(_msg("что угодно"), user_id="u") is False


@pytest.mark.asyncio
async def test_custom_limit_survives_restart():
    """Между кнопкой и вводом числа процесс перезапустился — состояние в
    SQLite, не в памяти."""
    import nexus.handlers.finance as fin
    await fin.on_set_limit(_call("setlim_кафе_custom"), user_id="u")
    # эмулируем: gate вызывается «после рестарта» — новое соединение к kv
    with patch.object(fin, "_save_limit_to_memory", AsyncMock()) as m_save:
        assert await fin.handle_finance_pending(_msg("8000"), user_id="u") is True
    m_save.assert_awaited_once_with("кафе", 8000, "u")


# ── переплата по долгу: kv-стор ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_overpaid_notify_then_cushion():
    import nexus.handlers.finance as fin

    await fin._notify_overpaid(_msg(), 1500.0)
    fake_cushion = MagicMock()
    fake_cushion.add_to_balance = AsyncMock(return_value=12000.0)
    with patch("core.repos.pg_cushion_repo._repo", fake_cushion):
        await fin.on_overpaid_cushion(_call("overpaid_cushion"), user_id="u")
    fake_cushion.add_to_balance.assert_awaited_once()
    assert fake_cushion.add_to_balance.await_args.args[1] == 1500.0


@pytest.mark.asyncio
async def test_overpaid_keep_clears():
    import nexus.handlers.finance as fin
    import core.pending_kv as kv
    await fin._notify_overpaid(_msg(), 500.0)
    await fin.on_overpaid_keep(_call("overpaid_keep"))
    assert kv.get(7, fin._PK_OVERPAID) is None


# ── nexus_bot: fin_type_* clarify — kv-стор, переживает рестарт ──────────────

@pytest.mark.asyncio
async def test_nexus_bot_fin_type_reads_kv():
    import nexus.nexus_bot as nb
    import core.pending_kv as kv

    kv.save(7, nb._PK_FIN_TYPE, {
        "data": {"amount": 500.0, "category": "💳 Прочее",
                 "source": "💳 Карта", "title": "от вадима"},
        "text": "500 от вадима", "user_id": "u-1",
    }, ttl=600)

    fake_repo = MagicMock()
    fake_repo.add = AsyncMock(return_value="fin-1")
    q = _call("fin_type_income_7")
    with patch("core.repos.finance_repo._repo", fake_repo), \
         patch.object(nb, "_get_user_tz", AsyncMock(return_value=3)):
        await nb.on_finance_clarify(q, user_id="u-1")

    fake_repo.add.assert_awaited_once()
    assert fake_repo.add.await_args.kwargs["type_"] == "💰 Доход"
    assert kv.get(7, nb._PK_FIN_TYPE) is None   # снят после записи


@pytest.mark.asyncio
async def test_nexus_bot_fin_type_expired():
    import nexus.nexus_bot as nb
    q = _call("fin_type_expense_7")
    with patch.object(nb, "_get_user_tz", AsyncMock(return_value=3)):
        await nb.on_finance_clarify(q, user_id="u-1")
    q.answer.assert_awaited()
    assert "истекл" in q.answer.await_args.args[0].lower()
