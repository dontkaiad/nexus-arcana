"""Сбой LLM API: Arcana не пишет «Не поняла», не создаёт мусорных записей из fallback,
владелец получает алерт о кредитах не чаще раза в 6 часов. Без сети."""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import httpx
import pytest

import arcana.handlers.grimoire as gmod
import arcana.handlers.work_preview as wp
from core import claude_client as cc
from core import llm_health as lh
from core import pending_kv

_TMP_DB = tempfile.NamedTemporaryFile(suffix="_pending_works_llmdown.db", delete=False).name
wp._PENDING_DB = _TMP_DB

NOTICE = "⚠️ LLM сейчас недоступен (проверь баланс Anthropic или повтори позже)"
_REQ = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _err(cls, status, msg="x"):
    resp = httpx.Response(status, request=_REQ, json={"type": "error", "error": {"message": msg}})
    return cls(msg, response=resp, body={"error": {"message": msg}})


ERRORS = {
    "billing": _err(anthropic.BadRequestError, 400, "Your credit balance is too low to access the Anthropic API."),
    "429": _err(anthropic.RateLimitError, 429, "slow down"),
    "5xx": _err(anthropic.InternalServerError, 500, "overloaded"),
    "timeout": anthropic.APITimeoutError(request=_REQ),
}


@pytest.fixture(autouse=True)
def _clean():
    lh._state.update(kind=None, since=0.0)
    pending_kv.delete(lh._KV_UID, lh._KV_KIND)
    if os.path.exists(_TMP_DB):
        os.remove(_TMP_DB)
    with patch.object(lh, "_send_alert", AsyncMock()):
        yield
    lh._state.update(kind=None, since=0.0)
    pending_kv.delete(lh._KV_UID, lh._KV_KIND)


def _msg(text):
    m = MagicMock()
    m.from_user.id = 42
    m.chat.id = 1
    m.text = text
    m.caption = None
    m.photo = None
    m.reply_to_message = None
    m.answer = AsyncMock()
    return m


def _texts(msg):
    return [str(c.args[0]) if c.args else str(c.kwargs.get("text", "")) for c in msg.answer.await_args_list]


@pytest.fixture
def route_patches():
    with patch("arcana.handlers.base.react", AsyncMock()), \
         patch("arcana.pending_clients.get_pending_client", AsyncMock(return_value=None)), \
         patch("arcana.handlers.grimoire.check_pending_search", AsyncMock(return_value=False)), \
         patch("arcana.pending_tarot.get_pending", AsyncMock(return_value=None)), \
         patch("arcana.handlers.work_preview.has_pending", return_value=False), \
         patch("arcana.handlers.lists.handle_list_pending", AsyncMock(return_value=False)), \
         patch("core.preprocess.normalize_text", AsyncMock(side_effect=lambda t, **kw: t)):
        yield


# ── ask_claude: явный признак сбоя, обратная совместимость ──────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("name", list(ERRORS))
async def test_strict_raises_and_default_still_returns_empty(name):
    with patch.object(cc, "_create_message", AsyncMock(side_effect=ERRORS[name])):
        assert await cc.ask_claude("p", role="parse") == ""                      # как раньше
        with pytest.raises(cc.LLMUnavailable) as ei:
            await cc.ask_claude("p", role="parse", strict=True)
    assert ei.value.kind == ("billing" if name == "billing" else "api")


# ── Arcana route ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("name", list(ERRORS))
async def test_route_shows_unavailable_not_ne_ponyala(name, route_patches):
    from arcana.handlers import base
    msg = _msg("что у Ани по раскладам")                   # детерминированный pre-filter не срабатывает
    with patch.object(cc, "_create_message", AsyncMock(side_effect=ERRORS[name])):
        await base.route_message(msg, user_id="u")
    texts = _texts(msg)
    assert NOTICE in texts[0]
    assert not any("Не поняла" in t for t in texts)
    assert len(texts) == 1                                  # middleware не дублирует уведомление


@pytest.mark.asyncio
async def test_deterministic_route_does_not_need_llm_to_route(route_patches):
    """«ритуал … завтра в 19:00» маршрутизируется без LLM; создание работы упирается в парсер → явное уведомление."""
    from arcana.handlers import base
    msg = _msg("ритуал очищение завтра в 19:00 дома")
    create = AsyncMock()
    with patch.object(cc, "_create_message", AsyncMock(side_effect=ERRORS["billing"])), \
         patch.object(wp._works_repo, "create", create), \
         patch("arcana.handlers.work_preview.get_effective_tz", AsyncMock(return_value=(3, "UTC+3"))):
        await base.route_message(msg, user_id="u")
    texts = _texts(msg)
    assert any(NOTICE in t for t in texts) and not any("Не поняла" in t or "Что за работа" in t for t in texts)
    create.assert_not_awaited()
    assert wp._pending_get(42) is None                      # мусорный partial-pending не создан


# ── парсеры ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("name", list(ERRORS))
async def test_work_parser_unavailable_no_partial_no_record(name):
    msg = _msg("сделать ритуал завтра")
    create = AsyncMock()
    with patch.object(cc, "_create_message", AsyncMock(side_effect=ERRORS[name])), \
         patch.object(wp._works_repo, "create", create), \
         patch("arcana.handlers.work_preview.get_effective_tz", AsyncMock(return_value=(3, "UTC+3"))):
        await wp.handle_add_work_preview(msg, "сделать ритуал завтра", "u")
    assert NOTICE in _texts(msg)[0] and len(_texts(msg)) == 1
    create.assert_not_awaited()
    assert wp._pending_get(42) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("name", list(ERRORS))
async def test_grimoire_unavailable_no_record(name):
    msg = _msg("x")
    with patch.object(cc, "_create_message", AsyncMock(side_effect=ERRORS[name])), \
         patch.object(gmod._repo, "add", AsyncMock(return_value="g")) as add, \
         patch.object(gmod, "log_error", AsyncMock()) as log_err:
        await gmod.handle_grimoire_add(msg, "заговор на дорогу — читать на растущую луну", user_id="u")
    assert NOTICE in _texts(msg)[0] and len(_texts(msg)) == 1
    add.assert_not_awaited()
    log_err.assert_not_awaited()                            # это не «ошибка бота»


# ── алерт владельцу ─────────────────────────────────────────────────────────

def test_alert_text_and_6h_dedup():
    assert lh.ALERT_EVERY_SEC == 6 * 3600
    assert lh._alert_text("billing") == "⚠️ Anthropic: кончились кредиты, LLM не работает"
    sent = AsyncMock()
    with patch.object(lh, "_send_alert", sent):
        async def run():
            for _ in range(3):
                lh.on_error(ERRORS["billing"])
            await asyncio.sleep(0)
        asyncio.run(run())
    assert sent.await_count == 1


def test_alert_has_no_secrets_or_request_text():
    t = lh._alert_text("billing") + lh._alert_text("auth")
    for bad in ("sk-", "http", "api_key", "token"):
        assert bad not in t.lower()


# ── лог: одна строка, без URL ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_api_error_logged_in_one_line_without_url(caplog):
    with patch.object(cc, "_create_message", AsyncMock(side_effect=ERRORS["billing"])), \
         caplog.at_level(logging.ERROR, logger="core.claude_client"):
        await cc.ask_claude("секретный текст запроса", role="parse")
    recs = [r for r in caplog.records if r.name == "core.claude_client"]
    assert len(recs) == 1
    line = recs[0].getMessage()
    assert "\n" not in line and "400" in line and "BadRequestError" in line
    assert "http" not in line.lower() and "секретный" not in line
