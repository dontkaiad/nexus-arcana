"""tests/test_finance_budget_intercept.py — sticky budget state intercept (v1.2.2).

Контекст: пока pending_budget содержал state.plan, ЛЮБОЙ текст в боте
считался «корректировкой плана» — Sonnet перезаписывал план вместо того
чтобы сообщение дошло до classify(). Это ломало list_buy/task/note маршрут.

Здесь покрываем:
1. Команды другого домена (списки/задачи/память) с has_plan → НЕ
   перехватываются (handle_budget_setup_text возвращает False).
2. Реальная корректировка («добавь 5к на еду») → перехватывается как раньше.
3. has_plan TTL = 15 мин: возраст 16 мин → state удалён.
4. Collecting state без plan: TTL = 60 мин → 16 мин не удаляется.
"""
from __future__ import annotations

import json
import sqlite3
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def tmp_budget_db(tmp_path, monkeypatch):
    """Изолированная SQLite для пакета тестов — без касания продовой ../pending_budget.db."""
    from nexus.handlers import finance
    db_path = tmp_path / "test_pending_budget.db"
    monkeypatch.setattr(finance, "_BUDGET_DB", str(db_path))
    yield db_path


def _seed_state(uid: int, data: dict, ts: float | None = None) -> None:
    """Записать state напрямую в БД с указанным timestamp."""
    from nexus.handlers import finance
    if ts is None:
        ts = time.time()
    con = sqlite3.connect(finance._BUDGET_DB)
    con.execute(
        "CREATE TABLE IF NOT EXISTS budget_pending "
        "(uid INTEGER PRIMARY KEY, data TEXT, ts REAL)"
    )
    con.execute(
        "INSERT OR REPLACE INTO budget_pending (uid, data, ts) VALUES (?,?,?)",
        (uid, json.dumps(data, ensure_ascii=False), ts),
    )
    con.commit()
    con.close()


def _make_message(uid: int, text: str) -> MagicMock:
    msg = MagicMock()
    msg.from_user.id = uid
    msg.text = text
    msg.react = AsyncMock()
    msg.answer = AsyncMock()
    return msg


# ── _is_other_domain_command (юнит-тесты на guard) ───────────────────────────


def test_other_domain_lists_buy_with_group():
    from nexus.handlers.finance import _is_other_domain_command
    assert _is_other_domain_command("добавь в косметичка: тушь 800р, крем 1.5к")


def test_other_domain_simple_kupi():
    from nexus.handlers.finance import _is_other_domain_command
    assert _is_other_domain_command("купи молоко")


def test_other_domain_memory_save():
    from nexus.handlers.finance import _is_other_domain_command
    assert _is_other_domain_command("запомни что Маша любит чай")


def test_other_domain_list_done():
    from nexus.handlers.finance import _is_other_domain_command
    assert _is_other_domain_command("купила молоко 89р")


def test_other_domain_list_sum():
    from nexus.handlers.finance import _is_other_domain_command
    assert _is_other_domain_command("сумма косметичка")


def test_not_other_domain_real_correction():
    """«добавь 5к на еду» — это корректировка плана, не команда другого домена."""
    from nexus.handlers.finance import _is_other_domain_command
    # «добавь N на X» без «в покупки/в [группа]:» — это про деньги
    assert not _is_other_domain_command("добавь 5к на еду")
    assert not _is_other_domain_command("обновить лимит привычки")
    assert not _is_other_domain_command("зп ***, аренда ***")
    assert not _is_other_domain_command("")


# ── handle_budget_setup_text — bypass при has_plan ───────────────────────────


@pytest.mark.asyncio
async def test_intercept_bypass_on_list_buy_with_group(tmp_budget_db):
    """has_plan + «добавь в [группа]: …» → handle_budget_setup_text возвращает False."""
    from nexus.handlers.finance import handle_budget_setup_text

    uid = 999_001
    _seed_state(uid, {
        "plan": {"income": 100000, "fixed_total": 30000},
        "state": "has_plan",
        "buf": ["original input"],
        "notion_uid": "fake-notion-uid",
    })
    msg = _make_message(uid, "добавь в косметичка: тушь 800р, крем 1.5к")

    with patch(
        "nexus.handlers.finance._run_budget_analysis", AsyncMock(),
    ) as mock_analysis:
        result = await handle_budget_setup_text(msg, "fake-notion-uid")

    assert result is False, "должен вернуть False — пусть пойдёт в classify"
    mock_analysis.assert_not_called(), "Sonnet не должен пересчитывать план"


@pytest.mark.asyncio
async def test_intercept_bypass_on_kupi(tmp_budget_db):
    from nexus.handlers.finance import handle_budget_setup_text

    uid = 999_002
    _seed_state(uid, {
        "plan": {"income": 100000},
        "state": "has_plan",
        "buf": [],
    })
    msg = _make_message(uid, "купи молоко")

    with patch(
        "nexus.handlers.finance._run_budget_analysis", AsyncMock(),
    ) as mock_analysis:
        result = await handle_budget_setup_text(msg, "")

    assert result is False
    mock_analysis.assert_not_called()


@pytest.mark.asyncio
async def test_intercept_bypass_on_memory(tmp_budget_db):
    from nexus.handlers.finance import handle_budget_setup_text

    uid = 999_003
    _seed_state(uid, {
        "plan": {"income": 100000},
        "state": "has_plan",
        "buf": [],
    })
    msg = _make_message(uid, "запомни что Маша любит чай")

    with patch(
        "nexus.handlers.finance._run_budget_analysis", AsyncMock(),
    ) as mock_analysis:
        result = await handle_budget_setup_text(msg, "")

    assert result is False
    mock_analysis.assert_not_called()


@pytest.mark.asyncio
async def test_intercept_keeps_real_correction(tmp_budget_db):
    """has_plan + «добавь 5к на еду» (реальная корректировка) → перехватывает."""
    from nexus.handlers.finance import handle_budget_setup_text

    uid = 999_004
    _seed_state(uid, {
        "plan": {"income": 100000},
        "state": "has_plan",
        "buf": ["initial data"],
        "notion_uid": "fake-uid",
    })
    msg = _make_message(uid, "добавь 5к на еду")

    with patch(
        "nexus.handlers.finance._run_budget_analysis", AsyncMock(),
    ) as mock_analysis:
        result = await handle_budget_setup_text(msg, "fake-uid")

    assert result is True, "должен перехватить как корректировку"
    mock_analysis.assert_called_once(), "Sonnet должен пересчитать план"


# ── TTL split: has_plan = 15 мин, collecting = 60 мин ────────────────────────


def test_ttl_has_plan_expires_at_16min(tmp_budget_db):
    """has_plan возрастом 16 мин → _budget_get удаляет state и возвращает None."""
    from nexus.handlers import finance

    uid = 999_005
    _seed_state(uid, {
        "plan": {"income": 100000},
        "state": "has_plan",
        "buf": [],
    }, ts=time.time() - 16 * 60)  # 16 минут назад

    result = finance._budget_get(uid)
    assert result is None, "has_plan должен протухнуть после 15 мин"

    # Убедимся что state физически удалён из БД
    con = sqlite3.connect(finance._BUDGET_DB)
    row = con.execute(
        "SELECT 1 FROM budget_pending WHERE uid=?", (uid,)
    ).fetchone()
    con.close()
    assert row is None


def test_ttl_collecting_alive_at_16min(tmp_budget_db):
    """collecting state без plan возрастом 16 мин → НЕ удалён (TTL 60 мин)."""
    from nexus.handlers import finance

    uid = 999_006
    _seed_state(uid, {
        "state": "collecting",
        "buf": ["partial data"],
    }, ts=time.time() - 16 * 60)

    result = finance._budget_get(uid)
    assert result is not None, "collecting должен жить до TTL=60мин"
    assert result.get("state") == "collecting"


def test_ttl_collecting_expires_at_61min(tmp_budget_db):
    """collecting state возрастом 61 мин → удалён."""
    from nexus.handlers import finance

    uid = 999_007
    _seed_state(uid, {
        "state": "collecting",
        "buf": [],
    }, ts=time.time() - 61 * 60)

    result = finance._budget_get(uid)
    assert result is None
