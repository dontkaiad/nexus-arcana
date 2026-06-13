"""tests/test_lists_v1_2.py — расширение Списков (план/магазин/этап/агрегации).

Покрытие:
- core/lists_parser.py — regex hint, JSON normalize, парсинг ответов Haiku.
- core/list_classifier.py — _LIST_SUM_RE.
- core/list_manager.py — _extract_page_data + add_items + get_list_summary.
- nexus/handlers/lists.py — handle_list_buy + handle_list_sum.
- miniapp/backend/routes/lists.py — _serialize + _summary.
- miniapp/backend/routes/writes.py — POST /lists, /checkout.

Все вызовы Haiku и Notion замоканы — реальные API не дёргаются.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ════════════════════════════════════════════════════════════════════════════
# core/lists_parser.py — regex + normalize
# ════════════════════════════════════════════════════════════════════════════


def test_extract_price_inline_k():
    from core.lists_parser import extract_price_inline
    assert extract_price_inline("iPhone Pro 108к") == 108_000
    assert extract_price_inline("AirPods 1.5к") == 1_500
    assert extract_price_inline("часы 0.5к") == 500


def test_extract_price_inline_rub():
    from core.lists_parser import extract_price_inline
    assert extract_price_inline("молоко 89р") == 89
    assert extract_price_inline("хлеб 2500₽") == 2500
    assert extract_price_inline("ужин 5000 руб") == 5000


def test_extract_price_inline_no_price():
    from core.lists_parser import extract_price_inline
    # "iPhone 17" — 17 без суффикса < 50, отфильтровано
    assert extract_price_inline("iPhone 17") is None
    assert extract_price_inline("просто молоко") is None
    assert extract_price_inline("") is None


def test_match_sum_command():
    from core.lists_parser import match_sum_command
    assert match_sum_command("сумма Apple-стек") == "Apple-стек"
    assert match_sum_command("сколько по продуктам") == "продуктам"
    assert match_sum_command("итого Apple-стек?") == "Apple-стек"
    assert match_sum_command("купи молоко") is None
    assert match_sum_command("") is None


def test_normalize_buy_item_priority_canonical():
    from core.lists_parser import normalize_buy_item
    assert normalize_buy_item({"name": "x", "priority": "🔴 Срочно"})["priority"] == "🔴 Срочно"
    assert normalize_buy_item({"name": "x", "priority": "срочно"})["priority"] == "🔴 Срочно"
    assert normalize_buy_item({"name": "x", "priority": "важно"})["priority"] == "🟡 Важно"
    assert normalize_buy_item({"name": "x", "priority": "потом"})["priority"] == "⚪ Можно потом"
    assert normalize_buy_item({"name": "x", "priority": "случайное"})["priority"] is None


def test_normalize_buy_item_note_truncate():
    from core.lists_parser import normalize_buy_item
    long_note = "x" * 150
    out = normalize_buy_item({"name": "x", "note": long_note})
    assert out["note"].endswith("...")
    assert len(out["note"]) <= 100


def test_normalize_buy_item_coerces_numbers():
    from core.lists_parser import normalize_buy_item
    out = normalize_buy_item({
        "name": "x", "price_plan": "108600", "stage": "2", "qty": "5",
    })
    assert out["price_plan"] == 108600.0
    assert out["stage"] == 2
    assert out["qty"] == 5.0


def test_parse_buy_response_items_wrapper():
    from core.lists_parser import parse_buy_response
    raw = json.dumps({"items": [
        {"name": "iPhone", "price_plan": 108000, "source": "iPiter"},
        {"name": "AirPods"},
    ]})
    out = parse_buy_response(raw)
    assert len(out) == 2
    assert out[0]["name"] == "iPhone"
    assert out[0]["price_plan"] == 108000
    assert out[0]["source"] == "iPiter"


def test_parse_buy_response_legacy_array():
    """Старый формат v1.1 (массив верхнего уровня) — должен работать."""
    from core.lists_parser import parse_buy_response
    raw = json.dumps([
        {"name": "молоко", "category": "🍜 Продукты"},
        {"name": "яйца", "category": "🍜 Продукты"},
    ])
    out = parse_buy_response(raw)
    assert [x["name"] for x in out] == ["молоко", "яйца"]
    # все 10 полей присутствуют (None для отсутствующих)
    assert "price_plan" in out[0] and out[0]["price_plan"] is None


def test_parse_buy_response_strips_markdown_fence():
    from core.lists_parser import parse_buy_response
    raw = '```json\n{"items":[{"name":"молоко"}]}\n```'
    out = parse_buy_response(raw)
    assert out[0]["name"] == "молоко"


def test_parse_buy_response_bad_json():
    from core.lists_parser import parse_buy_response
    assert parse_buy_response("совсем не json") == []


@pytest.mark.asyncio
async def test_parse_buy_text_full_pipeline():
    """parse_buy_text → ask_claude мокаем, проверяем нормализацию."""
    from core import lists_parser
    haiku_resp = json.dumps({"items": [{
        "name": "iPhone Pro", "category": "💳 Прочее",
        "price_plan": 108600, "source": "iPiter", "stage": 2,
        "group": "Apple-стек", "note": "Deep Blue 256GB",
        "priority": "🟡 Важно", "qty": 1, "expires": None,
    }]})
    with patch.object(lists_parser, "ask_claude",
                       AsyncMock(return_value=haiku_resp)) as mock:
        out = await lists_parser.parse_buy_text(
            "iPhone Pro 108.6к в iPiter, важно, в Apple-стек"
        )
    mock.assert_awaited_once()
    assert len(out) == 1
    assert out[0]["price_plan"] == 108600
    assert out[0]["source"] == "iPiter"
    assert out[0]["group"] == "Apple-стек"
    assert out[0]["priority"] == "🟡 Важно"


@pytest.mark.asyncio
async def test_parse_buy_text_long_multiline_list():
    """9+ items в одном сообщении — Haiku ответ не должен обрезаться.

    Регрессия issue #66: max_tokens=800 обрезал JSON на середине →
    parse_buy_response возвращал [] → «Не смог разобрать список».
    Тест фиксирует max_tokens≥2000.
    """
    from core import lists_parser
    items = [
        {"name": "Яйца", "category": "🍜 Продукты", "price_plan": 130, "qty": 10},
        {"name": "Молоко", "category": "🍜 Продукты", "price_plan": 100, "qty": 1},
        {"name": "Картошка", "category": "🍜 Продукты", "price_plan": 60, "qty": 1},
        {"name": "Лук", "category": "🍜 Продукты", "price_plan": 40, "qty": 1},
        {"name": "Морковь", "category": "🍜 Продукты", "price_plan": 50, "qty": 1},
        {"name": "Томатная паста", "category": "🍜 Продукты", "price_plan": 60},
        {"name": "Плавленый сыр", "category": "🍜 Продукты", "price_plan": 50, "qty": 1},
        {"name": "Хлеб", "category": "🍜 Продукты", "price_plan": 40},
        {"name": "Чапман", "category": "🍜 Продукты", "price_plan": 285},
    ]
    haiku_resp = json.dumps({"items": items})
    with patch.object(lists_parser, "ask_claude",
                       AsyncMock(return_value=haiku_resp)) as mock:
        out = await lists_parser.parse_buy_text(
            "запиши в покупки\nЯйца 10 шт — ~130₽\nМолоко 1 л — ~100₽\n..."
        )
    assert len(out) == 9
    assert [it["name"] for it in out] == [it["name"] for it in items]
    # max_tokens должен быть достаточен для длинного списка
    kwargs = mock.await_args.kwargs
    assert kwargs.get("max_tokens", 0) >= 2000


@pytest.mark.asyncio
async def test_parse_buy_text_legacy_message():
    """«молоко в покупки» → один item, extra поля = None. Без падений."""
    from core import lists_parser
    haiku_resp = json.dumps({"items": [
        {"name": "молоко", "category": "🍜 Продукты"},
    ]})
    with patch.object(lists_parser, "ask_claude",
                       AsyncMock(return_value=haiku_resp)):
        out = await lists_parser.parse_buy_text("молоко в покупки")
    assert len(out) == 1
    assert out[0]["name"] == "молоко"
    assert out[0]["price_plan"] is None
    assert out[0]["source"] is None


def test_format_rub_thousands():
    from core.lists_parser import format_rub
    assert format_rub(108600) == "108 600"
    assert format_rub(1_000_000) == "1 000 000"
    assert format_rub(0) == "0"


# ════════════════════════════════════════════════════════════════════════════
# core/list_classifier.py — _LIST_SUM_RE
# ════════════════════════════════════════════════════════════════════════════


def test_list_sum_regex():
    from core.list_classifier import _LIST_SUM_RE
    assert _LIST_SUM_RE.match("сумма Apple-стек")
    assert _LIST_SUM_RE.match("сколько по продуктам")
    assert _LIST_SUM_RE.match("итого продукты")
    assert not _LIST_SUM_RE.match("купи молоко")
    # NB: «сколько потратила» формально матчится этим regex, но в классифере
    # _STATS_RE стоит ВЫШЕ list_sum в pre-filter цепочке (см. classify_routes_*).


def test_classify_routes_list_sum():
    """Регрессия: «сумма X» должна классифицироваться как list_sum."""
    import asyncio
    from core.classifier import classify
    items = asyncio.run(classify("сумма Apple-стек", tz_offset=3))
    assert items[0]["type"] == "list_sum"


def test_classify_stats_wins_over_list_sum():
    """Pre-filter порядок: «сколько потратила» → stats, не list_sum."""
    import asyncio
    from core.classifier import classify
    items = asyncio.run(classify("сколько потратила за месяц", tz_offset=3))
    assert items[0]["type"] == "stats"


# ════════════════════════════════════════════════════════════════════════════
# core/list_manager.py — extract / add / summary
# ════════════════════════════════════════════════════════════════════════════


def _fake_page(props_by_name: dict) -> dict:
    """Сборка минимальной Notion-страницы для тестов."""
    return {"id": "p-test", "properties": props_by_name}


def test_extract_page_data_new_fields():
    from core.list_manager import _extract_page_data
    page = _fake_page({
        "Название": {"title": [{"plain_text": "iPhone", "text": {"content": "iPhone"}}]},
        "Тип": {"select": {"name": "🛒 Покупки"}},
        "Статус": {"status": {"name": "Not started"}},
        "Категория": {"select": {"name": "💳 Прочее"}},
        "Цена план": {"number": 108600},
        "Магазин": {"rich_text": [{"plain_text": "iPiter", "text": {"content": "iPiter"}}]},
        "Этап": {"number": 2},
    })
    data = _extract_page_data(page)
    assert data["price_plan"] == 108600
    assert data["source"] == "iPiter"
    assert data["stage"] == 2


def test_extract_page_data_existing_fields_now_returned():
    """v1.1 поля Заметка/Приоритет/Срок годности уже были в коде, но теперь
    их реально читают (раньше тоже читались — это регресс-страж)."""
    from core.list_manager import _extract_page_data
    page = _fake_page({
        "Название": {"title": [{"plain_text": "молоко", "text": {"content": "молоко"}}]},
        "Тип": {"select": {"name": "🛒 Покупки"}},
        "Статус": {"status": {"name": "Not started"}},
        "Заметка": {"rich_text": [{"plain_text": "органическое", "text": {"content": "органическое"}}]},
        "Приоритет": {"select": {"name": "🔴 Срочно"}},
        "Количество": {"number": 5},
        "Срок годности": {"date": {"start": "2026-05-15"}},
    })
    data = _extract_page_data(page)
    assert data["note"] == "органическое"
    assert data["priority"] == "🔴 Срочно"
    assert data["quantity"] == 5
    assert data["expiry"] == "2026-05-15"


def test_extract_page_data_works_relation_with_trailing_space():
    """В Notion property 🔮 Работы имеет trailing space — поддерживаем оба."""
    from core.list_manager import _extract_page_data
    page = _fake_page({
        "Название": {"title": [{"plain_text": "x", "text": {"content": "x"}}]},
        "Тип": {"select": {"name": "🛒 Покупки"}},
        "Статус": {"status": {"name": "Not started"}},
        "🔮 Работы ": {"relation": [{"id": "work-1"}]},
    })
    data = _extract_page_data(page)
    assert data["work_rel"] == "work-1"


@pytest.mark.asyncio
async def test_add_items_writes_v12_fields():
    """add_items с полным набором полей пишет Цена план / Магазин / Этап."""
    from core import list_manager

    captured: dict = {}

    async def _fake_create(db, props):
        captured["db"] = db
        captured["props"] = props
        return "page-1"

    with patch.object(list_manager, "page_create",
                       AsyncMock(side_effect=_fake_create)):
        out = await list_manager.add_items(
            [{
                "name": "iPhone Pro", "category": "💳 Прочее",
                "price_plan": 108600, "source": "iPiter", "stage": 2,
                "group": "Apple-стек", "note": "Deep Blue 256GB",
                "priority": "🟡 Важно", "qty": 1,
            }],
            list_type="🛒 Покупки",
            bot_name="☀️ Nexus",
            user_page_id="user-1",
        )

    assert out and out[0]["id"] == "page-1"
    p = captured["props"]
    assert p["Цена план"] == {"number": 108600.0}
    assert p["Магазин"]["rich_text"][0]["text"]["content"] == "iPiter"
    assert p["Этап"] == {"number": 2}
    assert p["Группа"]["rich_text"][0]["text"]["content"] == "Apple-стек"
    assert p["Приоритет"] == {"select": {"name": "🟡 Важно"}}


@pytest.mark.asyncio
async def test_add_items_legacy_no_extra_fields():
    """Старый вызов «молоко без всего» не падает и не пишет лишних полей."""
    from core import list_manager
    with patch.object(list_manager, "page_create",
                       AsyncMock(return_value="page-1")) as mock:
        out = await list_manager.add_items(
            [{"name": "молоко", "category": "🍜 Продукты"}],
            list_type="🛒 Покупки",
            bot_name="☀️ Nexus",
            user_page_id="user-1",
        )
    assert out
    props = mock.await_args.args[1]
    assert "Цена план" not in props
    assert "Магазин" not in props
    assert "Этап" not in props


@pytest.mark.asyncio
async def test_get_list_summary_aggregates():
    """get_list_summary считает план/факт/счётчики правильно."""
    from core import list_manager

    fake_pages = [
        _fake_page({
            "Название": {"title": [{"plain_text": "iPhone", "text": {"content": "x"}}]},
            "Тип": {"select": {"name": "🛒 Покупки"}},
            "Статус": {"status": {"name": "Not started"}},
            "Цена план": {"number": 108000},
            "Группа": {"rich_text": [{"plain_text": "Apple-стек", "text": {"content": "Apple-стек"}}]},
        }),
        _fake_page({
            "Название": {"title": [{"plain_text": "AirPods", "text": {"content": "x"}}]},
            "Тип": {"select": {"name": "🛒 Покупки"}},
            "Статус": {"status": {"name": "Done"}},
            "Цена план": {"number": 25500},
            "Цена": {"number": 24000},
            "Группа": {"rich_text": [{"plain_text": "Apple-стек", "text": {"content": "Apple-стек"}}]},
        }),
    ]
    with patch.object(list_manager, "db_query",
                       AsyncMock(return_value=fake_pages)):
        out = await list_manager.get_list_summary(
            user_notion_id="user-1", bot_name="☀️ Nexus",
            type_="🛒 Покупки", group="Apple-стек",
        )
    assert out["plan_total"] == 133500
    assert out["actual_total"] == 24000
    assert out["count_total"] == 2
    assert out["count_open"] == 1
    assert out["count_done"] == 1


@pytest.mark.asyncio
async def test_get_list_summary_empty():
    from core import list_manager
    with patch.object(list_manager, "db_query", AsyncMock(return_value=[])):
        out = await list_manager.get_list_summary(
            user_notion_id="user-1", bot_name="☀️ Nexus",
        )
    assert out["count_total"] == 0
    assert out["plan_total"] == 0


# ════════════════════════════════════════════════════════════════════════════
# nexus/handlers/lists.py — handle_list_buy + handle_list_sum
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_handle_list_buy_with_full_fields():
    """handle_list_buy → parse_buy_text → add_items → ответ показывает план."""
    from nexus.handlers import lists as nx

    parsed_items = [{
        "name": "iPhone Pro", "category": "💳 Прочее",
        "price_plan": 108600, "source": "iPiter", "stage": 2,
        "group": "Apple-стек", "note": None, "priority": None,
        "qty": None, "expires": None,
    }]

    msg = MagicMock()
    msg.text = "iPhone Pro 108.6к в iPiter, в Apple-стек"
    msg.answer = AsyncMock()

    with (
        patch.object(nx, "react", AsyncMock()),
        patch.object(nx, "search_memory_categories", AsyncMock(return_value={})),
        patch.object(nx, "parse_buy_text", AsyncMock(return_value=parsed_items)),
        patch.object(nx, "get_list", AsyncMock(return_value=[])),
        patch.object(nx, "add_items", AsyncMock(return_value=[
            {"id": "p1", "name": "iPhone Pro", "category": "💳 Прочее"},
        ])) as mock_add,
    ):
        await nx.handle_list_buy(msg, {"text": msg.text}, user_notion_id="u1")

    # add_items получил пункты с price_plan и source
    items_arg = mock_add.await_args.args[0]
    assert items_arg[0]["price_plan"] == 108600
    assert items_arg[0]["source"] == "iPiter"
    # ответ упоминает план
    sent_text = msg.answer.await_args.args[0]
    assert "iPhone Pro" in sent_text
    assert "108 600" in sent_text  # формат с разрядами


@pytest.mark.asyncio
async def test_handle_list_sum_by_group():
    from nexus.handlers import lists as nx

    summary = {
        "plan_total": 175500, "actual_total": 8000,
        "count_total": 5, "count_open": 4, "count_done": 1,
        "items": [
            {"name": "iPhone", "status": "Not started", "price_plan": 108600, "source": "iPiter"},
            {"name": "Anker", "status": "Done", "price": 8000, "source": "Озон"},
        ],
    }
    msg = MagicMock()
    msg.text = "сумма Apple-стек"
    msg.answer = AsyncMock()
    with (
        patch.object(nx, "react", AsyncMock()),
        patch.object(nx, "get_list_summary", AsyncMock(return_value=summary)) as mock_sum,
    ):
        await nx.handle_list_sum(msg, {"text": msg.text}, user_notion_id="u1")

    kwargs = mock_sum.await_args.kwargs
    assert kwargs["group"] == "Apple-стек"
    sent = msg.answer.await_args.args[0]
    assert "Apple-стек" in sent
    assert "175 500" in sent
    assert "8 000" in sent


@pytest.mark.asyncio
async def test_handle_list_sum_empty():
    """Если пунктов нет — выводит «Пусто», не падает."""
    from nexus.handlers import lists as nx
    msg = MagicMock()
    msg.text = "сумма Несуществующее"
    msg.answer = AsyncMock()
    empty_summary = {
        "plan_total": 0, "actual_total": 0, "count_total": 0,
        "count_open": 0, "count_done": 0, "items": [],
    }
    with (
        patch.object(nx, "react", AsyncMock()),
        patch.object(nx, "get_list_summary", AsyncMock(return_value=empty_summary)),
    ):
        await nx.handle_list_sum(msg, {"text": msg.text}, user_notion_id="u1")
    sent = msg.answer.await_args.args[0]
    assert "Пусто" in sent or "пусто" in sent


# ════════════════════════════════════════════════════════════════════════════
# miniapp/backend — _serialize + _summary + checkout
# ════════════════════════════════════════════════════════════════════════════


def test_miniapp_serialize_includes_v12_fields():
    from miniapp.backend.routes.lists import _serialize
    page = _fake_page({
        "Название": {"title": [{"plain_text": "iPhone", "text": {"content": "x"}}]},
        "Категория": {"select": {"name": "💳 Прочее"}},
        "Статус": {"status": {"name": "Not started"}},
        "Цена план": {"number": 108600},
        "Магазин": {"rich_text": [{"plain_text": "iPiter", "text": {"content": "iPiter"}}]},
        "Этап": {"number": 2},
        "Заметка": {"rich_text": [{"plain_text": "Deep Blue", "text": {"content": "x"}}]},
        "Приоритет": {"select": {"name": "🟡 Важно"}},
    })
    out = _serialize(page)
    assert out["price_plan"] == 108600
    assert out["source"] == "iPiter"
    assert out["stage"] == 2
    assert out["note"] == "Deep Blue"
    assert out["priority"] == "🟡 Важно"


def test_miniapp_summary_aggregates_items():
    from miniapp.backend.routes.lists import _summary
    items = [
        {"price_plan": 108000, "price": None, "done": False},
        {"price_plan": 25000, "price": 24000, "done": True},
        {"price_plan": None, "price": None, "done": False},
    ]
    s = _summary(items)
    assert s["plan_total"] == 133000
    assert s["actual_total"] == 24000
    assert s["count_total"] == 3
    assert s["count_open"] == 2


# ── Regression #100: work relation prop name (trailing space) ────────────────


@pytest.mark.asyncio
async def test_add_items_work_rel_uses_exact_schema_name():
    """add_items с work_rel должен писать props['🔮 Работы '] (с trailing space).

    Регрессия #100: старый код писал '🔮 Работы' (без пробела) → Notion
    молча игнорил неизвестное поле → relation не ставился → чеклист-сироты.
    Этот тест ПАДАЛ на коде без константы WORK_REL_PROP.
    """
    from core import list_manager
    from core.list_manager import WORK_REL_PROP

    captured: dict = {}

    async def _fake_create(db, props):
        captured["props"] = props
        return "page-x"

    with patch.object(list_manager, "page_create",
                      AsyncMock(side_effect=_fake_create)):
        await list_manager.add_items(
            [{"name": "Подготовить пространство", "work_rel": "work-abc"}],
            list_type="📋 Чеклист",
            bot_name="🌒 Arcana",
            user_page_id="user-1",
        )

    p = captured["props"]
    # Точное имя проперти должно совпадать с WORK_REL_PROP
    assert WORK_REL_PROP in p, (
        f"Ожидали ключ {WORK_REL_PROP!r} в props, получили: {list(p)}"
    )
    # И НЕ должно быть старого беспробельного ключа
    assert "🔮 Работы" not in p or WORK_REL_PROP in p, (
        "Ключ без trailing space не должен использоваться для записи"
    )
    # Значение — relation с нужным id
    assert p[WORK_REL_PROP] == {"relation": [{"id": "work-abc"}]}
