"""tests/test_session_merge.py — мердж сессий по lowercase + client_id."""
from unittest.mock import AsyncMock, patch

import pytest


def _page_with_session(name: str, client_id: str = None, date_iso: str = "2026-05-01"):
    p = {
        "id": f"p-{name}-{client_id or 'self'}",
        "properties": {
            "Сессия": {"rich_text": [{"plain_text": name}]},
            "Дата": {"date": {"start": date_iso}},
            "👥 Клиенты": {"relation": [{"id": client_id}] if client_id else []},
        },
    }
    return p


@pytest.mark.asyncio
async def test_canonical_uses_existing_capitalization():
    """Если уже есть запись 'Вадим', при вводе 'вадим' — используем 'Вадим'."""
    from core.notion_client import _resolve_canonical_session_name

    existing = [_page_with_session("Вадим", client_id="c1", date_iso="2026-04-20")]
    with patch("core.notion_client.query_pages", new=AsyncMock(return_value=existing)):
        canonical = await _resolve_canonical_session_name(
            "вадим", client_id="c1", user_notion_id="u1"
        )
        assert canonical == "Вадим"


@pytest.mark.asyncio
async def test_no_existing_returns_input_as_is():
    from core.notion_client import _resolve_canonical_session_name

    with patch("core.notion_client.query_pages", new=AsyncMock(return_value=[])):
        canonical = await _resolve_canonical_session_name(
            "Маша", client_id="c2", user_notion_id="u1"
        )
        assert canonical == "Маша"


@pytest.mark.asyncio
async def test_different_client_does_not_match():
    """Сессия 'Вадим' клиента c1 не должна цепляться к клиенту c2."""
    from core.notion_client import _resolve_canonical_session_name

    existing = [_page_with_session("Вадим", client_id="c1", date_iso="2026-04-20")]
    with patch("core.notion_client.query_pages", new=AsyncMock(return_value=existing)):
        canonical = await _resolve_canonical_session_name(
            "Вадим", client_id="c2", user_notion_id="u1"
        )
        # Не нашли совпадения по клиенту — возвращаем ввод как есть
        assert canonical == "Вадим"


@pytest.mark.asyncio
async def test_self_session_excludes_client_pages():
    """Self-сессия (client_id=None) не должна цепляться за клиентскую."""
    from core.notion_client import _resolve_canonical_session_name

    existing = [_page_with_session("Вадим", client_id="c1", date_iso="2026-04-20")]
    with patch("core.notion_client.query_pages", new=AsyncMock(return_value=existing)):
        canonical = await _resolve_canonical_session_name(
            "вадим", client_id=None, user_notion_id="u1"
        )
        # self != клиентская, не мерджим
        assert canonical == "вадим"


@pytest.mark.asyncio
async def test_picks_earliest_date_as_canonical():
    """Если по lowercase совпало несколько — берём имя из самой ранней записи."""
    from core.notion_client import _resolve_canonical_session_name

    existing = [
        _page_with_session("ВАДИМ", client_id="c1", date_iso="2026-05-01"),
        _page_with_session("Вадим", client_id="c1", date_iso="2026-04-20"),  # earlier
        _page_with_session("вадим", client_id="c1", date_iso="2026-04-25"),
    ]
    with patch("core.notion_client.query_pages", new=AsyncMock(return_value=existing)):
        canonical = await _resolve_canonical_session_name(
            "вадим", client_id="c1", user_notion_id="u1"
        )
        assert canonical == "Вадим"  # самая ранняя
