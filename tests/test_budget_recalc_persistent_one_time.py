"""tests/test_budget_recalc_persistent_one_time.py

Пересчёт УЖЕ принятого плана (_run_budget_analysis, has_existing_data=True)
не должен терять разовые — сессия к этому моменту свежая (buf пуст, старая
сессия закрылась после ✅ Принять), поэтому Sonnet больше не видит разовые в
user_messages. Фикс: core.budget.load_budget_data теперь читает персистентные
разовый_* факты в bucket "разовые"; _build_sonnet_input подмешивает их в
user_messages (маркированным текстом "разовый: ...") ТОЛЬКО когда в buf
разовых нет — свежий явный ввод в buf имеет приоритет.
"""
from __future__ import annotations

import json
import sqlite3
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _seed_state(uid: int, data: dict, ts=None) -> None:
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


@pytest.fixture
def tmp_budget_db(tmp_path, monkeypatch):
    from nexus.handlers import finance
    db_path = tmp_path / "test_pending_budget.db"
    monkeypatch.setattr(finance, "_BUDGET_DB", str(db_path))
    yield db_path


async def _fake_loading() -> AsyncMock:
    loading = AsyncMock()
    loading.message_id = 555
    loading.edit_text = AsyncMock()
    return loading


# ── Unit: _build_sonnet_input подмешивает персистентные разовые ─────────────

@pytest.mark.asyncio
async def test_build_sonnet_input_uses_persistent_one_time_when_buf_empty():
    from nexus.handlers import finance

    budget = {
        "доходы": [], "постоянные": [], "цели": [], "долги": [], "лимиты": [],
        "разовые": [{"name": "виза", "amount": 3500}, {"name": "билет", "amount": 15000}],
    }
    with patch.object(finance, "_load_budget_data", AsyncMock(return_value=budget)), \
         patch.object(finance, "_get_payday", AsyncMock(return_value=1)), \
         patch.object(finance._repo, "query_records", AsyncMock(return_value=[])), \
         patch.object(finance, "_budget_get", lambda uid: {"buf": []}):
        raw = await finance._build_sonnet_input(uid=1, user_notion_id="u")

    ctx = json.loads(raw)
    assert "разовый: виза — 3500₽" in ctx["user_messages"]
    assert "разовый: билет — 15000₽" in ctx["user_messages"]


@pytest.mark.asyncio
async def test_build_sonnet_input_buf_marker_takes_priority_no_duplication():
    """buf уже содержит явную метку 'разовый:' → персистентные факты НЕ
    подмешиваются (не задваиваем)."""
    from nexus.handlers import finance

    budget = {
        "доходы": [], "постоянные": [], "цели": [], "долги": [], "лимиты": [],
        "разовые": [{"name": "старая виза", "amount": 999}],
    }
    with patch.object(finance, "_load_budget_data", AsyncMock(return_value=budget)), \
         patch.object(finance, "_get_payday", AsyncMock(return_value=1)), \
         patch.object(finance._repo, "query_records", AsyncMock(return_value=[])), \
         patch.object(finance, "_budget_get", lambda uid: {"buf": ["разовый: новая виза 5000"]}):
        raw = await finance._build_sonnet_input(uid=1, user_notion_id="u")

    ctx = json.loads(raw)
    assert "новая виза" in ctx["user_messages"]
    assert "старая виза" not in ctx["user_messages"]


@pytest.mark.asyncio
async def test_build_sonnet_input_no_one_time_anywhere_regression():
    """Обычный первый расчёт: buf без разовых, в Памяти разовых фактов ещё нет
    → user_messages как раньше, ничего не подмешано."""
    from nexus.handlers import finance

    budget = {"доходы": [], "постоянные": [], "цели": [], "долги": [], "лимиты": [], "разовые": []}
    with patch.object(finance, "_load_budget_data", AsyncMock(return_value=budget)), \
         patch.object(finance, "_get_payday", AsyncMock(return_value=1)), \
         patch.object(finance._repo, "query_records", AsyncMock(return_value=[])), \
         patch.object(finance, "_budget_get", lambda uid: {"buf": ["зп 100к, аренда 30к"]}):
        raw = await finance._build_sonnet_input(uid=1, user_notion_id="u")

    ctx = json.loads(raw)
    assert ctx["user_messages"] == "зп 100к, аренда 30к"
    assert "разовый" not in ctx["user_messages"]


# ── Integration: пересчёт принятого плана через _run_budget_analysis ────────

@pytest.mark.asyncio
async def test_recalc_accepted_plan_keeps_one_time_tightens_pool(tmp_budget_db):
    """has_existing_data=True (постоянные непустые), state.buf пуст, в Памяти
    есть разовый_виза — 20000₽. Sonnet (мок) получает контекст с этой разовой
    позицией (проверяем сам prompt) и — как и должна была бы честно посчитать
    Sonnet, увидев one_time в контексте, — возвращает already_spent, включающий
    one_time_total: пул на месяц оказывается меньше, план «жёстко», а не
    ошибочно «комфортный месяц» (расчёт был бы таким без разовой позиции)."""
    from nexus.handlers import finance

    uid = 999_600
    # buf пуст — сессия свежая (как после ✅ Принять и нового /budget пересчёта).
    _seed_state(uid, {"buf": [], "notion_uid": "u-1", "state": "collecting"})

    loading = await _fake_loading()
    msg = MagicMock()
    msg.from_user.id = uid
    msg.chat.id = 1
    msg.bot = AsyncMock()
    msg.answer = AsyncMock(return_value=loading)

    budget_data = {
        "доходы": [{"name": "зарплата", "amount": 40000}],
        "постоянные": [{"name": "аренда", "category": "🏠 Жильё", "amount": 10000}],
        "цели": [], "долги": [], "лимиты": [],
        "разовые": [{"name": "виза", "amount": 20000}],
    }

    prompts = []

    async def fake_ask_claude(prompt, **kw):
        # _run_budget_analysis вызывает ask_claude дважды: Фаза 1 (разбор,
        # sonnet_input с user_messages) и Фаза 2 (текстовая нарезка плана,
        # другой формат) — нас интересует только первый вызов.
        prompts.append(prompt)
        if len(prompts) == 1:
            return json.dumps({
                "income_total": 40000, "fixed_total": 10000,
                "one_time": [{"name": "виза", "category": "💳 Прочее", "amount": 20000}],
                "one_time_total": 20000,
                "already_spent": 20000,  # только one_time — реальных трат периода нет
                "debts_monthly": [], "goals": [],
            }, ensure_ascii=False)
        return "{}"  # Фаза 2 нарезка — не важна для этого теста

    with patch.object(finance, "_load_budget_data", AsyncMock(return_value=budget_data)), \
         patch.object(finance, "_period_spending", AsyncMock(return_value=({}, 0.0))), \
         patch.object(finance._repo, "query_records", AsyncMock(return_value=[])), \
         patch.object(finance, "_get_payday", AsyncMock(return_value=1)), \
         patch.object(finance, "ask_claude", fake_ask_claude):
        await finance._run_budget_analysis(msg, uid)

    # Контекст, реально отправленный Sonnet (Фаза 1), содержит персистентную разовую.
    ctx = json.loads(prompts[0])
    assert "разовый: виза — 20000₽" in ctx["user_messages"]

    # distributable = income(40000) - fixed(10000) - already_spent(20000) = 10000
    # — заметно меньше, чем было бы без one_time (30000) → «жёстко».
    text = loading.edit_text.call_args.args[0]
    assert "жёстко" in text
