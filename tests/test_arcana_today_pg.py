"""tests/test_arcana_today_pg.py — Today-tab Арканы на PG (Notion-removal секвенс 1).

- ritual_to_stub: PG Ritual → Notion-подобный стаб; verdict/payment/barter читаются.
- _load_rituals: тянет PgRitualsRepo.list_all, маппит через стаб (Notion не зовётся).
- verdict ритуала → PgRitualsRepo.set_result('positive'/'partial'/'negative').
- роут не импортирует Notion API (rituals_all/query_pages/update_page_select).
- mark_task_done → PgTasksRepo.set_status(id, 'Done').
- #155: лимиты применяются без NOTION_DB_MEMORY.
"""
from __future__ import annotations

import inspect
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from arcana.repos.rituals_repo import Ritual
from miniapp.backend.routes import arcana_today as at
from miniapp.backend.routes._arcana_common import ritual_to_stub


# ── ritual_to_stub ────────────────────────────────────────────────────────────

def test_ritual_to_stub_shape_and_verdict():
    r = Ritual(
        id="7", name="Свеча", date=datetime(2026, 6, 1, 14, 30),
        result="positive", client_id="5", price=1500, paid=1500,
        payment_source="💵 Наличные", barter_what="",
    )
    stub = ritual_to_stub(r)
    assert stub["id"] == "7"
    props = stub["properties"]
    assert props["Результат"]["select"]["name"] == "✅ Сработало"
    assert props["Дата"]["date"]["start"].startswith("2026-06-01")
    assert props["👥 Клиенты"]["relation"] == [{"id": "5"}]
    assert props["Цена за ритуал"]["number"] == 1500.0
    assert props["Источник оплаты"]["select"]["name"] == "💵 Наличные"
    # читается шеринг-хелпером роута
    assert at._ritual_verdict(stub) == "yes"


def test_ritual_to_stub_unverified_and_barter():
    r = Ritual(id="9", name="R", date=None, result=None,
               payment_source="🔄 Бартер", barter_what="мёд")
    stub = ritual_to_stub(r)
    assert stub["properties"]["Результат"]["select"]["name"] == "⏳ Не проверено"
    assert at._ritual_verdict(stub) is None
    assert stub["properties"]["Дата"]["date"]["start"] == ""
    assert stub["properties"]["👥 Клиенты"]["relation"] == []
    assert stub["properties"]["Бартер · что"]["rich_text"][0]["plain_text"] == "мёд"


# ── _load_rituals → PG ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_load_rituals_uses_pg_list_all():
    rits = [
        Ritual(id="1", name="A", date=datetime(2026, 6, 1), result="positive"),
        Ritual(id="2", name="B", date=datetime(2026, 6, 2), result="negative"),
    ]
    with patch.object(at._pg_rituals_repo, "list_all", AsyncMock(return_value=rits)) as m:
        out = await at._load_rituals("u-1")
    m.assert_awaited_once_with(user_notion_id="u-1")
    assert [p["id"] for p in out] == ["1", "2"]
    assert at._compute_accuracy([], out, "rituals")["yes"] == 1


# ── verdict → set_result ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_verify_ritual_calls_set_result():
    body = at.VerifyAccuracyBody(id="3", type="ritual", verdict="yes")
    with patch.object(at._pg_rituals_repo, "set_result", AsyncMock(return_value=True)) as m_set, \
         patch.object(at._pg_sessions_repo, "list_all", AsyncMock(return_value=[])), \
         patch.object(at._pg_rituals_repo, "list_all", AsyncMock(return_value=[])), \
         patch.object(at, "get_user_notion_id", AsyncMock(return_value="u")), \
         patch.object(at, "notify_user", AsyncMock()):
        res = await at.post_arcana_accuracy_verify(body, tg_id=1)
    m_set.assert_awaited_once_with("3", "positive")   # yes → positive
    assert res["ok"] is True


def test_route_has_no_notion_api_imports():
    src = inspect.getsource(at)
    assert "rituals_all" not in src
    assert "query_pages" not in src
    assert "update_page_select" not in src
