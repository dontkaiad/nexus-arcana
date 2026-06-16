"""tests/test_grimoire_parse.py — robustness of «запиши в гримуар: …» pipeline.

Покрываем:
 1. _parse_json_safe умеет вытащить JSON из markdown-fence.
 2. _heuristic_grimoire_parse даёт sane результат когда Haiku молчит.
 3. handle_grimoire_add падает на heuristic при пустом ответе и пишет в Notion.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import arcana.handlers.grimoire as gmod
from arcana.handlers.grimoire import (
    _heuristic_grimoire_parse,
    _parse_json_safe,
    handle_grimoire_add,
)


def test_parse_json_safe_strips_markdown_fence():
    raw = '```json\n{"title": "тест", "category": "заговор"}\n```'
    assert _parse_json_safe(raw)["title"] == "тест"


def test_parse_json_safe_extracts_object_from_noise():
    raw = 'Конечно, вот результат: {"title":"x","category":"заметка"} спасибо!'
    assert _parse_json_safe(raw)["title"] == "x"


def test_parse_json_safe_empty_on_garbage():
    assert _parse_json_safe("совсем не json") == {}


def test_heuristic_parses_money_spell():
    out = _heuristic_grimoire_parse(
        "запиши в гримуар: тест — заговор на деньги, читать на убывающую луну"
    )
    assert out["title"] == "тест"
    assert out["category"] == "заговор"
    assert "финансы" in out["themes"]
    assert "убывающую" in out["text"]


def test_heuristic_parses_recipe_oil():
    out = _heuristic_grimoire_parse("запиши в гримуар: масло защиты — рецепт на полнолуние")
    assert out["category"] == "рецепт"
    assert "защита" in out["themes"]


def test_heuristic_short_text_becomes_title():
    out = _heuristic_grimoire_parse("запиши в гримуар: заговор")
    assert out["title"] == "заговор"
    assert out["category"] == "заговор"


def test_pg_grimoire_repo_find_by_id_invalid_id():
    """_find_by_id_sync возвращает None для нечисловых ID (нет DB-вызова)."""
    from arcana.repos.pg_grimoire_repo import PgGrimoireRepo
    repo = PgGrimoireRepo()
    assert repo._find_by_id_sync("not-an-int", "") is None
    assert repo._find_by_id_sync("", "") is None
    assert repo._find_by_id_sync(None, "") is None


@pytest.mark.asyncio
async def test_grimoire_repo_find_by_id_delegates():
    """GrimoireRepo.find_by_id делегирует в PgGrimoireRepo.find_by_id."""
    from arcana.repos.grimoire_repo import GrimoireEntry, GrimoireRepo
    expected = GrimoireEntry(
        id="42", title="Тест", category="📿 Заговор",
        themes=[], verified=False, text="", source="",
    )
    with patch("arcana.repos.pg_grimoire_repo.PgGrimoireRepo.find_by_id",
               AsyncMock(return_value=expected)) as mock_find:
        result = await GrimoireRepo().find_by_id("42", "user-1")
    mock_find.assert_awaited_once_with("42", "user-1")
    assert result is expected


@pytest.mark.asyncio
async def test_handle_grimoire_add_falls_back_to_heuristic():
    msg = MagicMock()
    msg.answer = AsyncMock()
    text = "тест — заговор на деньги, читать на убывающую луну"
    with patch("arcana.handlers.grimoire.ask_claude",
               AsyncMock(return_value="не json вообще")), \
         patch.object(gmod._repo, "add",
               AsyncMock(return_value="page-1")) as ga:
        await handle_grimoire_add(msg, text, user_notion_id="u1")
    ga.assert_awaited_once()
    kwargs = ga.await_args.kwargs
    assert kwargs["title"] == "тест"
    assert kwargs["category"] == "📿 Заговор"
    # answer вызван с уведомлением «📖 Записано в гримуар …»
    assert msg.answer.await_count == 1
    msg.answer.assert_awaited()
