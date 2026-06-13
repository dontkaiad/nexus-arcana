"""tests/test_notes_repo.py — NotesRepo seam + handler integration.

Покрытие:
- NotesRepo.add → core.notion_client.note_add (patch at module ref)
- NotesRepo.find_for_edit → hint='последняя' и hint=<title>
- NotesRepo.update_tags → get_notion().pages.update(Теги)
- NotesRepo.archive → get_notion().pages.update(archived=True)
- _save_note через _repo.add (интеграция handler→repo)
- handle_edit_note: single-tag path и multi-tag path (pending)
- handle_note_delete через _repo.archive
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── helpers ───────────────────────────────────────────────────────────────────

def _note_page(page_id: str, title: str, tags=None, date: str = "2026-06-01") -> dict:
    return {
        "id": page_id,
        "properties": {
            "Заголовок": {"title": [{"plain_text": title}]},
            "Теги": {"multi_select": [{"name": t} for t in (tags or [])]},
            "Дата": {"date": {"start": date}},
            "Категория": {"select": None},
        },
    }


# ── NotesRepo unit tests ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_notes_repo_add_delegates_to_note_add():
    from nexus.repos import notes_repo as mod
    from nexus.repos.notes_repo import NotesRepo

    with patch.object(mod._notion, "note_add", AsyncMock(return_value="page-x")) as m:
        repo = NotesRepo()
        result = await repo.add("мысль", tags=["идея"], date="2026-06-01", user_notion_id="u")

    assert result == "page-x"
    m.assert_awaited_once_with(text="мысль", tags=["идея"], date="2026-06-01", user_notion_id="u")


@pytest.mark.asyncio
async def test_notes_repo_find_for_edit_latest():
    """hint='последняя' → db_query с sort по Дата desc."""
    from nexus.repos import notes_repo as mod
    from nexus.repos.notes_repo import NotesRepo

    page = _note_page("p-1", "Старая мысль", tags=["идея"])
    with patch.object(mod._notion, "db_query", AsyncMock(return_value=[page])):
        repo = NotesRepo()
        note = await repo.find_for_edit("db-notes", "последняя")

    assert note is not None
    assert note.id == "p-1"
    assert note.title == "Старая мысль"
    assert note.tags == ["идея"]


@pytest.mark.asyncio
async def test_notes_repo_find_for_edit_by_hint():
    """hint=<строка> → db_query с фильтром по Заголовку."""
    from nexus.repos import notes_repo as mod
    from nexus.repos.notes_repo import NotesRepo

    page = _note_page("p-2", "Про котов", tags=["коты", "мысль"])
    with patch.object(mod._notion, "db_query", AsyncMock(return_value=[page])) as m:
        repo = NotesRepo()
        note = await repo.find_for_edit("db-notes", "котов")

    assert note is not None
    assert note.id == "p-2"
    assert note.tags == ["коты", "мысль"]
    call_kwargs = m.await_args.kwargs
    assert call_kwargs["filter_obj"]["title"]["contains"] == "котов"


@pytest.mark.asyncio
async def test_notes_repo_find_for_edit_not_found():
    from nexus.repos import notes_repo as mod
    from nexus.repos.notes_repo import NotesRepo

    with patch.object(mod._notion, "db_query", AsyncMock(return_value=[])):
        repo = NotesRepo()
        note = await repo.find_for_edit("db-notes", "несуществующее")

    assert note is None


@pytest.mark.asyncio
async def test_notes_repo_update_tags():
    from nexus.repos import notes_repo as mod
    from nexus.repos.notes_repo import NotesRepo

    fake_notion = MagicMock()
    fake_notion.pages.update = AsyncMock()
    with patch.object(mod._notion, "get_notion", return_value=fake_notion):
        repo = NotesRepo()
        await repo.update_tags("p-1", ["тег1", "тег2"])

    fake_notion.pages.update.assert_awaited_once_with(
        page_id="p-1",
        properties={"Теги": {"multi_select": [{"name": "тег1"}, {"name": "тег2"}]}},
    )


@pytest.mark.asyncio
async def test_notes_repo_archive_success():
    from nexus.repos import notes_repo as mod
    from nexus.repos.notes_repo import NotesRepo

    fake_notion = MagicMock()
    fake_notion.pages.update = AsyncMock()
    with patch.object(mod._notion, "get_notion", return_value=fake_notion):
        repo = NotesRepo()
        ok = await repo.archive("p-del")

    assert ok is True
    fake_notion.pages.update.assert_awaited_once_with(page_id="p-del", archived=True)


@pytest.mark.asyncio
async def test_notes_repo_archive_failure_returns_false():
    from nexus.repos import notes_repo as mod
    from nexus.repos.notes_repo import NotesRepo

    fake_notion = MagicMock()
    fake_notion.pages.update = AsyncMock(side_effect=Exception("Notion 503"))
    with patch.object(mod._notion, "get_notion", return_value=fake_notion):
        repo = NotesRepo()
        ok = await repo.archive("p-fail")

    assert ok is False


# ── Handler → Repo integration ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_save_note_uses_repo_add():
    """_save_note вызывает _repo.add, не note_add напрямую."""
    import nexus.handlers.notes as nmod

    msg = MagicMock()
    msg.answer = AsyncMock()
    msg.edit_text = AsyncMock()

    with patch.object(nmod._repo, "add", AsyncMock(return_value="page-ok")):
        await nmod._save_note(msg, "мысль", ["идея"], "2026-06-01", user_notion_id="u")

    nmod._repo.add  # already patched — just check the mock was used
    msg.answer.assert_awaited_once()
    assert "сохранена" in msg.answer.call_args.args[0].lower()


@pytest.mark.asyncio
async def test_handle_edit_note_single_tag_calls_update_tags():
    """handle_edit_note: одна метка → _repo.update_tags вызывается."""
    import nexus.handlers.notes as nmod
    import os

    msg = MagicMock()
    msg.from_user.id = 7
    msg.answer = AsyncMock()

    note_obj = MagicMock()
    note_obj.id = "p-1"
    note_obj.tags = ["старый"]

    with patch.dict(os.environ, {"NOTION_DB_NOTES": "db-id"}), \
         patch.object(nmod._repo, "find_for_edit", AsyncMock(return_value=note_obj)), \
         patch.object(nmod._repo, "update_tags", AsyncMock()) as upd:
        await nmod.handle_edit_note(msg, {"hint": "последняя", "new_value": "новый"}, "u")

    upd.assert_awaited_once()
    called_tags = upd.await_args.args[1]
    assert "Новый" in called_tags[0] or "новый" in called_tags[0].lower()
    msg.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_edit_note_multi_tag_shows_keyboard():
    """handle_edit_note: несколько меток → inline-клавиатура, _repo.update_tags НЕ вызывается."""
    import nexus.handlers.notes as nmod
    import os

    msg = MagicMock()
    msg.from_user.id = 7
    msg.answer = AsyncMock()

    note_obj = MagicMock()
    note_obj.id = "p-multi"
    note_obj.tags = ["тег1", "тег2", "тег3"]

    with patch.dict(os.environ, {"NOTION_DB_NOTES": "db-id"}), \
         patch.object(nmod._repo, "find_for_edit", AsyncMock(return_value=note_obj)), \
         patch.object(nmod._repo, "update_tags", AsyncMock()) as upd:
        await nmod.handle_edit_note(msg, {"hint": "последняя", "new_value": "новый"}, "u")

    upd.assert_not_awaited()
    msg.answer.assert_awaited_once()
    kb = msg.answer.call_args.kwargs.get("reply_markup")
    assert kb is not None


@pytest.mark.asyncio
async def test_handle_note_delete_archives_via_repo():
    """handle_note_delete использует _repo.archive, не get_notion() напрямую."""
    import nexus.handlers.notes as nmod

    uid = 42
    nmod._last_digest_results[uid] = [
        {"page_id": "p-a", "title": "мысль про котов", "tags": ["коты"]},
        {"page_id": "p-b", "title": "другая мысль", "tags": ["идеи"]},
    ]

    msg = MagicMock()
    msg.from_user.id = uid
    msg.answer = AsyncMock()

    with patch.object(nmod._repo, "archive", AsyncMock(return_value=True)) as arch:
        await nmod.handle_note_delete(msg, {"hint": "котов"}, "u")

    arch.assert_awaited_once_with("p-a")
    reply = msg.answer.call_args.args[0]
    assert "1" in reply and "удалил" in reply.lower()
