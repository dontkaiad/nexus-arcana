"""Mini App — списки и память.

GET/POST /api/lists (покупки/инвентарь/чеклисты, client-side type matching,
скрытие чеклистов закрытых родителей), GET/POST /api/memory (+ ADHD-профиль).

Собрано из wave2a / wave3 / wave5 / wave6 / wave8.62 при реорганизации
тестов по доменам.
"""
from __future__ import annotations

import json as _json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from miniapp.backend import cache
from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id


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


def _parent_task(title, *, status="Not started"):
    return {
        "id": f"parent-{title}",
        "properties": {
            "Задача": {"title": [{"plain_text": title}]},
            "Статус": {"status": {"name": status}},
            "Приоритет": {"select": {"name": "🔴 Срочно"}},
            "Категория": {"select": {"name": "🏠 Дом"}},
            "Дедлайн": {"date": None},
            "Напоминание": {"date": None},
            "Время повтора": {"rich_text": []},
            "Повтор": {"select": None},
            "🪪 Пользователи": {"relation": [{"id": FAKE_NOTION_USER}]},
        },
    }


# ── GET /api/lists ───────────────────────────────────────────────────────────

def test_lists_buy_returns_items(client):
    pages = [
        _list_item("l1", "Молоко", cat="🍜 Продукты", qty=1, note="Простоквашино"),
        _list_item("l2", "Хлеб", status="Done", cat="🍜 Продукты"),
    ]

    async def qp(*_, **__):
        return pages

    with patch("miniapp.backend.routes.lists.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.lists.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
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
    soon = (datetime.now().date() + timedelta(days=5)).isoformat()
    later = (datetime.now().date() + timedelta(days=30)).isoformat()
    pages = [
        _list_item("a", "Потом", type_="📦 Инвентарь", expires=later),
        _list_item("b", "Скоро", type_="📦 Инвентарь", expires=soon),
        _list_item("c", "Без срока", type_="📦 Инвентарь", expires=None),
    ]

    async def qp(*_, **__):
        return pages

    with patch("miniapp.backend.routes.lists.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.lists.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/lists?type=inv")

    assert r.status_code == 200
    items = r.json()["items"]
    assert [i["name"] for i in items] == ["Скоро", "Потом", "Без срока"]


def test_lists_q_filter(client):
    pages = [
        _list_item("a", "Молоко", note="3,2%"),
        _list_item("b", "Хлеб", note="бородинский"),
        _list_item("c", "Сыр", note=None),
    ]

    async def qp(*_, **__):
        return pages

    with patch("miniapp.backend.routes.lists.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.lists.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/lists?type=buy&q=мол")

    items = r.json()["items"]
    assert [i["name"] for i in items] == ["Молоко"]


def test_lists_invalid_type(client):
    r = client.get("/api/lists?type=bogus")
    assert r.status_code == 400


def test_lists_empty(client):
    async def qp(*_, **__):
        return []
    with patch("miniapp.backend.routes.lists.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.lists.get_user_notion_id",
               AsyncMock(return_value="")):
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
    """wave5.4: чеклисты без заполненного Бот тоже должны попадать в выборку."""
    captured = {}

    async def qp(db_id, *, filters=None, **kwargs):
        captured["filters"] = filters
        return []

    with patch("miniapp.backend.routes.lists.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.lists.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/lists?type=check")

    assert r.status_code == 200
    filter_str = _json.dumps(captured["filters"] or {}, ensure_ascii=False)
    # or-ветвь с is_empty присутствует (Бот Nexus OR пустой)
    assert "is_empty" in filter_str
    # wave6.1: "Тип" теперь фильтруется client-side — в Notion-запросе его нет
    assert "Чеклист" not in filter_str


# ── GET /api/lists — client-side type matching (wave6) ──────────────────────

def test_lists_check_loads_with_exact_emoji(client):
    pages = [
        _list_item("c1", "купить молоко", type_="📋 Чеклист", bot="☀️ Nexus"),
        _list_item("c2", "старое покупочное", type_="🛒 Покупки", bot="☀️ Nexus"),
    ]

    async def qp(*_, **__):
        return pages

    with patch("miniapp.backend.routes.lists.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.lists.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/lists?type=check")

    assert r.status_code == 200
    ids = [i["id"] for i in r.json()["items"]]
    assert "c1" in ids
    assert "c2" not in ids


def test_lists_check_loads_when_type_has_diff_spacing(client):
    """Если в Notion тип записан как '📋  Чеклист' с 2 пробелами — client-side match ловит."""
    pages = [
        _list_item("c1", "план дня", type_="📋  Чеклист", bot="☀️ Nexus"),
        _list_item("c2", "утро ритуал", type_="📋 Чеклист ", bot="☀️ Nexus"),  # trailing space
    ]

    async def qp(*_, **__):
        return pages

    with patch("miniapp.backend.routes.lists.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.lists.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/lists?type=check")

    assert r.status_code == 200
    ids = {i["id"] for i in r.json()["items"]}
    assert ids == {"c1", "c2"}


def test_lists_inv_matches_partial_keyword(client):
    pages = [
        _list_item("i1", "гречка", type_="📦 Инвентарь", bot="☀️ Nexus"),
        _list_item("i2", "перец", type_="📦  Инвентарь", bot="☀️ Nexus"),  # double space
    ]

    async def qp(*_, **__):
        return pages

    with patch("miniapp.backend.routes.lists.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.lists.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/lists?type=inv")

    assert r.status_code == 200
    assert len(r.json()["items"]) == 2


def test_lists_archived_filtered_out(client):
    pages = [
        _list_item("a1", "активное", type_="📋 Чеклист", status="Not started", bot="☀️ Nexus"),
        _list_item("a2", "архив", type_="📋 Чеклист", status="Archived", bot="☀️ Nexus"),
    ]

    async def qp(*_, **__):
        return pages

    with patch("miniapp.backend.routes.lists.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.lists.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/lists?type=check")

    ids = [i["id"] for i in r.json()["items"]]
    assert ids == ["a1"]


# ── GET /api/lists — чеклисты закрытых родителей (wave8.62) ─────────────────

def test_check_items_with_done_parent_are_hidden(client):
    """Чеклист задачи в статусе Done не должен возвращаться endpoint'ом."""
    DB_LISTS = "db-lists-id"
    DB_TASKS = "db-tasks-id"

    list_pages = [
        _list_item("c1", "помыть холодильник", type_="📋 Чеклист", group="Генеральная уборка"),
        _list_item("c2", "купить молоко", type_="📋 Чеклист", group="Покупки на неделю"),
        _list_item("c3", "выкинуть просрочку", type_="📋 Чеклист", group="Архив прошлый год"),
    ]
    task_pages = [
        _parent_task("Генеральная уборка", status="Done"),
        _parent_task("Покупки на неделю", status="Not started"),
        _parent_task("Архив прошлый год", status="Archived"),
    ]

    async def qp(db_id, *, filters=None, **__):
        if db_id == DB_LISTS:
            return list_pages
        if db_id == DB_TASKS:
            return task_pages
        return []

    with patch("miniapp.backend.routes.lists.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.lists.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)), \
         patch("miniapp.backend.routes.lists.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))), \
         patch("miniapp.backend.routes.lists.config") as cfg:
        cfg.db_lists = DB_LISTS
        cfg.nexus.db_tasks = DB_TASKS
        r = client.get("/api/lists?type=check")

    assert r.status_code == 200, r.text
    items = r.json()["items"]
    ids = [i["id"] for i in items]
    # c1 (parent Done) и c3 (parent Archived) спрятаны, c2 (parent Not started) виден
    assert ids == ["c2"]


def test_check_items_match_parent_case_insensitive(client):
    """wave8.62.1: title задачи и Группа айтема могут отличаться регистром/пробелами —
    parent должен всё равно приклеиться, а Done-родитель — спрятать item."""
    DB_LISTS = "db-lists-id"
    DB_TASKS = "db-tasks-id"

    list_pages = [
        # Группа = lowercase + лишние пробелы
        _list_item("c-mismatch", "помыть холодильник", type_="📋 Чеклист",
                   group="  сделать генеральную уборку кухни  "),
    ]
    task_pages = [
        # Title = capitalize, без лишних пробелов
        _parent_task("Сделать Генеральную Уборку Кухни", status="Done"),
    ]

    async def qp(db_id, *, filters=None, **__):
        if db_id == DB_LISTS:
            return list_pages
        if db_id == DB_TASKS:
            return task_pages
        return []

    with patch("miniapp.backend.routes.lists.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.lists.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)), \
         patch("miniapp.backend.routes.lists.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))), \
         patch("miniapp.backend.routes.lists.config") as cfg:
        cfg.db_lists = DB_LISTS
        cfg.nexus.db_tasks = DB_TASKS
        r = client.get("/api/lists?type=check")

    assert r.status_code == 200
    items = r.json()["items"]
    # parent Done должен быть найден несмотря на mismatch регистра/пробелов → item скрыт
    assert items == []


def test_check_items_match_parent_without_user_relation(client):
    """wave8.62.1: родитель без relation 🪪 Пользователи (создан в Notion-UI) —
    запрос должен включать OR is_empty, иначе parent=None и Done-родитель не прячется."""
    DB_LISTS = "db-lists-id"
    DB_TASKS = "db-tasks-id"

    list_pages = [
        _list_item("c-orphan-parent", "пункт", type_="📋 Чеклист", group="Уборка"),
    ]
    parent_no_user = _parent_task("Уборка", status="Done")
    parent_no_user["properties"]["🪪 Пользователи"] = {"relation": []}
    task_pages = [parent_no_user]

    captured = {}

    async def qp(db_id, *, filters=None, **__):
        if db_id == DB_LISTS:
            return list_pages
        if db_id == DB_TASKS:
            captured["tasks_filters"] = filters
            return task_pages
        return []

    with patch("miniapp.backend.routes.lists.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.lists.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)), \
         patch("miniapp.backend.routes.lists.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))), \
         patch("miniapp.backend.routes.lists.config") as cfg:
        cfg.db_lists = DB_LISTS
        cfg.nexus.db_tasks = DB_TASKS
        r = client.get("/api/lists?type=check")

    assert r.status_code == 200
    # Запрос к db_tasks должен иметь is_empty в OR-ветке user-relation
    f_str = _json.dumps(captured.get("tasks_filters") or {}, ensure_ascii=False)
    assert "is_empty" in f_str
    # И item с parent Done спрятан
    assert r.json()["items"] == []


def test_check_items_with_group_param_show_even_if_parent_closed(client):
    """wave8.62.2: TaskSheet закрытой задачи запрашивает /api/lists?type=check&group=<title>
    чтобы показать subtasks read-only. Фильтр closed-parent НЕ должен прятать items
    в этом случае — иначе секция Чеклист в закрытом TaskSheet всегда пустая."""
    DB_LISTS = "db-lists-id"
    DB_TASKS = "db-tasks-id"

    list_pages = [
        _list_item("c-done-parent-1", "помыть холодильник", type_="📋 Чеклист", group="Уборка кухни"),
        _list_item("c-done-parent-2", "выкинуть просрочку", type_="📋 Чеклист", group="Уборка кухни"),
    ]
    task_pages = [
        _parent_task("Уборка кухни", status="Done"),
    ]

    async def qp(db_id, *, filters=None, **__):
        if db_id == DB_LISTS:
            return list_pages
        if db_id == DB_TASKS:
            return task_pages
        return []

    with patch("miniapp.backend.routes.lists.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.lists.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)), \
         patch("miniapp.backend.routes.lists.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))), \
         patch("miniapp.backend.routes.lists.config") as cfg:
        cfg.db_lists = DB_LISTS
        cfg.nexus.db_tasks = DB_TASKS
        # С group= — items должны быть видны (read-only subtasks в закрытом TaskSheet)
        r = client.get("/api/lists?type=check&group=Уборка%20кухни")

    assert r.status_code == 200
    ids = [i["id"] for i in r.json()["items"]]
    assert ids == ["c-done-parent-1", "c-done-parent-2"]


# ── POST /api/lists create/done/delete ───────────────────────────────────────

def test_list_create_inv_arcana_uses_arcana_bot_label(client):
    """POST /api/lists с bot=arcana пишет Бот=🌒 Arcana."""
    with patch("miniapp.backend.routes.writes.page_create",
               AsyncMock(return_value="list-arc")) as pc, \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/lists", json={
            "type": "inv",
            "name": "соль",
            "cat": "🕯️ Расходники",
            "qty": 200,
            "bot": "arcana",
        })
    assert r.status_code == 200
    assert r.json()["id"] == "list-arc"
    props = pc.await_args.args[1]
    assert props["Бот"]["select"]["name"] == "🌒 Arcana"
    assert props["Тип"]["select"]["name"] == "📦 Инвентарь"
    assert props["Категория"]["select"]["name"] == "🕯️ Расходники"


def test_list_create_buy(client):
    with patch("miniapp.backend.routes.writes.page_create",
               AsyncMock(return_value="list-id")) as pc, \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/lists", json={
            "type": "buy",
            "name": "Молоко",
            "cat": "🍜 Продукты",
        })
    assert r.status_code == 200
    assert r.json() == {"ok": True, "id": "list-id"}
    args, _ = pc.await_args
    _, props = args
    assert props["Тип"]["select"]["name"] == "🛒 Покупки"
    assert props["Название"]["title"][0]["text"]["content"] == "Молоко"


def test_list_create_invalid_type(client):
    r = client.post("/api/lists", json={"type": "bogus", "name": "x"})
    assert r.status_code == 400


def test_lists_done_endpoint_marks_status(client):
    from core.notion_client import _status

    captured = {}

    async def fake_get_page(pid):
        return {
            "id": pid,
            "properties": {
                "🪪 Пользователи": {"relation": [{"id": FAKE_NOTION_USER}]},
            },
        }

    async def fake_update_page(pid, props):
        captured["id"] = pid
        captured["props"] = props
        return {"ok": True}

    with patch("miniapp.backend.routes.writes.get_page", side_effect=fake_get_page), \
         patch("miniapp.backend.routes.writes.update_page", side_effect=fake_update_page), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/lists/list-item-1/done")

    assert r.status_code == 200
    assert captured["id"] == "list-item-1"
    assert captured["props"]["Статус"] == _status("Done")


def test_list_delete_archives(client):
    page = _page("l-2")
    with patch("miniapp.backend.routes.writes.get_page", AsyncMock(return_value=page)), \
         patch("miniapp.backend.routes.writes.update_page",
               AsyncMock(return_value=None)) as up, \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/lists/l-2/delete")
    assert r.status_code == 200
    args, _ = up.await_args
    assert args[1]["Статус"]["status"]["name"] == "Archived"


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
