"""tests/test_notes_repo.py — NotesRepo seam (PG) + handler integration.

Покрытие:
- NotesRepo.add → делегирует в _pg.add
- NotesRepo.find_for_edit (hint='последняя' и hint=<строка>)
- NotesRepo.update_tags → _pg.update_tags
- NotesRepo.archive → _pg.archive
- _save_note через _repo.add (интеграция handler→repo)
- handle_edit_note: single-tag path и multi-tag path (pending)
- handle_note_delete через _repo.archive
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_note(page_id="p-1", title="Тестовая", tags=None, date="2026-06-01"):
    from nexus.repos.pg_notes_repo import Note
    return Note(id=page_id, title=title, tags=tags or [], date=date)


# ── NotesRepo unit tests ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_notes_repo_add_delegates_to_pg():
    from nexus.repos.notes_repo import NotesRepo

    repo = NotesRepo()
    with patch.object(repo._pg, "add", AsyncMock(return_value="42")) as m:
        result = await repo.add("мысль", tags=["идея"], date="2026-06-01", user_notion_id="u")

    assert result == "42"
    m.assert_awaited_once_with(text="мысль", tags=["идея"], date="2026-06-01", user_notion_id="u")


@pytest.mark.asyncio
async def test_notes_repo_find_for_edit_latest():
    """hint='последняя' → find_for_edit передаёт hint PG."""
    from nexus.repos.notes_repo import NotesRepo

    note = _make_note("p-1", "Старая мысль", tags=["идея"])
    repo = NotesRepo()
    with patch.object(repo._pg, "find_for_edit", AsyncMock(return_value=note)) as m:
        result = await repo.find_for_edit("последняя", user_notion_id="u")

    assert result is not None
    assert result.id == "p-1"
    assert result.title == "Старая мысль"
    assert result.tags == ["идея"]
    m.assert_awaited_once_with("последняя", "u")


@pytest.mark.asyncio
async def test_notes_repo_find_for_edit_by_hint():
    """hint=<строка> → find_for_edit делегирует в PG."""
    from nexus.repos.notes_repo import NotesRepo

    note = _make_note("p-2", "Про котов", tags=["коты", "мысль"])
    repo = NotesRepo()
    with patch.object(repo._pg, "find_for_edit", AsyncMock(return_value=note)):
        result = await repo.find_for_edit("котов", user_notion_id="u")

    assert result is not None
    assert result.id == "p-2"
    assert result.tags == ["коты", "мысль"]


@pytest.mark.asyncio
async def test_notes_repo_find_for_edit_not_found():
    from nexus.repos.notes_repo import NotesRepo

    repo = NotesRepo()
    with patch.object(repo._pg, "find_for_edit", AsyncMock(return_value=None)):
        result = await repo.find_for_edit("несуществующее")

    assert result is None


@pytest.mark.asyncio
async def test_notes_repo_update_tags():
    from nexus.repos.notes_repo import NotesRepo

    repo = NotesRepo()
    with patch.object(repo._pg, "update_tags", AsyncMock()) as m:
        await repo.update_tags("42", ["тег1", "тег2"])

    m.assert_awaited_once_with("42", ["тег1", "тег2"])


@pytest.mark.asyncio
async def test_notes_repo_archive_success():
    from nexus.repos.notes_repo import NotesRepo

    repo = NotesRepo()
    with patch.object(repo._pg, "archive", AsyncMock(return_value=True)) as m:
        ok = await repo.archive("42")

    assert ok is True
    m.assert_awaited_once_with("42")


@pytest.mark.asyncio
async def test_notes_repo_archive_failure_returns_false():
    from nexus.repos.notes_repo import NotesRepo

    repo = NotesRepo()
    with patch.object(repo._pg, "archive", AsyncMock(return_value=False)):
        ok = await repo.archive("fail-id")

    assert ok is False


# ── Handler → Repo integration ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_save_note_uses_repo_add():
    """_save_note вызывает _repo.add."""
    import nexus.handlers.notes as nmod

    msg = MagicMock()
    msg.answer = AsyncMock()
    msg.edit_text = AsyncMock()

    with patch.object(nmod._repo, "add", AsyncMock(return_value="42")), \
         patch("nexus.handlers.notes.react", AsyncMock()):
        await nmod._save_note(msg, "мысль", ["идея"], "2026-06-01", user_notion_id="u")

    msg.answer.assert_awaited_once()
    assert "сохранена" in msg.answer.call_args.args[0].lower()


@pytest.mark.asyncio
async def test_handle_edit_note_single_tag_calls_update_tags():
    """handle_edit_note: одна метка → _repo.update_tags вызывается."""
    import nexus.handlers.notes as nmod

    msg = MagicMock()
    msg.from_user.id = 7
    msg.answer = AsyncMock()

    note_obj = _make_note("p-1", "тест", tags=["старый"])

    with patch.object(nmod._repo, "find_for_edit", AsyncMock(return_value=note_obj)), \
         patch.object(nmod._repo, "update_tags", AsyncMock()) as upd:
        await nmod.handle_edit_note(msg, {"hint": "последняя", "new_value": "новый"}, "u")

    upd.assert_awaited_once()
    called_tags = upd.await_args.args[1]
    assert any("новый" in t.lower() for t in called_tags)
    msg.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_edit_note_multi_tag_shows_keyboard():
    """handle_edit_note: несколько меток → inline-клавиатура, update_tags НЕ вызывается."""
    import nexus.handlers.notes as nmod

    msg = MagicMock()
    msg.from_user.id = 7
    msg.answer = AsyncMock()

    note_obj = _make_note("p-multi", "тест", tags=["тег1", "тег2", "тег3"])

    with patch.object(nmod._repo, "find_for_edit", AsyncMock(return_value=note_obj)), \
         patch.object(nmod._repo, "update_tags", AsyncMock()) as upd:
        await nmod.handle_edit_note(msg, {"hint": "последняя", "new_value": "новый"}, "u")

    upd.assert_not_awaited()
    msg.answer.assert_awaited_once()
    kb = msg.answer.call_args.kwargs.get("reply_markup")
    assert kb is not None


@pytest.mark.asyncio
async def test_handle_note_delete_archives_via_repo():
    """handle_note_delete использует _repo.archive."""
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
