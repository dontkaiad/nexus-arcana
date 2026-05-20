"""tests/test_tip_validation.py — валидация и retry-flow ADHD-совета (issue #71).

Покрывает:
- _validate_tip: плейсхолдеры, длина (мин/макс)
- retry: первый брак → второй вызов
- fallback: дважды брак → статичная строка, без кеширования
- bad cache: кэш с плейсхолдером не отдаётся, генерится заново
"""
from unittest.mock import AsyncMock, patch

import pytest

from miniapp.backend.routes import today as today_mod
from miniapp.backend.routes.today import _validate_tip, _FALLBACK_TIP


# ─── _validate_tip ───────────────────────────────────────────────────────────

def test_validate_rejects_placeholder():
    valid, reason = _validate_tip("Положи штуку в одно место рядом с вещами потом")
    assert valid is False
    assert "placeholder" in reason


@pytest.mark.parametrize("word", ["штука", "вещь", "что-то", "нечто", "что-нибудь"])
def test_validate_rejects_each_placeholder(word):
    text = f"Сделай {word} утром и закрой задачу до обеда сегодня"
    assert _validate_tip(text)[0] is False


def test_validate_rejects_too_short():
    valid, reason = _validate_tip("Дыши, ты справишься.")
    assert valid is False
    assert "too short" in reason


def test_validate_rejects_too_long():
    valid, reason = _validate_tip(" ".join(["слово"] * 21))
    assert valid is False
    assert "too long" in reason


def test_validate_accepts_good_tip():
    text = "Поставь телефон у кровати — иначе утром забудешь его дома."
    valid, reason = _validate_tip(text)
    assert valid is True
    assert reason == ""


# ─── retry / fallback flow ───────────────────────────────────────────────────

GOOD = "Поставь телефон у кровати — иначе утром забудешь его дома."
BAD = "Положи штуку рядом с вещами потом."


def _patch_ctx(claude_mock):
    return [
        patch.object(today_mod, "ask_claude", claude_mock),
        patch.object(today_mod, "_adhd_context_memories", AsyncMock(return_value=[])),
        patch.object(today_mod.cache, "get_tip", lambda *a, **k: None),
        patch.object(today_mod.cache, "set_tip", lambda *a, **k: None),
    ]


@pytest.mark.asyncio
async def test_retry_then_success():
    """Первый ответ — брак, второй — ок. Возвращается второй."""
    claude_mock = AsyncMock(side_effect=[BAD, GOOD])
    sets = []
    with patch.object(today_mod, "ask_claude", claude_mock), \
         patch.object(today_mod, "_adhd_context_memories", AsyncMock(return_value=[])), \
         patch.object(today_mod.cache, "get_tip", lambda *a, **k: None), \
         patch.object(today_mod.cache, "set_tip", lambda tg, d, t: sets.append(t)):
        tip = await today_mod._generate_adhd_tip(1, "2026-05-20", [], "")
    assert tip == GOOD
    assert claude_mock.await_count == 2
    assert sets == [GOOD]  # успешный совет закеширован


@pytest.mark.asyncio
async def test_double_fail_returns_fallback_uncached():
    """Дважды брак → fallback, и он НЕ кешируется."""
    claude_mock = AsyncMock(side_effect=[BAD, BAD])
    sets = []
    with patch.object(today_mod, "ask_claude", claude_mock), \
         patch.object(today_mod, "_adhd_context_memories", AsyncMock(return_value=[])), \
         patch.object(today_mod.cache, "get_tip", lambda *a, **k: None), \
         patch.object(today_mod.cache, "set_tip", lambda tg, d, t: sets.append(t)):
        tip = await today_mod._generate_adhd_tip(1, "2026-05-20", [], "")
    assert tip == _FALLBACK_TIP
    assert claude_mock.await_count == 2
    assert sets == []  # fallback не попал в кеш


@pytest.mark.asyncio
async def test_first_try_success_no_retry():
    """Первый ответ валиден — второго вызова нет."""
    claude_mock = AsyncMock(return_value=GOOD)
    with patch.object(today_mod, "ask_claude", claude_mock), \
         patch.object(today_mod, "_adhd_context_memories", AsyncMock(return_value=[])), \
         patch.object(today_mod.cache, "get_tip", lambda *a, **k: None), \
         patch.object(today_mod.cache, "set_tip", lambda *a, **k: None):
        tip = await today_mod._generate_adhd_tip(1, "2026-05-20", [], "")
    assert tip == GOOD
    assert claude_mock.await_count == 1


@pytest.mark.asyncio
async def test_bad_cache_not_served():
    """Кэш с плейсхолдером игнорируется — генерим заново."""
    claude_mock = AsyncMock(return_value=GOOD)
    with patch.object(today_mod, "ask_claude", claude_mock), \
         patch.object(today_mod, "_adhd_context_memories", AsyncMock(return_value=[])), \
         patch.object(today_mod.cache, "get_tip",
                      lambda *a, **k: "Положи штуку рядом с вещами потом."), \
         patch.object(today_mod.cache, "set_tip", lambda *a, **k: None):
        tip = await today_mod._generate_adhd_tip(1, "2026-05-20", [], "")
    assert tip == GOOD
    assert claude_mock.await_count == 1
