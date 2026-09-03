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


def test_budget_db_lives_in_data_dir():
    """#191: pending_budget.db должна лежать в /app/data (volume), не в корне /app —
    иначе payday_sent/pending теряются при каждом пересоздании контейнера."""
    import os
    from nexus.handlers import finance
    parts = os.path.normpath(finance._BUDGET_DB).split(os.sep)
    assert parts[-2] == "data"
    assert parts[-1] == "pending_budget.db"


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


def test_other_domain_task_word_prefix():
    """issue #90: «задача …» — явная команда задач, не корректировка бюджета."""
    from nexus.handlers.finance import _is_other_domain_command
    assert _is_other_domain_command("задача проверить интернет напоминание на 3 июля 13 часов")
    assert _is_other_domain_command("задачу поставь на завтра")
    assert _is_other_domain_command("задачи на сегодня")


def test_other_domain_remind_forms():
    """issue #90: формы «напомни/напоминалку/напоминание» в начале текста."""
    from nexus.handlers.finance import _is_other_domain_command
    assert _is_other_domain_command("напомни в 15 позвонить")
    assert _is_other_domain_command("напомнить завтра про коммуналку")
    assert _is_other_domain_command("напоминалку на пятницу")
    assert _is_other_domain_command("напоминание на 3 июля 13 часов")


def test_not_other_domain_real_correction():
    """«добавь 5к на еду» — это корректировка плана, не команда другого домена."""
    from nexus.handlers.finance import _is_other_domain_command
    # «добавь N на X» без «в покупки/в [группа]:» — это про деньги
    assert not _is_other_domain_command("добавь 5к на еду")
    assert not _is_other_domain_command("обновить лимит привычки")
    assert not _is_other_domain_command("зп 60к, аренда 20к")
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
async def test_intercept_bypass_on_task_with_reminder(tmp_budget_db):
    """issue #90: has_plan + «задача … напоминание …» → НЕ корректировка,
    Sonnet не пересчитывает план."""
    from nexus.handlers.finance import handle_budget_setup_text

    uid = 999_004
    _seed_state(uid, {
        "plan": {"income": 100000},
        "state": "has_plan",
        "buf": [],
    })
    msg = _make_message(uid, "задача проверить интернет напоминание на 3 июля 13 часов")

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


# ── Порог "жёстко" = BUDGET_TIGHT_WARN (25 500₽) ──────────────────────────────
#
# Новая модель: лимиты считает compute_limits(). «Жёстко» = discretionary ниже
# 25 500₽ (10к продукты + 13к потолок привычек + 2.5к железных транспорт/импульс)
# — priority-категории (кафе/бьюти/…) уже обнулены. free_after_debts код
# пересчитывает сам из income/fixed/долгов, поле из ответа Sonnet игнорируется.

async def _fake_loading() -> AsyncMock:
    loading = AsyncMock()
    loading.message_id = 555
    loading.edit_text = AsyncMock()
    return loading


@pytest.mark.asyncio
async def test_run_budget_analysis_warns_below_tight_warn(tmp_budget_db):
    """income 20к, без долгов → discretionary 20к (< 25 500) → плашка «жёстко»."""
    from nexus.handlers import finance

    uid = 999_100
    _seed_state(uid, {"buf": ["зп 20к"], "notion_uid": "u-1", "state": "collecting"})

    loading = await _fake_loading()
    msg = MagicMock()
    msg.from_user.id = uid
    msg.chat.id = 1
    msg.bot = AsyncMock()
    msg.answer = AsyncMock(return_value=loading)

    empty_budget = {"доходы": [], "постоянные": [], "цели": [], "долги": [], "лимиты": []}
    plan_json = json.dumps({
        "income_total": 20000, "fixed_total": 0, "debts_monthly": [], "goals": [],
    }, ensure_ascii=False)

    with patch.object(finance, "_load_budget_data", AsyncMock(return_value=empty_budget)), \
         patch.object(finance, "_period_spending", AsyncMock(return_value=({}, 0.0))), \
         patch.object(finance, "ask_claude", AsyncMock(return_value=plan_json)):
        await finance._run_budget_analysis(msg, uid)

    text = loading.edit_text.call_args.args[0]
    assert "жёстко" in text


@pytest.mark.asyncio
async def test_run_budget_analysis_no_warning_above_tight_warn(tmp_budget_db):
    """income 60к, без долгов → discretionary 60к (≥ 25 500) → без плашки «жёстко»."""
    from nexus.handlers import finance

    uid = 999_101
    _seed_state(uid, {"buf": ["зп 60к"], "notion_uid": "u-1", "state": "collecting"})

    loading = await _fake_loading()
    msg = MagicMock()
    msg.from_user.id = uid
    msg.chat.id = 1
    msg.bot = AsyncMock()
    msg.answer = AsyncMock(return_value=loading)

    empty_budget = {"доходы": [], "постоянные": [], "цели": [], "долги": [], "лимиты": []}
    plan_json = json.dumps({
        "income_total": 60000, "fixed_total": 0, "debts_monthly": [], "goals": [],
    }, ensure_ascii=False)

    with patch.object(finance, "_load_budget_data", AsyncMock(return_value=empty_budget)), \
         patch.object(finance, "_period_spending", AsyncMock(return_value=({}, 0.0))), \
         patch.object(finance, "ask_claude", AsyncMock(return_value=plan_json)):
        await finance._run_budget_analysis(msg, uid)

    text = loading.edit_text.call_args.args[0]
    assert "жёстко" not in text


def test_format_plan_variant_a_tight_label_just_below_15500():
    """remaining=15499 (< 15500) → лейбл «⚠️ жёстко» на варианте А."""
    from nexus.handlers.finance import _format_plan

    plan = {
        "is_tight_month": True,
        "variant_a": {"label": "Платить по плану", "remaining": 15499},
        "variant_b": {"label": "Пересмотреть стратегию", "remaining": 20000},
    }
    out = _format_plan(plan)
    assert "жёстко" in out


def test_format_plan_variant_a_no_tight_label_at_tight_warn():
    """remaining=25500 (граница BUDGET_TIGHT_WARN, НЕ <) → БЕЗ лейбла «жёстко»."""
    from nexus.handlers.finance import _format_plan

    plan = {
        "is_tight_month": True,
        "variant_a": {"label": "Платить по плану", "remaining": 25500},
        "variant_b": {"label": "Пересмотреть стратегию", "remaining": 30000},
    }
    out = _format_plan(plan)
    assert "жёстко" not in out


# ── already_spent/savings_from_last_period в legacy-промпте (первый /budget) ─
#
# BUDGET_SONNET_SYSTEM (полный контекст) получил Шаг 1.5/1.6 раньше;
# _BUDGET_PARSE_PROMPT_LEGACY (первый /budget с нуля, пустая Память) — нет.
# already_spent/savings_from_last_period теперь считаются один раз в общем
# _period_spending() и пробрасываются в оба промпта.

@pytest.mark.asyncio
async def test_period_spending_splits_income_and_expense():
    from nexus.handlers import finance

    r1 = MagicMock(amount=1000, category="🍜 Продукты", type_="💸 Расход")
    r2 = MagicMock(amount=500, category="🍱 Кафе/Доставка", type_="💸 Расход")
    r3 = MagicMock(amount=60000, category="💰 Зарплата", type_="💰 Доход")

    with patch.object(finance, "_get_payday", AsyncMock(return_value=1)), \
         patch.object(finance._repo, "query_records", AsyncMock(return_value=[r1, r2, r3])):
        spending, income = await finance._period_spending()

    assert spending == {"🍜 Продукты": 1000.0, "🍱 Кафе/Доставка": 500.0}
    assert income == 60000.0


def test_legacy_prompt_template_has_already_spent_placeholders():
    """Регресс: раньше already_spent/savings_from_last_period были только
    в BUDGET_SONNET_SYSTEM, в legacy-промпте про них не было ни слова."""
    from nexus.handlers.finance import _BUDGET_PARSE_PROMPT_LEGACY

    assert "{already_spent}" in _BUDGET_PARSE_PROMPT_LEGACY
    assert "{savings_from_last_period}" in _BUDGET_PARSE_PROMPT_LEGACY
    assert '"already_spent"' in _BUDGET_PARSE_PROMPT_LEGACY
    assert '"savings_from_last_period"' in _BUDGET_PARSE_PROMPT_LEGACY
    # already_spent реально вычитается, а не только упоминается — как в Шаге 1.5
    assert "already_spent" in _BUDGET_PARSE_PROMPT_LEGACY.split("РАСЧЁТ:")[1].split("4.")[0]


@pytest.mark.asyncio
async def test_run_budget_analysis_legacy_branch_passes_already_spent(tmp_budget_db):
    """Первый /budget с нуля (пустая Память → legacy-ветка): already_spent из
    _period_spending и savings_from_last_period из state реально попадают в
    промпт Sonnet, а из ответа — в итоговое сообщение (через _format_plan)."""
    from nexus.handlers import finance

    uid = 999_200
    _seed_state(uid, {
        "buf": ["зп 60к, аренда 20к"], "notion_uid": "u-1", "state": "collecting",
        "savings_from_last_period": 3000,
    })

    loading = await _fake_loading()
    msg = MagicMock()
    msg.from_user.id = uid
    msg.chat.id = 1
    msg.bot = AsyncMock()
    msg.answer = AsyncMock(return_value=loading)

    empty_budget = {"доходы": [], "постоянные": [], "цели": [], "долги": [], "лимиты": []}
    prompts = []

    async def fake_ask(prompt, **kw):
        prompts.append(prompt)
        return json.dumps({
            "income_total": 60000, "fixed_total": 20000, "debts_monthly": [], "goals": [],
            "already_spent": 4500, "savings_from_last_period": 3000,
        }, ensure_ascii=False)

    with patch.object(finance, "_load_budget_data", AsyncMock(return_value=empty_budget)), \
         patch.object(finance, "_period_spending",
                      AsyncMock(return_value=({"🍜 Продукты": 4500.0}, 0.0))), \
         patch.object(finance, "ask_claude", fake_ask):
        await finance._run_budget_analysis(msg, uid)

    prompt = prompts[0]  # Фаза 1 — разбор
    assert "4500" in prompt
    assert "3000" in prompt

    text = loading.edit_text.call_args.args[0]
    assert "📤 Уже потрачено (реальные траты): 4,500₽" in text
    assert "🛡️ Экономия с прошлого периода: +3,000₽" in text


@pytest.mark.asyncio
async def test_run_budget_analysis_legacy_branch_defaults_to_zero(tmp_budget_db):
    """Совсем свежий пользователь: нет прошлых трат, нет экономии → 0, промпт
    не падает, already_spent/экономия не показываются в сообщении."""
    from nexus.handlers import finance

    uid = 999_201
    _seed_state(uid, {"buf": ["зп 60к"], "notion_uid": "u-1", "state": "collecting"})

    loading = await _fake_loading()
    msg = MagicMock()
    msg.from_user.id = uid
    msg.chat.id = 1
    msg.bot = AsyncMock()
    msg.answer = AsyncMock(return_value=loading)

    empty_budget = {"доходы": [], "постоянные": [], "цели": [], "долги": [], "лимиты": []}
    prompts = []

    async def fake_ask(prompt, **kw):
        prompts.append(prompt)
        return json.dumps({
            "income_total": 60000, "fixed_total": 0, "debts_monthly": [], "goals": [],
            "already_spent": 0, "savings_from_last_period": 0,
        }, ensure_ascii=False)

    with patch.object(finance, "_load_budget_data", AsyncMock(return_value=empty_budget)), \
         patch.object(finance, "_period_spending", AsyncMock(return_value=({}, 0.0))), \
         patch.object(finance, "ask_claude", fake_ask):
        await finance._run_budget_analysis(msg, uid)

    prompt = prompts[0]  # Фаза 1 — разбор
    assert "{already_spent}" not in prompt  # плейсхолдер реально подставлен
    assert "already_spent): 0₽" in prompt

    text = loading.edit_text.call_args.args[0]
    assert "Уже потрачено" not in text
    assert "Экономия с прошлого периода" not in text


# ── Тяжёлый месяц без платежа по долгу → ОДИН план, без кнопок А/Б ──────────

@pytest.mark.asyncio
async def test_tight_month_no_debt_payment_renders_single_plan(tmp_budget_db):
    """Нет платящего долга → код делает ОДИН план даже при малом остатке:
    кнопки обычные (✅ Принять), НЕ 🅰️/🅱️, плюс плашка «жёстко»."""
    from nexus.handlers import finance

    uid = 999_300
    _seed_state(uid, {"buf": ["зп 18к"], "notion_uid": "u-1", "state": "collecting"})

    loading = await _fake_loading()
    msg = MagicMock()
    msg.from_user.id = uid
    msg.chat.id = 1
    msg.bot = AsyncMock()
    msg.answer = AsyncMock(return_value=loading)

    empty_budget = {"доходы": [], "постоянные": [], "цели": [], "долги": [], "лимиты": []}
    plan_json = json.dumps({
        "income_total": 18000, "fixed_total": 0, "debts_monthly": [], "goals": [],
        "already_spent": 0, "savings_from_last_period": 0,
    }, ensure_ascii=False)

    with patch.object(finance, "_load_budget_data", AsyncMock(return_value=empty_budget)), \
         patch.object(finance, "_period_spending", AsyncMock(return_value=({}, 0.0))), \
         patch.object(finance, "ask_claude", AsyncMock(return_value=plan_json)):
        await finance._run_budget_analysis(msg, uid)

    _, kwargs = loading.edit_text.call_args
    kb = kwargs["reply_markup"]
    all_cb = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "bsetup_accept" in all_cb
    assert "bsetup_variant_a" not in all_cb
    assert "bsetup_variant_b" not in all_cb

    text = loading.edit_text.call_args.args[0]
    assert "жёстко" in text  # discretionary 18 000 < 25 500 → плашка


# ── Разовые из composite-дампа НЕ становятся постоянными при Принятии ───────
#
# Баг: Кай в composite-дампе /budget помечает «разовый: билет 15000», но JSON
# Sonnet имел один массив "fixed" — разовые уходили туда и при _save_budget_plan
# писались в Память как постоянно_* НАВСЕГДА.

@pytest.mark.asyncio
async def test_save_budget_plan_one_time_written_as_finance_not_permanent(tmp_budget_db):
    from nexus.handlers import finance
    from core.repos import memory_repo as mrmod

    uid = 999_400
    plan = {
        "fixed": [
            {"name": "квартира", "category": "🏠 Жильё", "amount": 30000},
            {"name": "подписки", "category": "💻 Подписки", "amount": 5000},
        ],
        "fixed_total": 35000,
        "one_time": [
            {"name": "билет в питер", "category": "🚕 Транспорт", "amount": 15000},
            {"name": "госпошлина", "category": "💳 Прочее", "amount": 3500},
        ],
        "one_time_total": 18500,
    }
    _seed_state(uid, {"plan": plan, "notion_uid": "u-1", "state": "has_plan", "msg_id": 0})

    loading = await _fake_loading()
    msg = MagicMock()
    msg.chat.id = 1
    msg.bot = AsyncMock()
    msg.answer = AsyncMock(return_value=loading)

    mem_keys = []
    ot_writes = []

    async def cap_mem(key, fact, notion_uid=""):
        mem_keys.append(key)

    async def cap_ot(desc, amount, category="💳 Прочее", user_notion_id="", bot_label="☀️ Nexus", uid=0):
        ot_writes.append((desc, amount, category))
        return "pg-tx"

    with patch.object(finance, "_save_memory_entry", AsyncMock(side_effect=cap_mem)), \
         patch.object(finance, "_write_one_time_expense", AsyncMock(side_effect=cap_ot)), \
         patch.object(finance, "_get_limits", AsyncMock(return_value={})), \
         patch.object(finance, "build_budget_message", AsyncMock(return_value="ok")), \
         patch.object(mrmod._repo, "find_by_key_prefixes", AsyncMock(return_value=[])), \
         patch.object(mrmod._repo, "set_active", AsyncMock()):
        await finance._save_budget_plan(msg, uid)

    perm_keys = [k for k in mem_keys if k.startswith("постоянно_")]
    assert len(perm_keys) == 2, f"постоянно_* только для fixed, получили {perm_keys}"
    assert not any(w in k for k in perm_keys for w in ("билет", "питер", "госпошлин"))

    assert len(ot_writes) == 2
    assert {(d, a) for d, a, _ in ot_writes} == {("билет в питер", 15000), ("госпошлина", 3500)}
    assert dict((d, c) for d, _, c in ot_writes)["билет в питер"] == "🚕 Транспорт"


def test_format_plan_fixed_total_excludes_one_time_regression():
    """Регресс кейса из бага: 3 «фикс:» + 3 «разовый:» вперемешку →
    fixed_total = 54 650₽ (без разовых), НЕ 83 150₽."""
    from nexus.handlers.finance import _format_plan

    plan = {
        "income_total": 200000,
        "fixed": [
            {"name": "квартира", "category": "🏠 Жильё", "amount": 40000},
            {"name": "коммуналка", "category": "🏠 Жильё", "amount": 8000},
            {"name": "подписки", "category": "💻 Подписки", "amount": 6650},
        ],
        "fixed_total": 54650,
        "one_time": [
            {"name": "билет", "category": "🚕 Транспорт", "amount": 15000},
            {"name": "виза", "category": "💳 Прочее", "amount": 8000},
            {"name": "страховка", "category": "💳 Прочее", "amount": 5500},
        ],
        "one_time_total": 28500,
        "already_spent": 28500,
    }
    out = _format_plan(plan)
    assert "🔒 Фикс: 54,650₽" in out
    assert "83,150" not in out
    assert "📤 Разовые в этом периоде: 28,500₽" in out


# ── Нулевые категории лимитов не пропадают из вывода ─────────────────────────

def test_limits_fields_keeps_zero_categories():
    """_limits_fields показывает ВСЕ категории compute_limits, включая нулевые —
    прозрачность: видно, что категория получила 0₽ осознанно, а не потерялась."""
    from nexus.handlers.finance import _limits_fields
    from core.budget import PRIORITY_CHAIN, CAT_PRODUCTS, CAT_HABITS

    # discretionary 15 350 → priority-цепочка вся по нулям, продукты/привычки делят пополам
    fields = _limits_fields(15350, 0)
    by_cat = {i["category"]: i["amount"] for i in fields["limits"]}
    for cat in PRIORITY_CHAIN:
        assert cat in by_cat, f"категория {cat} потерялась"
        assert by_cat[cat] == 0
    assert by_cat[CAT_PRODUCTS] == 6425
    assert by_cat[CAT_HABITS] == 6425
    # транспорт 1500 + продукты 6425 + привычки 6425; нули цепочки не влияют
    assert fields["limits_total"] == 1500 + 6425 + 6425
    assert fields["impulse_budget"] == 1000


def test_limits_fields_all_zero_when_no_money():
    from nexus.handlers.finance import _limits_fields
    fields = _limits_fields(0, 0)
    assert len(fields["limits"]) == 8  # все переменные категории, кроме импульсивных
    assert all(i["amount"] == 0 for i in fields["limits"])
    assert fields["limits_total"] == 0


# ── Дедуп долга, продублированного Sonnet в оба массива ─────────────────────

def test_format_plan_dedups_debt_in_both_arrays():
    """Sonnet положил один долг и в debts_monthly, и в queued_debts →
    рендер показывает его ОДИН раз (версию с платежом)."""
    from nexus.handlers.finance import _format_plan

    plan = {
        "income_total": 80000, "fixed_total": 0,
        "debts_monthly": [{"name": "Аня", "total": 50000, "monthly": 20000, "strategy": ""}],
        "queued_debts": [{"name": "Аня", "total": 50000, "strategy": "Отложен"}],
    }
    out = _format_plan(plan)
    assert out.count("Аня") == 1
    assert "платёж 20,000₽/мес" in out
    assert "Отложен" not in out


def test_apply_computed_limits_dedups_queued():
    from nexus.handlers.finance import _apply_computed_limits

    plan = {
        "income_total": 80000, "fixed_total": 0,
        "debts_monthly": [{"name": "Петя", "monthly": 8000, "deadline": "май 2026"}],
        "queued_debts": [{"name": "Петя", "monthly": 0, "strategy": "Отложен"},
                         {"name": "Лена", "monthly": 0, "deadline": "август 2026"}],
    }
    _apply_computed_limits(plan)
    names = [q.get("name") for q in plan["queued_debts"]]
    assert names == ["Лена"]  # дубль Пети вычищен




# ── Позиции разовых расходов видны в плане, не только итоговая сумма ────────

def test_format_plan_lists_one_time_line_items():
    from nexus.handlers.finance import _format_plan

    plan = {
        "income_total": 100000, "fixed_total": 0,
        "already_spent": 12000, "one_time_total": 12000,
        "one_time": [
            {"name": "билет в Питер", "category": "🚕 Транспорт", "amount": 8000},
            {"name": "госпошлина", "category": "📄 Документы", "amount": 4000},
        ],
    }
    out = _format_plan(plan)
    assert "🚕 Транспорт билет в Питер — 8,000₽" in out
    assert "📄 Документы госпошлина — 4,000₽" in out


def test_format_plan_lists_one_time_items_when_also_real_spent():
    from nexus.handlers.finance import _format_plan

    plan = {
        "income_total": 100000, "fixed_total": 0,
        "already_spent": 20000, "one_time_total": 8000,
        "one_time": [{"name": "виза", "category": "📄 Документы", "amount": 8000}],
    }
    out = _format_plan(plan)
    assert "📤 Разовые из этого плана: 8,000₽" in out
    assert "📄 Документы виза — 8,000₽" in out


# ── ✅ Принять закрывает сессию сам (тот же баг, что кнопка «Закрыть план») ──

@pytest.mark.asyncio
async def test_accept_closes_session_next_message_not_intercepted(tmp_budget_db):
    """После успешного ✅ Принять _budget_get(uid) → None; следующее произвольное
    сообщение («подушка 300000») идёт ОБЫЧНЫМ путём (cushion regex), а не как
    КОРРЕКТИРОВКА бюджета через Sonnet."""
    from nexus.handlers import finance

    uid = 999_500
    _seed_state(uid, {
        "plan": {"income_total": 100000, "fixed": [], "goals": [], "limits": []},
        "notion_uid": "u-1", "state": "has_plan", "msg_id": 0,
    })

    loading = await _fake_loading()
    call = MagicMock()
    call.from_user.id = uid
    call.answer = AsyncMock()
    call.message = MagicMock()
    call.message.chat.id = 1
    call.message.bot = AsyncMock()
    call.message.answer = AsyncMock(return_value=loading)

    with patch.object(finance, "_save_memory_entry", AsyncMock()), \
         patch.object(finance, "_write_one_time_expense", AsyncMock(return_value="tx")), \
         patch.object(finance, "_get_limits", AsyncMock(return_value={})), \
         patch.object(finance, "build_budget_message", AsyncMock(return_value="ok")), \
         patch("core.repos.memory_repo._repo.find_by_key_prefixes", AsyncMock(return_value=[])), \
         patch("core.repos.memory_repo._repo.set_active", AsyncMock()), \
         patch("core.repos.pg_debts_repo._repo.upsert", AsyncMock()):
        await finance.on_budget_accept(call)

    assert finance._budget_get(uid) is None, "сессия должна закрыться после Принять"

    msg = _make_message(uid, "подушка 300000")
    with patch("nexus.handlers.finance._run_budget_analysis", AsyncMock()) as m_analysis:
        result = await finance.handle_budget_setup_text(msg, "u-1")

    assert result is False, "бюджет не должен перехватывать — сессии больше нет"
    m_analysis.assert_not_called()


@pytest.mark.asyncio
async def test_accept_failure_keeps_session_for_retry(tmp_budget_db):
    """Если _save_budget_plan бросил — сессия НЕ теряется, можно повторить."""
    from nexus.handlers import finance

    uid = 999_501
    _seed_state(uid, {
        "plan": {"income_total": 100000, "fixed": [], "goals": [], "limits": []},
        "notion_uid": "u-1", "state": "has_plan", "msg_id": 0,
    })

    call = MagicMock()
    call.from_user.id = uid
    call.answer = AsyncMock()
    call.message = MagicMock()

    with patch.object(finance, "_save_budget_plan", AsyncMock(side_effect=RuntimeError("boom"))):
        with pytest.raises(RuntimeError):
            await finance.on_budget_accept(call)

    assert finance._budget_get(uid) is not None, "неудачное сохранение не должно терять данные"
