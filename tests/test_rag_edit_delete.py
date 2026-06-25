"""tests/test_rag_edit_delete.py — RAG/тема инвалидация при правке/удалении (#165 #166).

Правка триплета → reindex вектора (index_triplet с RU-картами) + clear_theme_summary.
Удаление триплета → delete_triplet (убрать из pgvector) + clear_theme_summary.
Провал RAG НЕ роняет операцию (данные уже в PG). Моки, без реальной сети.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from arcana.handlers import sessions as S
from arcana.repos.sessions_repo import TripletEntry


def _entry():
    # cards в EN-canonical (как лежит в PG) — проверим, что reindex эмбедит RU.
    return TripletEntry(
        id="42", question="что чувствует", cards="Eight of Swords, The Lovers",
        interpretation="<p>старое</p>", deck="Уэйт", session_name="Вадим",
        client_id="c1", date="2026-06-22",
        amount=Decimal("0"), paid=Decimal("0"),
    )


# ── Правка ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_edit_reindexes_with_ru_cards_and_clears_theme(monkeypatch):
    monkeypatch.setattr(S, "_resolve_triplet_page", AsyncMock(return_value=_entry()))
    monkeypatch.setattr(S, "ask_claude", AsyncMock(return_value="<p>новая трактовка</p>"))
    monkeypatch.setattr(S, "_make_triplet_summary", AsyncMock(return_value="кратко"))
    monkeypatch.setattr(S._repo, "update_interpretation", AsyncMock())
    monkeypatch.setattr(S._repo, "clear_session_summary", AsyncMock())
    clear_theme = AsyncMock()
    monkeypatch.setattr(S._repo, "clear_theme_summary", clear_theme)
    monkeypatch.setattr("arcana.pending_tarot.delete_pending", AsyncMock())

    index_mock = MagicMock(return_value=True)
    monkeypatch.setattr("core.rag.index_triplet", index_mock)

    msg = MagicMock()
    msg.from_user.id = 123
    msg.answer = AsyncMock()

    await S.handle_triplet_correction(msg, "поправь тон", {"triplet_short_id": "abc"}, "u1")

    # reindex вызван
    index_mock.assert_called_once()
    args = index_mock.call_args.args
    assert args[0] == "42"                  # page_id
    cards_arg = args[1]                     # карты для эмбеддинга
    # КРИТИЧНО: карты в RU-форме, не EN (иначе вектор разойдётся с корпусом)
    assert "Eight of Swords" not in cards_arg
    assert "Мечей" in cards_arg and "Влюблённые" in cards_arg
    assert args[3] == "<p>новая трактовка</p>"  # новый interpretation
    # тема инвалидирована
    clear_theme.assert_awaited_once_with("Вадим", "c1")


@pytest.mark.asyncio
async def test_edit_survives_rag_failure(monkeypatch):
    monkeypatch.setattr(S, "_resolve_triplet_page", AsyncMock(return_value=_entry()))
    monkeypatch.setattr(S, "ask_claude", AsyncMock(return_value="<p>новая</p>"))
    monkeypatch.setattr(S, "_make_triplet_summary", AsyncMock(return_value="кратко"))
    monkeypatch.setattr(S._repo, "update_interpretation", AsyncMock())
    monkeypatch.setattr(S._repo, "clear_session_summary", AsyncMock())
    monkeypatch.setattr(S._repo, "clear_theme_summary", AsyncMock())
    delete_pending = AsyncMock()
    monkeypatch.setattr("arcana.pending_tarot.delete_pending", delete_pending)

    # index_triplet кидает → _rag_index_safe гасит, правка доходит до конца.
    monkeypatch.setattr("core.rag.index_triplet",
                        MagicMock(side_effect=RuntimeError("pg down")))

    msg = MagicMock()
    msg.from_user.id = 123
    msg.answer = AsyncMock()

    await S.handle_triplet_correction(msg, "поправь", {"triplet_short_id": "abc"}, "u1")
    # дошли до конца (pending удалён) несмотря на провал RAG
    delete_pending.assert_awaited()


# ── Удаление ──────────────────────────────────────────────────────────────────

def _call():
    call = MagicMock()
    call.answer = AsyncMock()
    call.data = "triplet_remove_yes:abc"
    call.from_user.id = 123
    call.message.edit_text = AsyncMock()
    return call


@pytest.mark.asyncio
async def test_delete_removes_vector_and_clears_theme(monkeypatch):
    monkeypatch.setattr("core.user_manager.get_user_notion_id", AsyncMock(return_value="u1"))
    monkeypatch.setattr(S, "_resolve_triplet_page", AsyncMock(return_value=_entry()))
    monkeypatch.setattr(S._repo, "archive", AsyncMock(return_value=True))
    monkeypatch.setattr(S._repo, "clear_session_summary", AsyncMock())
    clear_theme = AsyncMock()
    monkeypatch.setattr(S._repo, "clear_theme_summary", clear_theme)
    del_mock = MagicMock(return_value=True)
    monkeypatch.setattr("core.rag.delete_triplet", del_mock)

    call = _call()
    await S.cb_triplet_remove_yes(call)

    del_mock.assert_called_once_with("42")        # вектор убран по id
    clear_theme.assert_awaited_once_with("Вадим", "c1")
    call.message.edit_text.assert_awaited()       # дошли до «удалён»


@pytest.mark.asyncio
async def test_delete_survives_rag_failure(monkeypatch):
    monkeypatch.setattr("core.user_manager.get_user_notion_id", AsyncMock(return_value="u1"))
    monkeypatch.setattr(S, "_resolve_triplet_page", AsyncMock(return_value=_entry()))
    monkeypatch.setattr(S._repo, "archive", AsyncMock(return_value=True))
    monkeypatch.setattr(S._repo, "clear_session_summary", AsyncMock())
    monkeypatch.setattr(S._repo, "clear_theme_summary", AsyncMock())
    monkeypatch.setattr("core.rag.delete_triplet",
                        MagicMock(side_effect=RuntimeError("pg down")))

    call = _call()
    await S.cb_triplet_remove_yes(call)
    # удаление состоялось несмотря на провал RAG
    call.message.edit_text.assert_awaited_with("🗑 Триплет удалён.")
