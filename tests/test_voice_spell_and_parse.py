"""tests/test_voice_spell_and_parse.py — голос: spell-whitelist для транскрипта,
запрет галлюцинации карты в парсере, локальное логирование транскрипта.

Корень бага (recon): голосовой транскрипт шёл мимо текстового spell-слоя
(route_message пропускает уже заданный _text), поэтому мисхёрды («крыльева
мечей») не чинились whitelist'ом карт, а парсер выдумывал ближайшую карту.
"""
from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

REPO = Path(__file__).resolve().parent.parent


def _msg(text="", _photo=None):
    m = MagicMock()
    m.from_user.id = 42
    m.chat.id = 1
    m.text = text
    m.caption = None
    m.photo = _photo
    m.reply_to_message = None
    m.answer = AsyncMock()
    return m


# ───────────── FIX 1: голос прогоняется через normalize_text ────────────────

def _route_patches(normalize_spy):
    """Глушим все pending-проверки route_message; терминируем на handle_list_pending."""
    return [
        patch("arcana.handlers.base.react", AsyncMock()),
        patch("core.preprocess.normalize_text", normalize_spy),
        patch("arcana.handlers.client_photo.handle_pending_text", AsyncMock(return_value=False)),
        patch("arcana.handlers.ritual_writeoff.handle_pending_edit", AsyncMock(return_value=False)),
        patch("arcana.handlers.barter_prompt.handle_pending_text", AsyncMock(return_value=False)),
        patch("arcana.pending_clients.get_pending_client", AsyncMock(return_value=None)),
        patch("arcana.handlers.grimoire.check_pending_search", AsyncMock(return_value=False)),
        patch("arcana.pending_tarot.get_pending", AsyncMock(return_value=None)),
        # терминируем тут — до роутера; нормализация (или её пропуск) уже произошла
        patch("arcana.handlers.lists.handle_list_pending", AsyncMock(return_value=True)),
    ]


@pytest.mark.asyncio
async def test_route_message_skips_normalize_for_voice_text():
    """Голос приходит как _text (уже нормализован в handle_voice) → route_message
    НЕ нормализует повторно (нет двойного прохода)."""
    from arcana.handlers import base
    import contextlib
    spy = AsyncMock(side_effect=lambda t, **kw: t)
    with contextlib.ExitStack() as st:
        for p in _route_patches(spy):
            st.enter_context(p)
        await base.route_message(_msg(text="x"), user_notion_id="u",
                                 _text="королева мечей")
    spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_route_message_normalizes_typed_text():
    """Печатный путь (без _text) — spell-correction вызывается ровно один раз."""
    from arcana.handlers import base
    import contextlib
    spy = AsyncMock(side_effect=lambda t, **kw: t)
    with contextlib.ExitStack() as st:
        for p in _route_patches(spy):
            st.enter_context(p)
        await base.route_message(_msg(text="расклад на работу"), user_notion_id="u")
    spy.assert_awaited_once()


def test_handle_voice_calls_normalize_before_route():
    """handle_voice (nested в create_dp_and_bot, не импортируется) — source-guard:
    транскрипт прогоняется через normalize_text ДО route_message."""
    src = (REPO / "arcana" / "bot.py").read_text(encoding="utf-8")
    start = src.index("async def handle_voice")
    end = src.index("async def handle_photo", start)
    body = src[start:end]
    # сверяем порядок ВЫЗОВОВ (call-syntax), не упоминаний в комментах
    assert "normalize_text(text" in body, "голос не чистится whitelist-spell'ом"
    assert body.index("normalize_text(text") < body.index("route_message(msg"), \
        "normalize_text должен примениться ДО парсинга (route_message)"


# ───────────── FIX 2: парсер не выдумывает карту ────────────────────────────

def test_parse_prompt_forbids_card_hallucination():
    from arcana.handlers.sessions import PARSE_SESSION_SYSTEM as p
    assert "НЕ ВЫДУМЫВАЙ КАРТУ" in p
    assert "НЕ подменяй ближайшей знакомой картой" in p
    assert "НЕ угадывай ранг" in p
    assert "ДОСЛОВНО" in p
    assert "null" in p


# ───────────── FIX 3: транскрипт логируется локально, НЕ в TG ────────────────

def test_voice_module_has_no_tg_log_sink():
    """Source-guard приватности: voice.py НЕ зовёт log_error/notify_log_group —
    транскрипт (личный текст) не может утечь в TG-группу логов из этого модуля."""
    src = (REPO / "core" / "voice.py").read_text(encoding="utf-8")
    # call-syntax (комменты могут упоминать имена как прозу)
    assert "log_error(" not in src
    assert "notify_log_group(" not in src
    # сам транскрипт логируется локально, на DEBUG
    assert "logger.debug" in src


class _FakeResp:
    status = 200
    headers: dict = {}

    async def json(self):
        return {"text": "королева мечей"}

    async def text(self):
        return ""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeSession:
    def __init__(self, *a, **kw):
        pass

    def post(self, *a, **kw):
        return _FakeResp()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


@pytest.mark.asyncio
async def test_transcribe_logs_transcript_at_debug(caplog):
    """transcribe пишет сам текст в локальный лог на DEBUG (для отладки голоса)."""
    import core.voice as voice
    with patch.object(voice, "config", MagicMock(openai_key="sk-test")), \
         patch.object(voice.aiohttp, "ClientSession", _FakeSession):
        with caplog.at_level(logging.DEBUG, logger="nexus.voice"):
            out = await voice.transcribe(b"audio-bytes")
    assert out == "королева мечей"
    debug_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("королева мечей" in m for m in debug_msgs), \
        "транскрипт должен попадать в DEBUG-лог локально"
