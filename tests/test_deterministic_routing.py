"""Сообщения «ритуал/расклад/сеанс …» маршрутизируются без LLM (баг: при недоступном
LLM роутер возвращал "" → меню «Не поняла … Что сделать?»)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arcana.handlers.intent_resolve import deterministic_intent as di


@pytest.mark.parametrize("text,expected", [
    ("ритуал очищение завтра в 19:00 дома", "ritual_planned"),
    ("ритуал на открытие дорог через род сегодня в 19:45 (день помина)", "ritual_planned"),
    ("Ритуал защиты завтра вечером", "ritual_planned"),       # «вечером» ≠ прошедшее время
    ("ритуал очищения провела маше, 3000", "ritual_done"),
    ("ритуал на привлечение", "ritual_ambiguous"),
    ("расклад на работу Игоря в пятницу", "session_planned"),
    ("расклад разложила маше: шут маг жрица", "session_done"),
    ("сеанс завтра в 15:00", "session_planned"),
    ("расклад шут маг жрица", None),                           # ввод карт — решает LLM
    ("расклады про отношения", None),                          # другое слово (поиск)
    ("расклад покажи про деньги", None),                       # поиск
    ("ритуал удали последний", None),
    ("купить свечи завтра", None),
    ("", None),
])
def test_deterministic_intent(text, expected):
    assert di(text) == expected


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


@pytest.fixture
def common_patches():
    with patch("arcana.handlers.base.react", AsyncMock()), \
         patch("arcana.pending_clients.get_pending_client", AsyncMock(return_value=None)), \
         patch("arcana.handlers.grimoire.check_pending_search", AsyncMock(return_value=False)), \
         patch("arcana.pending_tarot.get_pending", AsyncMock(return_value=None)), \
         patch("arcana.handlers.work_preview.has_pending", return_value=False), \
         patch("arcana.handlers.lists.handle_list_pending", AsyncMock(return_value=False)), \
         patch("core.preprocess.normalize_text", AsyncMock(side_effect=lambda t, **kw: t)):
        yield


async def _route(text, llm_resp=""):
    from arcana.handlers import base
    llm = AsyncMock(return_value=llm_resp)
    msg = _msg(text)
    with patch("arcana.handlers.base.ask_claude", llm), \
         patch("arcana.handlers.works.handle_add_work", AsyncMock()) as work, \
         patch("arcana.handlers.rituals.handle_add_ritual", AsyncMock()) as ritual, \
         patch("arcana.handlers.intent_resolve.ask_ritual_disambiguation", AsyncMock()) as amb:
        await base.route_message(msg, user_id="u")
    route_calls = [c for c in llm.await_args_list if c.kwargs.get("role") == "route"]
    return work, ritual, amb, route_calls, msg


@pytest.mark.asyncio
async def test_ritual_planned_goes_to_work_flow_without_llm(common_patches):
    """Тот самый баг: LLM недоступен (пустой ответ) → раньше меню «Не поняла»."""
    work, ritual, amb, route_calls, msg = await _route("ритуал очищение завтра в 19:00 дома")
    work.assert_awaited_once()
    assert route_calls == []
    for c in msg.answer.await_args_list:
        assert "Не поняла" not in str(c)


@pytest.mark.asyncio
async def test_ritual_done_goes_to_ritual_flow_without_llm(common_patches):
    work, ritual, amb, route_calls, _ = await _route("ритуал очищения провела маше, 3000")
    ritual.assert_awaited_once()
    assert route_calls == [] and not work.await_count


@pytest.mark.asyncio
async def test_bare_ritual_asks_disambiguation_without_llm(common_patches):
    work, ritual, amb, route_calls, _ = await _route("ритуал на привлечение")
    amb.assert_awaited_once()
    assert route_calls == []


@pytest.mark.asyncio
async def test_other_text_still_uses_llm_router(common_patches):
    work, ritual, amb, route_calls, _ = await _route("купить свечи завтра", "nexus_redirect")
    assert len(route_calls) == 1
