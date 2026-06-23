"""tests/test_card_correction.py — правка «Поправить» реально меняет КАРТУ (#166).

Раньше handle_triplet_correction писал только interpretation (update_cards в репо
не было) → правка «королева кубков, а не король» меняла ТЕКСТ, но заголовок-карта
оставался старым (противоречие). Теперь смена карты:
  парс card_edit → пересбор cards → перечитать справочник → перегенерить трактовку
  → update_cards + update_interpretation → RAG reindex новыми cards → подтверждение.
Правка ТЕКСТА карту не трогает.
"""
from __future__ import annotations

import contextlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arcana.handlers import sessions
from arcana.handlers.sessions import _parse_card_edit, handle_triplet_correction


# ───────────────────────── _parse_card_edit (Haiku) ─────────────────────────

@pytest.mark.asyncio
async def test_parse_card_edit_detects_card_change():
    resp = json.dumps({
        "card_edit": True,
        "cards": ["король кубков", "шут", "маг"],
        "bottom_card": None,
    })
    with patch.object(sessions, "ask_claude", AsyncMock(return_value=resp)) as ask:
        out = await _parse_card_edit("королева кубков, а не король", "королева кубков, шут, маг", "")
    assert out == {"cards_ru": "король кубков, шут, маг", "bottom_ru": ""}
    # парсер карт — Haiku (дёшево), не Sonnet
    assert ask.await_args.kwargs["model"] == "claude-haiku-4-5-20251001"


@pytest.mark.asyncio
async def test_parse_card_edit_text_only_returns_none():
    resp = json.dumps({"card_edit": False, "cards": None, "bottom_card": None})
    with patch.object(sessions, "ask_claude", AsyncMock(return_value=resp)):
        out = await _parse_card_edit("добавь про деньги", "королева кубков, шут, маг", "")
    assert out is None


# ───────────────────── handle_triplet_correction (saved) ────────────────────

def _entry():
    return SimpleNamespace(
        id="1", question="что чувствует", cards="King of Cups, Fool, Magician",
        interpretation="<p>старая</p>", deck="Уэйт", session_name="Тема",
        client_id="c1", bottom_card="", date="2026-06-01",
    )


def _correction_patches(repo, get_ctx, card_edit_ret, rag):
    return [
        patch.object(sessions, "_resolve_triplet_page", AsyncMock(return_value=_entry())),
        patch.object(sessions, "_parse_card_edit", AsyncMock(return_value=card_edit_ret)),
        patch.object(sessions, "_repo", repo),
        patch.object(sessions, "ask_claude", AsyncMock(return_value="<p>новая трактовка</p>")),
        patch.object(sessions, "_make_triplet_summary", AsyncMock(return_value="саммари")),
        patch.object(sessions, "_rag_index_safe", rag),
        patch.object(sessions, "_cards_to_ru", MagicMock(return_value="королева кубков, шут, маг")),
        patch.object(sessions, "_canon_card_ru", MagicMock(return_value="")),
        patch.object(sessions, "_canon_cards_str", MagicMock(return_value="King of Cups, Fool, Magician")),
        patch.object(sessions, "_canon_card", MagicMock(return_value="")),
        patch("arcana.tarot_loader.get_cards_context", get_ctx),
        patch("arcana.pending_tarot.delete_pending", AsyncMock()),
        patch("core.session_cache.cache_delete", MagicMock()),
        patch("core.session_cache.session_summary_key", MagicMock(return_value="k")),
    ]


def _repo_mock():
    r = MagicMock()
    r.update_interpretation = AsyncMock()
    r.update_cards = AsyncMock()
    r.clear_session_summary = AsyncMock(return_value=0)
    r.clear_theme_summary = AsyncMock(return_value=0)
    return r


def _msg():
    m = MagicMock()
    m.from_user.id = 42
    m.answer = AsyncMock(return_value=MagicMock(chat=MagicMock(id=1), message_id=2))
    return m


@pytest.mark.asyncio
async def test_card_edit_updates_cards_context_interp_rag_and_confirms():
    """Смена карты: update_cards вызван (новые EN-карты), справочник перечитан
    для НОВОЙ карты, трактовка перегенерена, RAG reindex новыми cards_ru,
    подтверждение смены показано."""
    repo = _repo_mock()
    get_ctx = MagicMock(return_value="<контекст новой карты>")
    card_edit = {"cards_ru": "король кубков, шут, маг", "bottom_ru": ""}
    rag = AsyncMock()
    msg = _msg()
    with contextlib.ExitStack() as st:
        for p in _correction_patches(repo, get_ctx, card_edit, rag):
            st.enter_context(p)
        await handle_triplet_correction(
            msg, "королева кубков, а не король",
            {"triplet_short_id": "abc"}, user_notion_id="u",
        )

    # 1) данные карт обновлены (canonical-EN)
    repo.update_cards.assert_awaited_once()
    assert repo.update_cards.await_args.args[1] == "King of Cups, Fool, Magician"
    # 2) справочник перечитан для НОВОЙ карты
    ctx_cards = get_ctx.call_args.args[1]
    assert any("король кубков" in c for c in ctx_cards), "справочник не для новой карты"
    # 3) трактовка перезаписана
    repo.update_interpretation.assert_awaited_once()
    # 4) RAG reindex новыми cards_ru
    assert rag.await_args.kwargs["cards"] == "король кубков, шут, маг"
    # 5) подтверждение смены видно пользователю
    sent = " ".join(str(c.args[0]) for c in msg.answer.await_args_list)
    assert "🔄 Карта обновлена" in sent
    assert "король кубков" in sent


@pytest.mark.asyncio
async def test_text_only_edit_does_not_touch_cards():
    """Правка ТЕКСТА (card_edit=None): update_cards НЕ вызван, нет подтверждения смены."""
    repo = _repo_mock()
    get_ctx = MagicMock(return_value="<контекст>")
    rag = AsyncMock()
    msg = _msg()
    with contextlib.ExitStack() as st:
        for p in _correction_patches(repo, get_ctx, None, rag):  # None = текстовая правка
            st.enter_context(p)
        await handle_triplet_correction(
            msg, "перепиши мягче", {"triplet_short_id": "abc"}, user_notion_id="u",
        )

    repo.update_cards.assert_not_awaited()
    repo.update_interpretation.assert_awaited_once()
    # справочник для СТАРЫХ карт (cards_ru не менялся)
    assert rag.await_args.kwargs["cards"] == "королева кубков, шут, маг"
    sent = " ".join(str(c.args[0]) for c in msg.answer.await_args_list)
    assert "🔄 Карта обновлена" not in sent
