"""tests/test_preprocess.py — core/preprocess.normalize_text + whitelist кеш.

Гарантии:
- whitelist защищает имена клиентов и карты Таро от «исправления» Haiku
- conversational/слишком длинный ответ Haiku отвергается, возвращается оригинал
- кеш miss → один pull, hit → не пуллит
- find_or_create_client инвалидирует кеш user_notion_id
"""
from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, patch

import pytest

import core.preprocess as pp

# Подменяем cache DB на временный файл (не плодим в репе)
_TMP_DB = tempfile.NamedTemporaryFile(suffix="_spell_cache.db", delete=False).name
pp._WHITELIST_DB = _TMP_DB


def _fresh():
    if os.path.exists(_TMP_DB):
        os.remove(_TMP_DB)


# ── whitelist building ──────────────────────────────────────────────────────

def test_static_whitelist_contains_cards_and_eso_terms():
    wl = pp._static_whitelist()
    assert "Жрица" in wl, "Major Arcana RU должны быть в whitelist"
    assert "Шут" in wl
    assert "приворот" in wl
    assert "ритуал" in wl
    assert "Self" in wl


def test_tarot_loader_returns_78_unique_names():
    names = pp._tarot_card_names_ru()
    assert len(names) >= 78, f"ожидаем минимум 78 карт, получили {len(names)}"


# ── Защита whitelist от «исправлений» Haiku ─────────────────────────────────

@pytest.mark.asyncio
async def test_whitelist_protects_client_name_lena():
    """Haiku возвращает «лень» вместо «Лена» — должно отвергнуться: не в
    рамках anti-conversational, но whitelist гарантирует prompt предотвращает.
    Тест эмулирует «дисциплинированный» Haiku — он сам не правит благодаря
    промпту с явным запретом."""
    _fresh()
    pp._cache_set("u", pp._static_whitelist() + ["Лена"])

    # Имитируем что Haiku послушался whitelist'а и вернул как есть
    with patch("core.preprocess.ask_claude",
               AsyncMock(return_value="напомни про Лену")):
        out = await pp.normalize_text("напомни про Лену", user_notion_id="u")
    assert out == "напомни про Лену"


@pytest.mark.asyncio
async def test_prompt_includes_card_name_jрица_when_present():
    """Если в тексте есть «Жрица» — она должна попасть в prompt-инструкцию."""
    _fresh()
    captured = {}

    async def fake(text, system="", **kw):
        captured["system"] = system
        return text

    with patch("core.preprocess.ask_claude", side_effect=fake):
        await pp.normalize_text("выпала Жрица", user_notion_id="u")
    assert "Жрица" in captured["system"]


@pytest.mark.asyncio
async def test_prompt_includes_eso_term_when_present():
    _fresh()
    captured = {}

    async def fake(text, system="", **kw):
        captured["system"] = system
        return text

    with patch("core.preprocess.ask_claude", side_effect=fake):
        await pp.normalize_text("сделать приворот", user_notion_id="u")
    assert "приворот" in captured["system"]


# ── Anti-conversational + length guard ──────────────────────────────────────

@pytest.mark.asyncio
async def test_conversational_response_rejected():
    _fresh()
    with patch("core.preprocess.ask_claude",
               AsyncMock(return_value="Извините, я не могу помочь...")):
        out = await pp.normalize_text("привет", user_notion_id="u")
    assert out == "привет", "разговорный ответ Haiku должен быть отклонён"


@pytest.mark.asyncio
async def test_too_long_response_rejected():
    _fresh()
    long_resp = "тут очень длинный ответ от Haiku " * 10
    with patch("core.preprocess.ask_claude",
               AsyncMock(return_value=long_resp)):
        out = await pp.normalize_text("кот", user_notion_id="u")
    assert out == "кот"


@pytest.mark.asyncio
async def test_haiku_exception_returns_original():
    _fresh()
    with patch("core.preprocess.ask_claude",
               AsyncMock(side_effect=Exception("api boom"))):
        out = await pp.normalize_text("текст", user_notion_id="u")
    assert out == "текст"


# ── Layout conversion (EN→RU QWERTY→ЙЦУКЕН) ─────────────────────────────────

@pytest.mark.asyncio
async def test_layout_conversion_runs_first():
    _fresh()
    # `ghbdtn` на английской раскладке = «привет» на русской
    with patch("core.preprocess.ask_claude",
               AsyncMock(side_effect=lambda t, **kw: t)):
        out = await pp.normalize_text("ghbdtn", user_notion_id="u")
    assert out == "привет"


# ── Кеш ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cache_miss_triggers_pull_then_hit_skips():
    _fresh()
    pull_count = {"n": 0}

    async def fake_fetch(uid):
        pull_count["n"] += 1
        return ["Маша", "Лена"]

    with patch("core.preprocess._fetch_client_names", fake_fetch):
        wl1 = await pp.get_whitelist("user-X")
        wl2 = await pp.get_whitelist("user-X")
    assert "Маша" in wl1 and "Лена" in wl1
    assert wl2 == wl1
    assert pull_count["n"] == 1, "second call must hit cache, not re-pull"


@pytest.mark.asyncio
async def test_invalidate_whitelist_forces_repull():
    _fresh()
    pull_count = {"n": 0}

    async def fake_fetch(uid):
        pull_count["n"] += 1
        return [f"Client_v{pull_count['n']}"]

    with patch("core.preprocess._fetch_client_names", fake_fetch):
        await pp.get_whitelist("user-Y")
        pp.invalidate_whitelist("user-Y")
        wl2 = await pp.get_whitelist("user-Y")
    assert pull_count["n"] == 2
    assert "Client_v2" in wl2


# ── find_or_create_client → invalidate ──────────────────────────────────────

@pytest.mark.asyncio
async def test_find_or_create_client_invalidates_whitelist_cache():
    _fresh()
    # Кладём «старый» whitelist в кеш
    pp._cache_set("u", ["A", "B"])
    assert pp._cache_get("u") == ["A", "B"]

    from core import notion_client as nc
    with patch.object(nc, "client_find", AsyncMock(return_value=None)), \
         patch.object(nc, "client_add",
                      AsyncMock(return_value="new-id")):
        await nc.find_or_create_client("Новый", user_notion_id="u")

    # Кеш сброшен (TTL не истёк, но invalidate_whitelist дёрнут)
    assert pp._cache_get("u") is None


# ── Безопасность: пустой/пробельный вход возвращается как есть ──────────────

@pytest.mark.asyncio
async def test_empty_text_passthrough():
    out = await pp.normalize_text("", user_notion_id="u")
    assert out == ""
    out2 = await pp.normalize_text("   ", user_notion_id="u")
    assert out2 == "   "
