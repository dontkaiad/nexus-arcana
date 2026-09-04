"""tests/test_finance_user_tz.py

Границы дня/периода в финансах считаются по личному tz пользователя
(tz_{tg_id} → get_user_tz), а не по серверному MOSCOW_TZ (UTC+3).
Регресс: без заданного tz (дефолт 3) — поведение как было.
"""
from __future__ import annotations

from datetime import datetime as _real_dt, timezone as _tzc
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nexus.handlers import finance


def _frozen_dt(instant_utc):
    class _DT(_real_dt):
        @classmethod
        def now(cls, tz=None):
            return instant_utc.astimezone(tz) if tz is not None else instant_utc.replace(tzinfo=None)
    return _DT


# момент: у сервера (+3) ещё 30 июня 23:00, у юзера (+5) уже 1 июля 01:00
_INSTANT = _real_dt(2026, 6, 30, 20, 0, tzinfo=_tzc.utc)


def test_today_month_use_user_tz(monkeypatch):
    monkeypatch.setattr(finance, "datetime", _frozen_dt(_INSTANT))
    assert finance._today(3) == "2026-06-30"
    assert finance._today(5) == "2026-07-01"
    assert finance._today() == "2026-06-30"      # регресс: дефолт == 3
    assert finance._month(3) == "2026-06"
    assert finance._month(5) == "2026-07"


def test_period_bounds_use_user_tz(monkeypatch):
    monkeypatch.setattr(finance, "datetime", _frozen_dt(_INSTANT))

    s3, e3 = finance._period_bounds(1, tz_offset=3)
    s5, e5 = finance._period_bounds(1, tz_offset=5)

    assert (s3, e3) == ("2026-06-01", "2026-06-30")   # для сервера ещё июнь
    assert (s5, e5) == ("2026-07-01", "2026-07-31")   # для юзера уже июль
    assert finance._period_bounds(1) == (s3, e3)      # регресс: дефолт == 3


@pytest.mark.asyncio
async def test_save_finance_uses_user_tz_for_date(monkeypatch):
    monkeypatch.setattr(finance, "datetime", _frozen_dt(_INSTANT))

    captured = {}

    async def fake_create(db_id, **kw):
        captured.update(kw)
        return "pg-1"

    with patch.object(finance._repo, "create_entry", AsyncMock(side_effect=fake_create)), \
         patch.object(finance, "_get_user_tz", AsyncMock(return_value=5)):
        await finance._save_finance({"amount": 100, "description": "кофе"}, "db", uid=42)

    assert captured["date"] == "2026-07-01"


@pytest.mark.asyncio
async def test_save_finance_no_uid_defaults_to_msk(monkeypatch):
    monkeypatch.setattr(finance, "datetime", _frozen_dt(_INSTANT))

    captured = {}

    async def fake_create(db_id, **kw):
        captured.update(kw)
        return "pg-1"

    with patch.object(finance._repo, "create_entry", AsyncMock(side_effect=fake_create)), \
         patch.object(finance, "_get_user_tz", AsyncMock(return_value=99)) as m_tz:
        await finance._save_finance({"amount": 100, "description": "кофе"}, "db")  # uid=0

    m_tz.assert_not_called()
    assert captured["date"] == "2026-06-30"


@pytest.mark.asyncio
async def test_check_budget_limit_period_uses_user_tz(monkeypatch):
    """period_start в запросе трат считается по личному tz (через _period_bounds)."""
    monkeypatch.setattr(finance, "datetime", _frozen_dt(_INSTANT))

    seen = {}

    async def fake_query(**kw):
        seen.update(kw)
        return []

    msg = MagicMock()
    msg.from_user.id = 7
    msg.answer = AsyncMock()

    with patch.object(finance, "_get_limits", AsyncMock(return_value={"продукты": 10000.0})), \
         patch.object(finance._repo, "query_records", AsyncMock(side_effect=fake_query)), \
         patch.object(finance, "_get_payday", AsyncMock(return_value=1)), \
         patch.object(finance, "_load_budget_data", AsyncMock(return_value={"долги": [], "постоянные": []})), \
         patch.object(finance, "_calc_free_remaining", AsyncMock(return_value=None)):
        await finance._check_budget_limit("🍜 Продукты", msg, "u-1", amount=500, tz_offset=5)

    assert seen["date_from"] == "2026-07-01"   # период юзера, не сервера (2026-06-01)


@pytest.mark.asyncio
async def test_check_budget_limit_passes_user_notion_id(monkeypatch):
    """_check_budget_limit должен передавать user_notion_id в query_records —
    иначе запрос ищет записи с пустым user_notion_id и period_total всегда 0
    (баг: строка вызова была без этого аргумента, хотя функция его получает)."""
    monkeypatch.setattr(finance, "datetime", _frozen_dt(_INSTANT))

    seen = {}

    async def fake_query(**kw):
        seen.update(kw)
        return []

    msg = MagicMock()
    msg.from_user.id = 7
    msg.answer = AsyncMock()

    with patch.object(finance, "_get_limits", AsyncMock(return_value={"продукты": 10000.0})), \
         patch.object(finance._repo, "query_records", AsyncMock(side_effect=fake_query)), \
         patch.object(finance, "_get_payday", AsyncMock(return_value=1)), \
         patch.object(finance, "_load_budget_data", AsyncMock(return_value={"долги": [], "постоянные": []})), \
         patch.object(finance, "_calc_free_remaining", AsyncMock(return_value=None)):
        await finance._check_budget_limit("🍜 Продукты", msg, "u-1", amount=500, tz_offset=5)

    assert seen["user_notion_id"] == "u-1"


@pytest.mark.asyncio
async def test_check_budget_limit_period_total_isolated_between_users(monkeypatch):
    """period_total считает только записи ТЕКУЩЕГО пользователя. Регресс: раньше
    (без user_notion_id в query_records) чужие записи той же категории
    подмешивались бы в сумму — здесь два пользователя с одинаковыми
    категориями/суммами не должны смешиваться."""
    from core.repos import finance_repo as fr
    from core.repos.pg_finance_repo import BudgetEntry

    monkeypatch.setattr(finance, "datetime", _frozen_dt(_INSTANT))

    def make_entry(uid_marker):
        return BudgetEntry(
            id=f"e-{uid_marker}", description="кофе", amount=500.0,
            category="🍜 Продукты", type_="💸 Расход", source="",
            date="2026-06-15",
        )

    async def fake_nexus_query(date_from, date_to, type_, category, page_size, user_notion_id=""):
        if user_notion_id == "u-1":
            return [make_entry("u1-a"), make_entry("u1-b")]
        return []  # другой пользователь / без фильтра — ничего своего не находит

    async def fake_arcana_query(date_from, date_to, type_, category, page_size, user_notion_id=""):
        return []

    msg = MagicMock()
    msg.from_user.id = 7
    msg.answer = AsyncMock()

    period_totals = []

    def fake_log_info(fmt, *args):
        if fmt.startswith("_check_budget_limit: period_total="):
            period_totals.append(args[0])  # period_total значение

    with patch.object(fr._nexus_repo, "query", AsyncMock(side_effect=fake_nexus_query)), \
         patch.object(fr._arcana_repo, "query", AsyncMock(side_effect=fake_arcana_query)), \
         patch.object(finance, "_get_limits", AsyncMock(return_value={"продукты": 10000.0})), \
         patch.object(finance, "_get_payday", AsyncMock(return_value=1)), \
         patch.object(finance, "_load_budget_data", AsyncMock(return_value={"долги": [], "постоянные": []})), \
         patch.object(finance, "_calc_free_remaining", AsyncMock(return_value=None)), \
         patch.object(finance.logger, "info", side_effect=fake_log_info):
        await finance._check_budget_limit("🍜 Продукты", msg, "u-1", amount=500, tz_offset=5)
        await finance._check_budget_limit("🍜 Продукты", msg, "u-2", amount=500, tz_offset=5)

    # u-1 находит свои 2 записи по 500 = 1000; u-2 (другой пользователь,
    # те же категория/сумма в фикстуре) не подмешивает их себе — 0.
    assert period_totals == [1000.0, 0.0]


@pytest.mark.asyncio
async def test_build_budget_message_default_tz_regression(monkeypatch):
    """build_budget_message без явного tz → период по МСК, как раньше."""
    monkeypatch.setattr(finance, "datetime", _frozen_dt(_INSTANT))

    budget = {
        "доходы": [{"name": "зп", "amount": 100000}],
        "постоянные": [{"name": "аренда", "amount": 20000}],
        "цели": [], "долги": [], "лимиты": [],
    }
    calls = []

    async def fake_query(**kw):
        calls.append(kw.get("date_from"))
        return []

    with patch.object(finance, "_load_budget_data", AsyncMock(return_value=budget)), \
         patch.object(finance, "_get_payday", AsyncMock(return_value=1)), \
         patch.object(finance._repo, "query_records", AsyncMock(side_effect=fake_query)):
        await finance.build_budget_message("u-1")            # дефолт → МСК
        await finance.build_budget_message("u-1", tz_offset=5)  # личный tz

    assert calls[0] == "2026-06-01"   # для сервера ещё июнь
    assert calls[1] == "2026-07-01"   # для юзера уже июль
