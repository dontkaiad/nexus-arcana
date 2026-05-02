"""tests/test_bottom_card.py — запись и чтение поля «Дно колоды»."""
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_session_add_writes_bottom_card_field():
    """session_add получает bottom_card='King of Cups' → props['Дно колоды']
    содержит rich_text с этим значением."""
    from core.notion_client import session_add

    captured = {}

    async def fake_create(db_id, props):
        captured["db_id"] = db_id
        captured["props"] = props
        return "p1"

    async def fake_match_select(db_id, prop, val):
        return val

    async def fake_canon_session(name, cid, uid):
        return name

    with patch("core.notion_client.page_create", new=fake_create), \
         patch("core.notion_client.match_select", new=fake_match_select), \
         patch("core.notion_client._resolve_canonical_session_name", new=fake_canon_session):
        page_id = await session_add(
            date="2026-05-02T10:00:00",
            spread_type="Триплет",
            question="тест",
            cards="The Fool, The Magician, The High Priestess",
            interpretation="<p>x</p>",
            bottom_card="King of Cups",
        )

    assert page_id == "p1"
    assert "Дно колоды" in captured["props"]
    rt = captured["props"]["Дно колоды"]["rich_text"]
    assert rt[0]["text"]["content"] == "King of Cups"


@pytest.mark.asyncio
async def test_session_add_omits_bottom_field_when_empty():
    """Без bottom_card — нет ключа «Дно колоды» в props."""
    from core.notion_client import session_add

    captured = {}

    async def fake_create(db_id, props):
        captured["props"] = props
        return "p2"

    async def fake_match_select(db_id, prop, val):
        return val

    async def fake_canon_session(name, cid, uid):
        return name

    with patch("core.notion_client.page_create", new=fake_create), \
         patch("core.notion_client.match_select", new=fake_match_select), \
         patch("core.notion_client._resolve_canonical_session_name", new=fake_canon_session):
        await session_add(
            date="2026-05-02T10:00:00",
            spread_type="Триплет",
            cards="X, Y, Z",
        )

    assert "Дно колоды" not in captured["props"]


@pytest.mark.asyncio
async def test_session_add_falls_back_when_field_missing():
    """Если Notion ответил validation_error по «Дно колоды» — повторная
    попытка без новых полей."""
    from core.notion_client import session_add

    calls = {"n": 0}
    captured: list[dict] = []

    async def fake_create(db_id, props):
        captured.append(dict(props))
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("validation_error: Дно колоды is not a property")
        return "p3"

    async def fake_match_select(db_id, prop, val):
        return val

    async def fake_canon_session(name, cid, uid):
        return name

    with patch("core.notion_client.page_create", new=fake_create), \
         patch("core.notion_client.match_select", new=fake_match_select), \
         patch("core.notion_client._resolve_canonical_session_name", new=fake_canon_session):
        page_id = await session_add(
            date="2026-05-02",
            cards="A, B, C",
            bottom_card="King of Cups",
            session="Вадим",
        )

    assert page_id == "p3"
    assert "Дно колоды" in captured[0]
    assert "Дно колоды" not in captured[1]


@pytest.mark.asyncio
async def test_serialize_triplet_reads_bottom_field():
    """_serialize_triplet берёт дно из поля «Дно колоды» rich_text,
    игнорируя legacy-парсинг из interp."""
    from miniapp.backend.routes.arcana_sessions import _serialize_triplet

    page = {
        "id": "abc",
        "properties": {
            "Тема": {"title": [{"plain_text": "тест"}]},
            "Карты": {"rich_text": [{"plain_text": "The Fool, The Magician, The High Priestess"}]},
            "Дно колоды": {"rich_text": [{"plain_text": "King of Cups"}]},
            "Колоды": {"multi_select": [{"name": "Уэйт"}]},
            "Дата": {"date": {"start": "2026-05-02"}},
            "Тип сеанса": {"select": {"name": "🌟 Личный"}},
            "Тип расклада": {"multi_select": [{"name": "🔺 Триплет"}]},
            "Сбылось": {"select": {"name": "⏳ Не проверено"}},
            "Сумма": {"number": 0},
            "Оплачено": {"number": 0},
            "Трактовка": {"rich_text": [{"plain_text": "<p>x</p>"}]},
            "Саммари триплета": {"rich_text": []},
            "👥 Клиенты": {"relation": []},
        },
    }
    out = await _serialize_triplet(page, clients_map={}, tz_offset=3)
    assert out["bottom_card"] is not None
    assert out["bottom_card"]["en"] == "King of Cups"
    assert out["bottom_card"]["ru"] == "Король Кубков"
