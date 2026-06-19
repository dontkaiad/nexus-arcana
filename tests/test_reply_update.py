"""tests/test_reply_update.py — reply-правка записей через PG set_props (#156).

apply_updates диспатчит по page_type на per-domain PG-репозитории. Notion
не используется (модуль не импортирует update_page/match_select/retrieve).
- task   → PgTasksRepo.set_props (Notion-format props, PG резолвит select/date)
- client → PgClientsRepo.update_profile: contact/request/notes ДОПИСЫВАЮТСЯ
  (read-modify-write), new_type→type_code, new_name→name
- session→ PgSessionsRepo.set_props: notes→append Трактовки; client_name→
  find_or_create_client (PG) → client_id + type_code='client'; fail-closed без юзера
- ritual → PgRitualsRepo.set_props (forces/structure/...)
- work   → PgWorksRepo.set_props (category маппится, priority, deadline)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import core.reply_update as ru
from arcana.repos.clients_repo import Client


# ── task ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_task_reply_calls_pg_tasks_set_props():
    from nexus.repos.pg_tasks_repo import PgTasksRepo
    with patch.object(PgTasksRepo, "set_props", AsyncMock()) as m:
        applied = await ru.apply_updates(
            "42", "task", None,
            {"deadline": "2026-07-01 18:00", "priority": "срочно", "category": "Дом"},
        )
    m.assert_awaited_once()
    page_id, props = m.await_args.args
    assert page_id == "42"
    assert props["Дедлайн"] == {"date": {"start": "2026-07-01T18:00"}}
    assert props["Приоритет"] == {"select": {"name": "Срочно"}}
    assert props["Категория"] == {"select": {"name": "Дом"}}
    assert applied["Приоритет"] == "Срочно"


# ── client: append concatenates, not overwrites ──────────────────────────────

@pytest.mark.asyncio
async def test_client_reply_appends_notes():
    from arcana.repos.pg_clients_repo import PgClientsRepo
    cur = Client(id="7", name="X", contact="", request="", notes="старое", since="")
    with patch.object(PgClientsRepo, "find_by_id", AsyncMock(return_value=cur)), \
         patch.object(PgClientsRepo, "update_profile", AsyncMock()) as m:
        applied = await ru.apply_updates("7", "client", None, {"notes": "новое"})
    m.assert_awaited_once()
    assert m.await_args.kwargs["notes"] == "старое\nновое"   # дописали, не затёрли
    assert applied["notes"] == "+ новое"


@pytest.mark.asyncio
async def test_client_reply_type_and_name():
    from arcana.repos.pg_clients_repo import PgClientsRepo
    with patch.object(PgClientsRepo, "find_by_id", AsyncMock(return_value=None)), \
         patch.object(PgClientsRepo, "update_profile", AsyncMock()) as m:
        await ru.apply_updates(
            "7", "client", None, {"new_type": "Self", "new_name": "Кай"},
        )
    kw = m.await_args.kwargs
    assert kw["type_code"] == "self"
    assert kw["name"] == "Кай"


# ── session: append Трактовки + client_name resolve ──────────────────────────

@pytest.mark.asyncio
async def test_session_reply_appends_interpretation():
    from arcana.repos.pg_sessions_repo import PgSessionsRepo
    with patch.object(PgSessionsRepo, "set_props", AsyncMock()) as m:
        await ru.apply_updates(
            "3", "session", None, {"notes": "сбылось", "question": "Вопрос"}, "u-1",
        )
    kw = m.await_args.kwargs
    assert kw["append_interpretation"] == "сбылось"
    assert kw["question"] == "Вопрос"


@pytest.mark.asyncio
async def test_session_reply_client_name_resolves_and_sets_type():
    from arcana.repos.pg_sessions_repo import PgSessionsRepo
    with patch.object(PgSessionsRepo, "set_props", AsyncMock()) as m, \
         patch("core.notion_client.find_or_create_client",
               AsyncMock(return_value=("99", False))) as m_resolve:
        applied = await ru.apply_updates(
            "3", "session", None, {"client_name": "Маша"}, "u-1",
        )
    m_resolve.assert_awaited_once()
    kw = m.await_args.kwargs
    assert kw["client_id"] == "99"
    assert kw["type_code"] == "client"
    assert applied["Клиент"] == "Маша"


@pytest.mark.asyncio
async def test_session_client_name_fail_closed_no_user():
    from arcana.repos.pg_sessions_repo import PgSessionsRepo
    with patch.object(PgSessionsRepo, "set_props", AsyncMock()) as m, \
         patch("core.notion_client.find_or_create_client", AsyncMock()) as m_resolve:
        await ru.apply_updates("3", "session", None, {"client_name": "Маша"}, "")
    m_resolve.assert_not_called()   # без юзера клиента не резолвим
    m.assert_not_awaited()          # больше нечего писать → set_props не зовём


# ── ritual ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ritual_reply_set_props():
    from arcana.repos.pg_rituals_repo import PgRitualsRepo
    with patch.object(PgRitualsRepo, "set_props", AsyncMock()) as m:
        await ru.apply_updates(
            "5", "ritual", None,
            {"forces": "огонь", "duration_min": 30, "notes": "ok"},
        )
    page_id, = m.await_args.args
    kw = m.await_args.kwargs
    assert page_id == "5"
    assert kw["forces"] == "огонь"
    assert kw["duration_min"] == 30
    assert kw["notes"] == "ok"


# ── work ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_work_reply_set_props_maps_category():
    from arcana.repos.pg_works_repo import PgWorksRepo
    with patch.object(PgWorksRepo, "set_props", AsyncMock()) as m:
        applied = await ru.apply_updates(
            "8", "work", None,
            {"category": "ритуал", "priority": "важно", "deadline": "2026-07-02 10:00"},
        )
    kw = m.await_args.kwargs
    assert kw["category"] == "✨ Ритуал"            # замаппили
    assert kw["priority"] == "важно"                # raw — репо сам резолвит код
    assert kw["deadline"] == "2026-07-02T10:00"
    assert applied["Категория"] == "✨ Ритуал"


# ── dispatch guards ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_empty_updates_noop():
    assert await ru.apply_updates("1", "task", None, {}) == {}


@pytest.mark.asyncio
async def test_unknown_type_noop():
    assert await ru.apply_updates("1", "grimoire", None, {"x": 1}) == {}


def test_module_has_no_notion_write_imports():
    """Регресс #156: модуль не тянет Notion-запись на уровне модуля."""
    import inspect
    src = inspect.getsource(ru)
    assert "update_page" not in src
    assert "match_select" not in src
    assert "pages.retrieve" not in src
