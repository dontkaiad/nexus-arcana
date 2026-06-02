"""tests/test_inv_add_batch.py — regression #75.

`handle_list_inv_add` падал на batch-вводе: Haiku возвращал list, а код звал
`.get(...)` на нём. Покрываем нормализацию + smoke на batch-флоу без
обращения к Notion (через моки).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from nexus.handlers import lists as lists_mod


def test_normalize_inv_items_dict_with_items():
    parsed = {"items": [
        {"name": "меновазин", "quantity": 2, "category": "🏥 Здоровье"},
        {"name": "уголь", "quantity": 1, "note": "250мг 30шт"},
    ]}
    out = lists_mod._normalize_inv_items(parsed)
    assert len(out) == 2
    assert out[0]["name"] == "меновазин" and out[0]["quantity"] == 2
    assert out[1]["note"] == "250мг 30шт"


def test_normalize_inv_items_legacy_single_dict():
    parsed = {"item": "парацетамол", "quantity": 1, "category": "🏥 Здоровье"}
    out = lists_mod._normalize_inv_items(parsed)
    assert len(out) == 1
    assert out[0]["name"] == "парацетамол"
    assert out[0]["category"] == "🏥 Здоровье"


def test_normalize_inv_items_bare_list():
    parsed = [
        {"name": "ромашка", "quantity": 1},
        {"name": "шалфей", "quantity": 1},
    ]
    out = lists_mod._normalize_inv_items(parsed)
    assert [it["name"] for it in out] == ["ромашка", "шалфей"]


def test_normalize_inv_items_defaults_for_missing_fields():
    out = lists_mod._normalize_inv_items({"items": [{"name": "x"}]})
    assert out[0]["quantity"] == 1
    assert out[0]["category"] == "💳 Прочее"
    assert out[0]["note"] == ""


def test_normalize_inv_items_skips_empty_names():
    out = lists_mod._normalize_inv_items({"items": [
        {"name": ""},
        {"item": None},
        {"name": "valid"},
        "junk-string",
    ]})
    assert [it["name"] for it in out] == ["valid"]


def test_normalize_inv_items_garbage_returns_empty():
    assert lists_mod._normalize_inv_items(None) == []
    assert lists_mod._normalize_inv_items("not json") == []
    assert lists_mod._normalize_inv_items(42) == []


@pytest.mark.asyncio
async def test_handle_list_inv_add_batch_does_not_ask_expiry():
    """5 позиций → одно сводное сообщение, без pending_set на срок годности."""
    parsed = {"items": [
        {"name": "меновазин", "quantity": 2, "category": "🏥 Здоровье"},
        {"name": "уголь", "quantity": 1, "note": "250мг", "category": "🏥 Здоровье"},
        {"name": "амоксиклав", "quantity": 1, "note": "500+125мг", "category": "🏥 Здоровье"},
        {"name": "гексикон", "quantity": 1, "category": "🏥 Здоровье"},
        {"name": "ромашка", "quantity": 1, "category": "🏥 Здоровье"},
    ]}
    created = [
        {"id": f"page-{i}", "name": it["name"], "type": "📦 Инвентарь", "category": it["category"]}
        for i, it in enumerate(parsed["items"])
    ]

    msg = AsyncMock()
    msg.from_user.id = 67686090
    msg.text = "занеси в инвентарь\nменовазин 2 шт\n..."

    with patch.object(lists_mod, "_haiku_parse", AsyncMock(return_value=parsed)), \
         patch.object(lists_mod, "add_items", AsyncMock(return_value=created)), \
         patch.object(lists_mod, "react", AsyncMock()), \
         patch.object(lists_mod, "pending_set") as p_set:
        await lists_mod.handle_list_inv_add(
            msg, {"text": msg.text}, user_notion_id="user-page-id",
        )

    p_set.assert_not_called()
    assert msg.answer.call_count == 1
    sent = msg.answer.call_args.args[0]
    assert "5 позиций" in sent
    assert "меновазин" in sent and "× 2" in sent
    assert "ромашка" in sent


@pytest.mark.asyncio
async def test_handle_list_inv_add_single_asks_expiry():
    """Одиночный ввод сохраняет поведение: добавляет + спрашивает срок."""
    parsed = {"items": [{"name": "парацетамол", "quantity": 1, "category": "🏥 Здоровье"}]}
    created = [{"id": "page-1", "name": "парацетамол", "type": "📦 Инвентарь", "category": "🏥 Здоровье"}]

    msg = AsyncMock()
    msg.from_user.id = 67686090
    msg.text = "дома есть парацетамол"

    with patch.object(lists_mod, "_haiku_parse", AsyncMock(return_value=parsed)), \
         patch.object(lists_mod, "add_items", AsyncMock(return_value=created)), \
         patch.object(lists_mod, "react", AsyncMock()), \
         patch.object(lists_mod, "pending_set") as p_set:
        await lists_mod.handle_list_inv_add(
            msg, {"text": msg.text}, user_notion_id="user-page-id",
        )

    p_set.assert_called_once()
    pending_args = p_set.call_args.args
    assert pending_args[1]["action"] == "inv_expiry"
    assert pending_args[1]["item_name"] == "парацетамол"
    assert msg.answer.call_count == 2
    assert "Срок годности" in msg.answer.call_args_list[1].args[0]


@pytest.mark.asyncio
async def test_handle_list_inv_add_empty_parse_responds_gracefully():
    """Если Haiku вернул мусор И fallback ничего не извлёк — отвечаем подсказкой.

    Используем ввод состоящий только из префикса без позиций — тогда fallback
    тоже вернёт [], потому что body_lines пуст.
    """
    msg = AsyncMock()
    msg.from_user.id = 67686090
    msg.text = "занеси в инвентарь"

    with patch.object(lists_mod, "_haiku_parse", AsyncMock(return_value={"items": []})), \
         patch.object(lists_mod, "add_items", AsyncMock(return_value=[])) as p_add, \
         patch.object(lists_mod, "react", AsyncMock()):
        await lists_mod.handle_list_inv_add(
            msg, {"text": msg.text}, user_notion_id="user-page-id",
        )

    p_add.assert_not_called()
    assert msg.answer.call_count == 1
    assert "Не смог разобрать" in msg.answer.call_args.args[0]


# ── #76: regex-fallback и category-хинты ─────────────────────────────────────

def test_category_from_hint_health():
    assert lists_mod._category_from_hint("лекарства") == "🏥 Здоровье"
    assert lists_mod._category_from_hint("ТАБЛЕТКИ") == "🏥 Здоровье"
    assert lists_mod._category_from_hint("аптечка") == "🏥 Здоровье"


def test_category_from_hint_other_categories():
    assert lists_mod._category_from_hint("продукты") == "🍜 Продукты"
    assert lists_mod._category_from_hint("еду") == "🍜 Продукты"
    assert lists_mod._category_from_hint("бытовая химия") == "🧹 Дом"
    assert lists_mod._category_from_hint("косметика") == "💄 Красота"
    assert lists_mod._category_from_hint("инструменты") == "🔧 Инструменты"


def test_category_from_hint_unknown():
    assert lists_mod._category_from_hint("xyz") == ""
    assert lists_mod._category_from_hint("") == ""


def test_fallback_split_with_category_prefix():
    text = (
        "занеси в инвентарь лекарства\n"
        "сироп солодки (немного)\n"
        "рициниол базовый 30мл\n"
        "зубные нити"
    )
    items = lists_mod._fallback_split_inv_text(text)
    assert [it["name"] for it in items] == [
        "сироп солодки (немного)", "рициниол базовый 30мл", "зубные нити",
    ]
    assert all(it["category"] == "🏥 Здоровье" for it in items)
    assert all(it["quantity"] == 1 for it in items)


def test_fallback_split_without_category_uses_default():
    text = "занеси в инвентарь\nфонарик\nверёвка"
    items = lists_mod._fallback_split_inv_text(text)
    assert [it["name"] for it in items] == ["фонарик", "верёвка"]
    assert all(it["category"] == "💳 Прочее" for it in items)


def test_fallback_split_strips_bullet_chars():
    text = "добавь в инвентарь продукты\n• молоко\n- хлеб\n— сыр"
    items = lists_mod._fallback_split_inv_text(text)
    assert [it["name"] for it in items] == ["молоко", "хлеб", "сыр"]
    assert all(it["category"] == "🍜 Продукты" for it in items)


def test_fallback_split_empty_returns_empty():
    assert lists_mod._fallback_split_inv_text("") == []
    assert lists_mod._fallback_split_inv_text("занеси в инвентарь лекарства") == []


@pytest.mark.asyncio
async def test_handle_list_inv_add_uses_fallback_when_haiku_returns_empty():
    """Haiku вернул items=[] — переходим на regex-fallback, создаём айтемы."""
    text = (
        "занеси в инвентарь лекарства\n"
        "сироп солодки (немного)\n"
        "рициниол базовый 30мл\n"
        "зубные нити"
    )
    fallback_items = lists_mod._fallback_split_inv_text(text)
    created = [
        {"id": f"p{i}", "name": it["name"], "type": "📦 Инвентарь", "category": it["category"]}
        for i, it in enumerate(fallback_items)
    ]

    msg = AsyncMock()
    msg.from_user.id = 67686090
    msg.text = text

    with patch.object(lists_mod, "_haiku_parse", AsyncMock(return_value={"items": []})), \
         patch.object(lists_mod, "add_items", AsyncMock(return_value=created)) as p_add, \
         patch.object(lists_mod, "react", AsyncMock()), \
         patch.object(lists_mod, "pending_set") as p_set:
        await lists_mod.handle_list_inv_add(
            msg, {"text": text}, user_notion_id="user-page-id",
        )

    p_add.assert_called_once()
    sent_items = p_add.call_args.args[0]
    assert [it["name"] for it in sent_items] == [
        "сироп солодки (немного)", "рициниол базовый 30мл", "зубные нити",
    ]
    assert all(it["category"] == "🏥 Здоровье" for it in sent_items)
    p_set.assert_not_called()
    assert "3 позиций" in msg.answer.call_args.args[0]


@pytest.mark.asyncio
async def test_handle_list_inv_add_uses_fallback_when_haiku_raises():
    """Haiku упал — regex-fallback всё равно спасает ввод."""
    text = "занеси в инвентарь лекарства\nпарацетамол"

    msg = AsyncMock()
    msg.from_user.id = 67686090
    msg.text = text

    with patch.object(lists_mod, "_haiku_parse", AsyncMock(side_effect=ValueError("bad json"))), \
         patch.object(lists_mod, "add_items", AsyncMock(return_value=[
             {"id": "p1", "name": "парацетамол", "type": "📦 Инвентарь", "category": "🏥 Здоровье"},
         ])), \
         patch.object(lists_mod, "react", AsyncMock()), \
         patch.object(lists_mod, "pending_set") as p_set:
        await lists_mod.handle_list_inv_add(
            msg, {"text": text}, user_notion_id="user-page-id",
        )

    # 1 элемент → должен спросить срок годности
    p_set.assert_called_once()
