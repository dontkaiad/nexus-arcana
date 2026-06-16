"""Mini App — списки и память.

GET/POST /api/lists (покупки/инвентарь/чеклисты, client-side type matching,
скрытие чеклистов закрытых родителей), GET/POST /api/memory (+ ADHD-профиль).

Собрано из wave2a / wave3 / wave5 / wave6 / wave8.62 при реорганизации
тестов по доменам.
"""
from __future__ import annotations

import json as _json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from miniapp.backend import cache
from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id
from nexus.repos.pg_tasks_repo import Task as PgTask


FAKE_TG_ID = 67686090
FAKE_NOTION_USER = "user-notion-id-42"


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    db_file = tmp_path / "adhd_cache.db"
    monkeypatch.setattr(cache, "_DB_PATH", str(db_file))
    cache._init_db()
    yield


@pytest.fixture
def client():
    app.dependency_overrides[current_user_id] = lambda: FAKE_TG_ID
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _today_date(tz: int = 3):
    return (datetime.now(timezone.utc) + timedelta(hours=tz)).date()


# ── helpers: fake Notion pages ───────────────────────────────────────────────

def _list_item(iid, name="item", *, type_="🛒 Покупки", status="Not started",
               cat=None, qty=None, note=None, expires=None, price=None,
               group="", bot=None):
    """Объединённый builder страницы 🗒️ Списки (wave2a + wave6 + wave8.62)."""
    return {
        "id": iid,
        "properties": {
            "Название": {"title": [{"plain_text": name}]},
            "Тип": {"select": {"name": type_}},
            "Статус": {"status": {"name": status}},
            "Категория": {"select": {"name": cat} if cat else None},
            "Количество": {"number": qty},
            "Цена": {"number": price},
            "Заметка": {"rich_text": [{"plain_text": note}] if note else []},
            "Срок годности": {"date": {"start": expires} if expires else None},
            "Повторяющийся": {"checkbox": False},
            "Группа": {"rich_text": [{"plain_text": group}] if group else []},
            "Бот": {"select": {"name": bot} if bot else None},
        },
    }


def _mem(mid, text, cat=None, related=None, key=None, actual=True):
    props = {
        "Текст": {"title": [{"plain_text": text}]},
        "Актуально": {"checkbox": actual},
    }
    if cat:
        props["Категория"] = {"select": {"name": cat}}
    if related:
        props["Связь"] = {"rich_text": [{"plain_text": related}]}
    if key:
        props["Ключ"] = {"rich_text": [{"plain_text": key}]}
    return {"id": mid, "properties": props}


def _page(pid: str, *, owner: str = FAKE_NOTION_USER, extra: dict | None = None) -> dict:
    props = {
        "🪪 Пользователи": {"relation": [{"id": owner}]},
        "Статус": {"status": {"name": "Not started"}},
        "Задача": {"title": [{"plain_text": "Test"}]},
    }
    if extra:
        props.update(extra)
    return {"id": pid, "properties": props}


def _parent_task_pg(title, *, status="Not started",
                    user_notion_id=FAKE_NOTION_USER) -> PgTask:
    return PgTask(
        id=f"parent-{title}",
        title=title,
        status=status,
        priority="🔴 Срочно",
        category="🏠 Дом",
        user_notion_id=user_notion_id,
    )


# ── GET /api/lists ───────────────────────────────────────────────────────────

def test_lists_buy_returns_items(client):
    from core.repos.pg_nexus_lists_repo import ListItem
    pg_items = [
        ListItem(id="l1", name="Молоко", list_type="покупки", status="not_started",
                 category="🍜 Продукты", quantity=1.0, note="Простоквашино"),
        ListItem(id="l2", name="Хлеб", list_type="покупки", status="done",
                 category="🍜 Продукты"),
    ]
    with patch("miniapp.backend.routes.lists._nexus_lists_repo") as mock_repo, \
         patch("miniapp.backend.routes.lists.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        mock_repo.get_summary_items = AsyncMock(return_value=pg_items)
        r = client.get("/api/lists?type=buy")

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["type"] == "buy"
    assert len(data["items"]) == 2
    milk = next(i for i in data["items"] if i["name"] == "Молоко")
    assert milk["cat"]["emoji"] == "🍜"
    assert milk["qty"] == 1
    bread = next(i for i in data["items"] if i["name"] == "Хлеб")
    assert bread["done"] is True


def test_lists_inv_sorts_by_expiry(client):
    from core.repos.pg_nexus_lists_repo import ListItem
    soon = (datetime.now().date() + timedelta(days=5)).isoformat()
    later = (datetime.now().date() + timedelta(days=30)).isoformat()
    pg_items = [
        ListItem(id="a", name="Потом", list_type="инвентарь", status="not_started", expires_at=later),
        ListItem(id="b", name="Скоро", list_type="инвентарь", status="not_started", expires_at=soon),
        ListItem(id="c", name="Без срока", list_type="инвентарь", status="not_started", expires_at=""),
    ]
    with patch("miniapp.backend.routes.lists._nexus_lists_repo") as mock_repo, \
         patch("miniapp.backend.routes.lists.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        mock_repo.get_summary_items = AsyncMock(return_value=pg_items)
        r = client.get("/api/lists?type=inv")

    assert r.status_code == 200
    items = r.json()["items"]
    assert [i["name"] for i in items] == ["Скоро", "Потом", "Без срока"]


def test_lists_q_filter(client):
    from core.repos.pg_nexus_lists_repo import ListItem
    pg_items = [
        ListItem(id="a", name="Молоко", list_type="покупки", status="not_started", note="3,2%"),
        ListItem(id="b", name="Хлеб", list_type="покупки", status="not_started", note="бородинский"),
        ListItem(id="c", name="Сыр", list_type="покупки", status="not_started"),
    ]
    with patch("miniapp.backend.routes.lists._nexus_lists_repo") as mock_repo, \
         patch("miniapp.backend.routes.lists.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        mock_repo.get_summary_items = AsyncMock(return_value=pg_items)
        r = client.get("/api/lists?type=buy&q=мол")

    items = r.json()["items"]
    assert [i["name"] for i in items] == ["Молоко"]


def test_lists_invalid_type(client):
    r = client.get("/api/lists?type=bogus")
    assert r.status_code == 400


def test_lists_empty(client):
    with patch("miniapp.backend.routes.lists._nexus_lists_repo") as mock_repo, \
         patch("miniapp.backend.routes.lists.get_user_notion_id",
               AsyncMock(return_value="")):
        mock_repo.get_summary_items = AsyncMock(return_value=[])
        r = client.get("/api/lists?type=check")
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "check"
    assert body["items"] == []
    # v1.2: пустой summary — все нули
    assert body.get("summary", {}).get("count_total") == 0


def test_lists_401_without_init_data():
    app.dependency_overrides.clear()
    c = TestClient(app)
    assert c.get("/api/lists").status_code == 401


def test_lists_filter_allows_empty_bot(client):
    """wave5.4→PG: чеклисты без поля Бот видны — PG не фильтрует по Бот."""
    from core.repos.pg_nexus_lists_repo import ListItem
    pg_items = [
        ListItem(id="c1", name="пункт без бота", list_type="чеклист", status="not_started"),
    ]
    with patch("miniapp.backend.routes.lists._nexus_lists_repo") as mock_repo, \
         patch("miniapp.backend.routes.lists.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        mock_repo.get_summary_items = AsyncMock(return_value=pg_items)
        r = client.get("/api/lists?type=check")

    assert r.status_code == 200
    # PG не фильтрует по полю Бот — все items юзера возвращаются
    assert len(r.json()["items"]) == 1


# ── GET /api/lists — client-side type matching (wave6) ──────────────────────

def test_lists_check_loads_with_exact_emoji(client):
    """PG path: get_summary_items фильтрует по list_type на стороне БД."""
    from core.repos.pg_nexus_lists_repo import ListItem
    # Покупки-тип не возвращаются — PG уже отфильтровал по list_type="📋 Чеклист"
    pg_items = [
        ListItem(id="c1", name="купить молоко", list_type="чеклист", status="not_started"),
    ]
    with patch("miniapp.backend.routes.lists._nexus_lists_repo") as mock_repo, \
         patch("miniapp.backend.routes.lists.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        mock_repo.get_summary_items = AsyncMock(return_value=pg_items)
        r = client.get("/api/lists?type=check")

    assert r.status_code == 200
    ids = [i["id"] for i in r.json()["items"]]
    assert "c1" in ids


def test_lists_check_loads_when_type_has_diff_spacing(client):
    """PG path: list_type нормализован при записи, оба пункта чеклиста видны."""
    from core.repos.pg_nexus_lists_repo import ListItem
    pg_items = [
        ListItem(id="c1", name="план дня", list_type="чеклист", status="not_started"),
        ListItem(id="c2", name="утро ритуал", list_type="чеклист", status="not_started"),
    ]
    with patch("miniapp.backend.routes.lists._nexus_lists_repo") as mock_repo, \
         patch("miniapp.backend.routes.lists.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        mock_repo.get_summary_items = AsyncMock(return_value=pg_items)
        r = client.get("/api/lists?type=check")

    assert r.status_code == 200
    ids = {i["id"] for i in r.json()["items"]}
    assert ids == {"c1", "c2"}


def test_lists_inv_matches_partial_keyword(client):
    """PG path: оба инвентарных пункта возвращаются."""
    from core.repos.pg_nexus_lists_repo import ListItem
    pg_items = [
        ListItem(id="i1", name="гречка", list_type="инвентарь", status="not_started"),
        ListItem(id="i2", name="перец", list_type="инвентарь", status="not_started"),
    ]
    with patch("miniapp.backend.routes.lists._nexus_lists_repo") as mock_repo, \
         patch("miniapp.backend.routes.lists.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        mock_repo.get_summary_items = AsyncMock(return_value=pg_items)
        r = client.get("/api/lists?type=inv")

    assert r.status_code == 200
    assert len(r.json()["items"]) == 2


def test_lists_archived_filtered_out(client):
    from core.repos.pg_nexus_lists_repo import ListItem
    pg_items = [
        ListItem(id="a1", name="активное", list_type="чеклист", status="not_started"),
        ListItem(id="a2", name="архив", list_type="чеклист", status="archived"),
    ]
    with patch("miniapp.backend.routes.lists._nexus_lists_repo") as mock_repo, \
         patch("miniapp.backend.routes.lists.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        mock_repo.get_summary_items = AsyncMock(return_value=pg_items)
        r = client.get("/api/lists?type=check")

    ids = [i["id"] for i in r.json()["items"]]
    assert ids == ["a1"]


# ── GET /api/lists — чеклисты закрытых родителей (wave8.62) ─────────────────

def test_check_items_with_done_parent_are_hidden(client):
    """Чеклист задачи в статусе Done не должен возвращаться endpoint'ом."""
    from core.repos.pg_nexus_lists_repo import ListItem

    pg_items = [
        ListItem(id="c1", name="помыть холодильник", list_type="чеклист", status="not_started",
                 group_name="Генеральная уборка"),
        ListItem(id="c2", name="купить молоко", list_type="чеклист", status="not_started",
                 group_name="Покупки на неделю"),
        ListItem(id="c3", name="выкинуть просрочку", list_type="чеклист", status="not_started",
                 group_name="Архив прошлый год"),
    ]
    pg_tasks = [
        _parent_task_pg("Генеральная уборка", status="Done"),
        _parent_task_pg("Покупки на неделю", status="Not started"),
        _parent_task_pg("Архив прошлый год", status="Archived"),
    ]

    with patch("miniapp.backend.routes.lists._nexus_lists_repo") as mock_repo, \
         patch("miniapp.backend.routes.lists._tasks_repo.list_all",
               AsyncMock(return_value=pg_tasks)), \
         patch("miniapp.backend.routes.lists.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)), \
         patch("miniapp.backend.routes.lists.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))):
        mock_repo.get_summary_items = AsyncMock(return_value=pg_items)
        r = client.get("/api/lists?type=check")

    assert r.status_code == 200, r.text
    items = r.json()["items"]
    ids = [i["id"] for i in items]
    # c1 (parent Done) и c3 (parent Archived) спрятаны, c2 (parent Not started) виден
    assert ids == ["c2"]


def test_check_items_match_parent_case_insensitive(client):
    """wave8.62.1: title задачи и Группа айтема могут отличаться регистром/пробелами —
    parent должен всё равно приклеиться, а Done-родитель — спрятать item."""
    from core.repos.pg_nexus_lists_repo import ListItem

    pg_items = [
        ListItem(id="c-mismatch", name="помыть холодильник", list_type="чеклист",
                 status="not_started", group_name="  сделать генеральную уборку кухни  "),
    ]
    pg_tasks = [
        _parent_task_pg("Сделать Генеральную Уборку Кухни", status="Done"),
    ]

    with patch("miniapp.backend.routes.lists._nexus_lists_repo") as mock_repo, \
         patch("miniapp.backend.routes.lists._tasks_repo.list_all",
               AsyncMock(return_value=pg_tasks)), \
         patch("miniapp.backend.routes.lists.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)), \
         patch("miniapp.backend.routes.lists.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))):
        mock_repo.get_summary_items = AsyncMock(return_value=pg_items)
        r = client.get("/api/lists?type=check")

    assert r.status_code == 200
    items = r.json()["items"]
    # parent Done должен быть найден несмотря на mismatch регистра/пробелов → item скрыт
    assert items == []


def test_check_items_match_parent_without_user_relation(client):
    """PG path: _tasks_repo.list_all дёргается с user_notion_id юзера.
    Если parent-задача найдена в PG → Done-родитель скрывает item."""
    from core.repos.pg_nexus_lists_repo import ListItem

    pg_items = [
        ListItem(id="c-orphan-parent", name="пункт", list_type="чеклист",
                 status="not_started", group_name="Уборка"),
    ]
    # В PG Done-задача матчится по title — user_notion_id не нужен как OR-условие
    pg_tasks = [_parent_task_pg("Уборка", status="Done")]

    captured_uid = {}

    async def fake_list_all(user_notion_id):
        captured_uid["v"] = user_notion_id
        return pg_tasks

    with patch("miniapp.backend.routes.lists._nexus_lists_repo") as mock_repo, \
         patch("miniapp.backend.routes.lists._tasks_repo.list_all",
               side_effect=fake_list_all), \
         patch("miniapp.backend.routes.lists.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)), \
         patch("miniapp.backend.routes.lists.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))):
        mock_repo.get_summary_items = AsyncMock(return_value=pg_items)
        r = client.get("/api/lists?type=check")

    assert r.status_code == 200
    # list_all вызван с user_notion_id
    assert captured_uid["v"] == FAKE_NOTION_USER
    # item с parent Done спрятан
    assert r.json()["items"] == []


def test_check_items_with_group_param_show_even_if_parent_closed(client):
    """wave8.62.2: TaskSheet закрытой задачи запрашивает /api/lists?type=check&group=<title>
    чтобы показать subtasks read-only. Фильтр closed-parent НЕ должен прятать items
    в этом случае — иначе секция Чеклист в закрытом TaskSheet всегда пустая."""
    from core.repos.pg_nexus_lists_repo import ListItem

    pg_items = [
        ListItem(id="c-done-parent-1", name="помыть холодильник", list_type="чеклист",
                 status="not_started", group_name="Уборка кухни"),
        ListItem(id="c-done-parent-2", name="выкинуть просрочку", list_type="чеклист",
                 status="not_started", group_name="Уборка кухни"),
    ]
    pg_tasks = [_parent_task_pg("Уборка кухни", status="Done")]

    with patch("miniapp.backend.routes.lists._nexus_lists_repo") as mock_repo, \
         patch("miniapp.backend.routes.lists._tasks_repo.list_all",
               AsyncMock(return_value=pg_tasks)), \
         patch("miniapp.backend.routes.lists.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)), \
         patch("miniapp.backend.routes.lists.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))):
        mock_repo.get_summary_items = AsyncMock(return_value=pg_items)
        # С group= — items должны быть видны (read-only subtasks в закрытом TaskSheet)
        r = client.get("/api/lists?type=check&group=Уборка%20кухни")

    assert r.status_code == 200
    ids = [i["id"] for i in r.json()["items"]]
    assert ids == ["c-done-parent-1", "c-done-parent-2"]


# ── POST /api/lists create/done/delete ───────────────────────────────────────

def test_list_create_inv_arcana_uses_arcana_bot_label(client):
    """POST /api/lists с bot=arcana → _arcana_inv_repo.add_item с правильными аргументами."""
    from core.repos.pg_nexus_lists_repo import InventoryItem
    fake_item = InventoryItem(
        id="list-arc", name="соль", list_type="инвентарь", status="not_started",
        category="🕯️ Расходники",
    )
    with patch("miniapp.backend.routes.writes._arcana_inv_repo") as mock_inv, \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        mock_inv.add_item = AsyncMock(return_value=fake_item)
        r = client.post("/api/lists", json={
            "type": "inv",
            "name": "соль",
            "cat": "🕯️ Расходники",
            "qty": 200,
            "bot": "arcana",
        })
    assert r.status_code == 200
    assert r.json()["id"] == "list-arc"
    kwargs = mock_inv.add_item.await_args.kwargs
    assert kwargs["list_type"] == "📦 Инвентарь"
    assert kwargs["category"] == "🕯️ Расходники"


def test_list_create_buy(client):
    from core.repos.pg_nexus_lists_repo import ListItem
    fake_item = ListItem(id="list-id", name="Молоко", list_type="покупки", status="not_started")
    with patch("miniapp.backend.routes.writes._nexus_lists_repo") as mock_nx, \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        mock_nx.add_item = AsyncMock(return_value=fake_item)
        r = client.post("/api/lists", json={
            "type": "buy",
            "name": "Молоко",
            "cat": "🍜 Продукты",
        })
    assert r.status_code == 200
    assert r.json() == {"ok": True, "id": "list-id"}
    kwargs = mock_nx.add_item.await_args.kwargs
    assert kwargs["list_type"] == "🛒 Покупки"
    assert kwargs["name"] == "Молоко"


def test_list_create_invalid_type(client):
    r = client.post("/api/lists", json={"type": "bogus", "name": "x"})
    assert r.status_code == 400


def test_lists_done_endpoint_marks_status(client):
    from core.repos.pg_nexus_lists_repo import ListItem
    fake_item = ListItem(
        id="list-item-1", name="Молоко", list_type="покупки", status="not_started",
        user_notion_id=FAKE_NOTION_USER,
    )
    with patch("miniapp.backend.routes.writes._nexus_lists_repo") as mock_nx, \
         patch("miniapp.backend.routes.writes._arcana_inv_repo") as mock_inv, \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        mock_nx.get_by_id = AsyncMock(return_value=fake_item)
        mock_nx.update_status = AsyncMock(return_value=True)
        mock_inv.get_by_id = AsyncMock(return_value=None)
        r = client.post("/api/lists/list-item-1/done")

    assert r.status_code == 200
    mock_nx.update_status.assert_awaited_once_with("list-item-1", "Done")


def test_list_delete_archives(client):
    from core.repos.pg_nexus_lists_repo import ListItem
    fake_item = ListItem(
        id="l-2", name="test", list_type="покупки", status="not_started",
        user_notion_id=FAKE_NOTION_USER,
    )
    with patch("miniapp.backend.routes.writes._nexus_lists_repo") as mock_nx, \
         patch("miniapp.backend.routes.writes._arcana_inv_repo") as mock_inv, \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        mock_nx.get_by_id = AsyncMock(return_value=fake_item)
        mock_nx.update_status = AsyncMock(return_value=True)
        mock_inv.get_by_id = AsyncMock(return_value=None)
        r = client.post("/api/lists/l-2/delete")
    assert r.status_code == 200
    mock_nx.update_status.assert_awaited_once_with("l-2", "Archived")


# ── GET /api/memory ──────────────────────────────────────────────────────────

def test_memory_excludes_budget_and_adhd_categories(client):
    pages = [
        _mem("m1", "Chapman = сигареты", cat="🛒 Предпочтения", key="chapman"),
        _mem("m2", "Работает техника 2 минут", cat="🦋 СДВГ", key="2min"),
        _mem("m3", "доход: ЗП — 115000₽", cat="📥 Доход", key="income_zp"),
        _mem("m4", "подруга Аня", cat="👥 Люди", key="anya"),
    ]

    async def qp(*_, **__):
        return pages

    with patch("miniapp.backend.routes.memory.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.memory.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/memory")

    assert r.status_code == 200
    data = r.json()
    names = {i["id"] for i in data["items"]}
    assert names == {"m1", "m4"}
    # #49: API теперь всегда возвращает канонический список категорий
    # (минус бюджетные/ADHD), даже если в данных пусто — фронт показывает
    # все табы и ставит empty state в пустые. Проверяем что обе категории
    # с реальными данными присутствуют + бюджетные/ADHD исключены.
    cats = set(data["categories"])
    assert {"🛒 Предпочтения", "👥 Люди"}.issubset(cats)
    assert cats.isdisjoint({"🦋 СДВГ", "📥 Доход", "🔒 Обязательные", "💰 Лимит", "📋 Долги", "🎯 Цели"})


def test_memory_cat_filter(client):
    pages = [
        _mem("m1", "A", cat="🛒 Предпочтения"),
        _mem("m2", "B", cat="👥 Люди"),
    ]

    async def qp(*_, **__):
        return pages

    with patch("miniapp.backend.routes.memory.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.memory.get_user_notion_id",
               AsyncMock(return_value="")):
        r = client.get("/api/memory?cat=%F0%9F%91%A5%20%D0%9B%D1%8E%D0%B4%D0%B8")  # 👥 Люди

    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1 and items[0]["id"] == "m2"


def test_memory_search_matches_key_and_related(client):
    # #6: поиск Mini App должен матчить Текст+Ключ+Связь, как бот.
    pages = [
        _mem("m1", "ходить раз в полгода", cat="🏥 Здоровье", key="невролог"),
        _mem("m2", "любит тёмный шоколад", cat="🛒 Предпочтения", related="Аня"),
        _mem("m3", "не относится", cat="🏠 Быт"),
    ]

    async def qp(*_, **__):
        return pages

    with patch("miniapp.backend.routes.memory.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.memory.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        # совпадение по Ключу (в тексте слова «невролог» нет)
        r_key = client.get("/api/memory?q=невролог")
        # совпадение по Связи
        r_rel = client.get("/api/memory?q=аня")
        # совпадение по тексту (как раньше)
        r_txt = client.get("/api/memory?q=шоколад")
        # ничего не нашли
        r_none = client.get("/api/memory?q=zzzz")

    assert {i["id"] for i in r_key.json()["items"]} == {"m1"}
    assert {i["id"] for i in r_rel.json()["items"]} == {"m2"}
    assert {i["id"] for i in r_txt.json()["items"]} == {"m2"}
    assert r_none.json()["items"] == []


def test_memory_adhd_returns_records_and_uses_cache(client):
    pages = [_mem("a1", "Работает техника 2 минут", cat="🦋 СДВГ")]
    sonnet = AsyncMock(return_value="Персональный профиль...")

    async def qp(*_, **__):
        return pages

    with patch("miniapp.backend.routes.memory.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.memory.ask_claude", sonnet), \
         patch("miniapp.backend.routes.memory.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r1 = client.get("/api/memory/adhd")
        r2 = client.get("/api/memory/adhd")

    assert r1.status_code == 200
    assert r1.json()["profile"] == "Персональный профиль..."
    # API группирует записи по типам (patterns/strategies/triggers/specifics).
    groups = r1.json()["groups"]
    assert sum(len(v) for v in groups.values()) == 1
    assert r2.json()["profile"] == "Персональный профиль..."
    # Sonnet должен быть вызван ровно один раз — второй ответ из кэша
    assert sonnet.await_count == 1


def test_memory_401_without_init_data():
    app.dependency_overrides.clear()
    c = TestClient(app)
    assert c.get("/api/memory").status_code == 401


# ── POST /api/memory ─────────────────────────────────────────────────────────

def test_memory_create(client):
    with patch("miniapp.backend.routes.writes.page_create",
               AsyncMock(return_value="mem-id")) as pc, \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/memory", json={
            "text": "Chapman = сигареты",
            "cat": "🛒 Предпочтения",
        })
    assert r.status_code == 200
    assert r.json() == {"ok": True, "id": "mem-id"}
    args, _ = pc.await_args
    _, props = args
    assert props["Текст"]["title"][0]["text"]["content"] == "Chapman = сигареты"
    assert props["Актуально"]["checkbox"] is True
