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
    """Если Haiku вернул мусор — отвечаем подсказкой, не падаем."""
    msg = AsyncMock()
    msg.from_user.id = 67686090
    msg.text = "🤷"

    with patch.object(lists_mod, "_haiku_parse", AsyncMock(return_value={"items": []})), \
         patch.object(lists_mod, "add_items", AsyncMock(return_value=[])) as p_add, \
         patch.object(lists_mod, "react", AsyncMock()):
        await lists_mod.handle_list_inv_add(
            msg, {"text": msg.text}, user_notion_id="user-page-id",
        )

    p_add.assert_not_called()
    assert msg.answer.call_count == 1
    assert "Не смог разобрать" in msg.answer.call_args.args[0]
