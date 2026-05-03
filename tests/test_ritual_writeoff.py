"""tests/test_ritual_writeoff.py — расходники после ритуала."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _isolate_pending_db(monkeypatch, tmp_path):
    from arcana.handlers import ritual_writeoff
    db = tmp_path / "pending_writeoff.db"
    monkeypatch.setattr(ritual_writeoff, "_DB", str(db))
    yield


def test_heuristic_parser_picks_qty_and_unit():
    from arcana.handlers.ritual_writeoff import _heuristic_parse
    out = _heuristic_parse("соль 50г, свеча 1шт, травы")
    names = {x["name"]: x for x in out}
    assert names["соль"]["qty"] == 50.0
    assert names["соль"]["unit"] == "г"
    assert names["свеча"]["unit"] == "шт"
    assert names["травы"]["qty"] is None


@pytest.mark.asyncio
async def test_propose_writeoff_shows_preview_with_inventory_match():
    from arcana.handlers.ritual_writeoff import propose_writeoff, _load
    msg = MagicMock()
    msg.from_user.id = 100
    msg.answer = AsyncMock()

    inventory_results = [
        [{"name": "соль", "quantity": 200}],   # для "соль"
        [],                                      # "травы" не нашлась
    ]
    with patch("arcana.handlers.ritual_writeoff.parse_supplies",
               AsyncMock(return_value=[
                   {"name": "соль", "qty": 50, "unit": "г"},
                   {"name": "травы", "qty": None, "unit": ""},
               ])), \
         patch("arcana.handlers.ritual_writeoff.inventory_search",
               AsyncMock(side_effect=inventory_results)):
        await propose_writeoff(msg, "соль 50г, травы", user_notion_id="u1")

    msg.answer.assert_awaited_once()
    args, kwargs = msg.answer.await_args
    text = args[0]
    assert "соль" in text and "200" in text and "150" in text
    assert "НЕТ В ИНВЕНТАРЕ" in text
    assert kwargs.get("reply_markup") is not None
    pending = _load(100)
    assert pending and len(pending["rows"]) == 2


@pytest.mark.asyncio
async def test_apply_callback_writes_inventory():
    from arcana.handlers.ritual_writeoff import _apply, _save
    rows = [
        {"name": "соль", "needed": 50, "unit": "г",
         "current": 200, "after": 150, "found": True, "inventory_name": "соль"},
        {"name": "травы", "needed": None, "unit": "", "found": False},
    ]
    with patch("arcana.handlers.ritual_writeoff.inventory_update",
               AsyncMock(return_value={"updated": "соль", "quantity": 150,
                                        "archived": False, "suggest_buy": False,
                                        "category": "🕯️ Расходники"})) as upd:
        notes = await _apply(rows, "u1")
    assert any("соль" in n and "150" in n for n in notes)
    assert any("травы" in n for n in notes)
    upd.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_pending_edit_recomputes_preview():
    from arcana.handlers.ritual_writeoff import handle_pending_edit, _save, _load
    _save(33, {"rows": [], "user_notion_id": "u1", "awaiting_edit": True})
    msg = MagicMock()
    msg.from_user.id = 33
    msg.answer = AsyncMock()
    with patch("arcana.handlers.ritual_writeoff.parse_supplies",
               AsyncMock(return_value=[{"name": "соль", "qty": 100, "unit": "г"}])), \
         patch("arcana.handlers.ritual_writeoff.inventory_search",
               AsyncMock(return_value=[{"name": "соль", "quantity": 200}])):
        handled = await handle_pending_edit(msg, "соль 100г")
    assert handled is True
    pending = _load(33)
    assert pending["awaiting_edit"] is False
    assert pending["rows"][0]["after"] == 100  # 200 - 100
