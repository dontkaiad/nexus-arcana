"""tests/test_finance_budget_checklist_tasks.py

При ✅ Принять бюджетного плана (_save_budget_plan) создаются две
задачи-чеклиста «Оплатить Фикс — {месяц год}» / «Оплатить Разовые — {месяц
год}» с подзадачами (🗒️ Списки relation task_rel, тот же паттерн, что у
«📋 Подзадачи» — core/subtasks_handler.py). Дедлайн = конец периода.
Повторное Принятие в том же периоде обновляет существующие задачи, не
плодит дубли. Пустой fixed/one_time → соответствующая задача не создаётся.
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


def _msg(uid: int):
    m = MagicMock()
    m.from_user.id = uid
    m.chat.id = 1
    m.bot = AsyncMock()
    return m


class _FakeTask:
    def __init__(self, id_, title):
        self.id = id_
        self.title = title


def _base_mocks():
    """Общий набор моков, не относящихся к чеклист-задачам."""
    from core.repos import memory_repo as mrmod
    from nexus.handlers import finance
    return [
        patch.object(finance, "_save_memory_entry", AsyncMock()),
        patch.object(finance, "_write_one_time_expense", AsyncMock()),
        patch.object(finance, "_save_finance", AsyncMock()),
        patch.object(finance, "_get_limits", AsyncMock(return_value={})),
        patch.object(finance, "build_budget_message", AsyncMock(return_value="ok")),
        patch.object(mrmod._repo, "find_by_key_prefixes", AsyncMock(return_value=[])),
        patch.object(mrmod._repo, "set_active", AsyncMock()),
        patch.object(finance, "_get_payday", AsyncMock(return_value=1)),
    ]


@pytest.mark.asyncio
async def test_accept_creates_two_checklist_tasks_with_subtasks(tmp_budget_db, monkeypatch):
    from nexus.handlers import finance
    from nexus.handlers import tasks as tasks_mod
    from nexus.repos import pg_tasks_repo as pgt_mod
    from nexus.repos import tasks_repo as tr_mod
    from core.repos import lists_repo as lr_mod

    uid = 999_500
    plan = {
        "fixed": [
            {"name": "Аренда Питер", "category": "🏠 Жильё", "amount": 20000},
            {"name": "Подписки", "category": "💻 Подписки", "amount": 1500},
        ],
        "fixed_total": 21500,
        "one_time": [
            {"name": "Виза", "category": "💳 Прочее", "amount": 3500},
        ],
        "one_time_total": 3500,
    }
    _seed_state(uid, {"plan": plan, "notion_uid": "u-1", "state": "has_plan", "msg_id": 0})

    loading = await _fake_loading()
    msg = _msg(uid)
    msg.answer = AsyncMock(return_value=loading)

    created_tasks = []
    added_items = []

    async def fake_find_by_title(self, query, user_notion_id=""):
        return []  # ничего не существует — обе задачи новые

    async def fake_create(db_id, props):
        created_tasks.append(props)
        return str(100 + len(created_tasks))

    async def fake_add(items, list_type, bot_name, user_page_id):
        added_items.append((list_type, bot_name, items))
        return [{"id": f"i{i}", "name": it["name"]} for i, it in enumerate(items)]

    with patch.object(pgt_mod.PgTasksRepo, "find_by_title", fake_find_by_title), \
         patch.object(tr_mod._repo, "create", fake_create), \
         patch.object(lr_mod._repo, "add", fake_add), \
         patch.object(lr_mod._repo, "get", AsyncMock(return_value=[])), \
         patch.object(lr_mod._repo, "archive", AsyncMock()) as m_archive, \
         patch.object(tasks_mod, "_schedule_reminder", AsyncMock()), \
         patch.object(tasks_mod, "_schedule_deadline_check", AsyncMock()):
        with _apply(_base_mocks()):
            await finance._save_budget_plan(msg, uid)

    m_archive.assert_not_called()  # ничего не существовало — нечего архивировать
    assert len(created_tasks) == 2

    titles = [p["Задача"]["title"][0]["text"]["content"] for p in created_tasks]
    fixed_title = next(t for t in titles if "Фикс" in t)
    one_time_title = next(t for t in titles if "Разовые" in t)
    assert fixed_title.startswith("Оплатить Фикс — ")
    assert one_time_title.startswith("Оплатить Разовые — ")

    assert len(added_items) == 2
    fixed_items = next(items for lt, bn, items in added_items
                        if items and items[0]["name"].startswith("Аренда"))
    names = {it["name"] for it in fixed_items}
    assert names == {"Аренда Питер — 20000₽", "Подписки — 1500₽"}

    one_time_call = next(items for lt, bn, items in added_items
                          if items and items[0]["name"].startswith("Виза"))
    assert [it["name"] for it in one_time_call] == ["Виза — 3500₽"]


@pytest.mark.asyncio
async def test_reaccept_same_period_updates_not_duplicates(tmp_budget_db):
    """Повторное Принятие в том же периоде — задача уже существует (найдена по
    точному title) → подзадачи пересоздаются (архивируются старые + новые
    добавляются), новая задача НЕ создаётся."""
    from nexus.handlers import finance
    from nexus.handlers import tasks as tasks_mod
    from nexus.repos import pg_tasks_repo as pgt_mod
    from nexus.repos import tasks_repo as tr_mod
    from core.repos import lists_repo as lr_mod

    uid = 999_501
    plan = {
        "fixed": [{"name": "Аренда", "category": "🏠 Жильё", "amount": 25000}],
        "fixed_total": 25000,
        "one_time": [],
        "one_time_total": 0,
    }
    _seed_state(uid, {"plan": plan, "notion_uid": "u-1", "state": "has_plan", "msg_id": 0})

    loading = await _fake_loading()
    msg = _msg(uid)
    msg.answer = AsyncMock(return_value=loading)

    # Существующая задача найдётся ТОЛЬКО для точного совпадения title.
    existing_holder = {}

    async def fake_find_by_title(self, query, user_notion_id=""):
        title = existing_holder.get("title")
        if title:
            return [_FakeTask("existing-42", title)]
        return []

    create_calls = []

    async def fake_create(db_id, props):
        title = props["Задача"]["title"][0]["text"]["content"]
        existing_holder["title"] = title  # чтобы второй _save_budget_plan нашёл её
        create_calls.append(title)
        return "existing-42"

    old_items = [
        {"id": "old-1", "name": "Аренда — 20000₽", "task_rel": "existing-42"},
        {"id": "other-1", "name": "чужой пункт", "task_rel": "не-та-задача"},
    ]
    added_items = []

    async def fake_add(items, list_type, bot_name, user_page_id):
        added_items.append(items)
        return []

    archived_ids = []

    async def fake_archive(ids):
        archived_ids.extend(ids)
        return len(ids)

    with patch.object(pgt_mod.PgTasksRepo, "find_by_title", fake_find_by_title), \
         patch.object(tr_mod._repo, "create", fake_create), \
         patch.object(lr_mod._repo, "add", fake_add), \
         patch.object(lr_mod._repo, "get", AsyncMock(return_value=old_items)), \
         patch.object(lr_mod._repo, "archive", fake_archive), \
         patch.object(tasks_mod, "_schedule_reminder", AsyncMock()), \
         patch.object(tasks_mod, "_schedule_deadline_check", AsyncMock()):
        with _apply(_base_mocks()):
            await finance._save_budget_plan(msg, uid)

        # Первое Принятие создало задачу. Повторное Принятие (правка суммы) —
        # тот же период → тот же title → должна найтись, не создаться заново.
        plan["fixed"][0]["amount"] = 27000
        _seed_state(uid, {"plan": plan, "notion_uid": "u-1", "state": "has_plan", "msg_id": 0})
        with _apply(_base_mocks()):
            await finance._save_budget_plan(msg, uid)

    assert len(create_calls) == 1  # НЕ дубль — второй раз create не вызывался
    # Только пункт этой самой задачи (task_rel=existing-42) архивирован — не чужой.
    assert archived_ids == ["old-1"]
    # Новый список подзадач добавлен с обновлённой суммой.
    last_added = added_items[-1]
    assert [it["name"] for it in last_added] == ["Аренда — 27000₽"]


@pytest.mark.asyncio
async def test_empty_fixed_or_one_time_skips_that_task(tmp_budget_db):
    """План без fixed (пусто) → задача «Оплатить Фикс» не создаётся вообще.
    one_time непустой → «Оплатить Разовые» создаётся."""
    from nexus.handlers import finance
    from nexus.handlers import tasks as tasks_mod
    from nexus.repos import pg_tasks_repo as pgt_mod
    from nexus.repos import tasks_repo as tr_mod
    from core.repos import lists_repo as lr_mod

    uid = 999_502
    plan = {
        "fixed": [], "fixed_total": 0,
        "one_time": [{"name": "Ремонт обуви", "category": "💳 Прочее", "amount": 1200}],
        "one_time_total": 1200,
    }
    _seed_state(uid, {"plan": plan, "notion_uid": "u-1", "state": "has_plan", "msg_id": 0})

    loading = await _fake_loading()
    msg = _msg(uid)
    msg.answer = AsyncMock(return_value=loading)

    create_calls = []

    async def fake_create(db_id, props):
        create_calls.append(props["Задача"]["title"][0]["text"]["content"])
        return "t-1"

    with patch.object(pgt_mod.PgTasksRepo, "find_by_title", AsyncMock(return_value=[])), \
         patch.object(tr_mod._repo, "create", fake_create), \
         patch.object(lr_mod._repo, "add", AsyncMock(return_value=[])), \
         patch.object(lr_mod._repo, "get", AsyncMock(return_value=[])), \
         patch.object(lr_mod._repo, "archive", AsyncMock()), \
         patch.object(tasks_mod, "_schedule_reminder", AsyncMock()), \
         patch.object(tasks_mod, "_schedule_deadline_check", AsyncMock()):
        with _apply(_base_mocks()):
            await finance._save_budget_plan(msg, uid)

    assert len(create_calls) == 1
    assert "Разовые" in create_calls[0]
    assert not any("Фикс" in t for t in create_calls)


# ── helper: apply a list of patch context managers together ──────────────────

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
