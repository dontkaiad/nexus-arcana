"""tests/test_arcana_ritual_place_routing.py — «работа на кладбище …» и прочие
места практики уходят в Аркану, не редиректятся в Nexus.

Место ритуала — закрытая таксономия ``ritual_place`` (home/forest/graveyard/
crossroad/water/church/…). Упоминание такого места (+ разговорные синонимы:
кладбище/могила, распутье, озеро/пруд) = сильный сигнал Арканы, даже без
слова «ритуал». Контроль: явный мусор всё ещё редиректится в Nexus.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


def _msg(text: str):
    from unittest.mock import MagicMock
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


async def _route(text: str, haiku_resp: str):
    """Прогнать route_message с замоканным Haiku-роутером, вернуть какие
    handler'ы дёрнулись."""
    from arcana.handlers import base
    with patch("arcana.handlers.base.ask_claude", AsyncMock(return_value=haiku_resp)), \
         patch("arcana.handlers.works.handle_add_work", AsyncMock()) as work_mock, \
         patch("arcana.handlers.rituals.handle_add_ritual", AsyncMock()) as ritual_mock, \
         patch("arcana.handlers.intent_resolve.send_nexus_redirect", AsyncMock()) as redirect_mock:
        await base.route_message(_msg(text), user_notion_id="u")
    return work_mock, ritual_mock, redirect_mock


# ── routing ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_work_on_graveyard_goes_to_arcana(common_patches):
    """«работа на кладбище сегодня в 17» → создаёт Работу (handle_add_work),
    НЕ редирект в Nexus — даже если Haiku ошибочно вернул nexus_redirect."""
    work, ritual, redirect = await _route(
        "работа на кладбище сегодня в 17", "nexus_redirect")
    work.assert_awaited_once()
    redirect.assert_not_called()
    ritual.assert_not_called()


@pytest.mark.asyncio
async def test_past_tense_place_goes_to_ritual(common_patches):
    """Прошедшее время + место → ritual_done → handle_add_ritual."""
    work, ritual, redirect = await _route(
        "сделала откуп на перекрёстке", "nexus_redirect")
    ritual.assert_awaited_once()
    redirect.assert_not_called()
    work.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "занести на погост в субботу",
    "поехать к озеру провести обряд",
    "сходить в церковь поставить свечи за упокой",
    "закопать на могиле",
])
async def test_various_places_not_redirected(common_patches, text):
    work, ritual, redirect = await _route(text, "nexus_redirect")
    redirect.assert_not_called()
    assert work.await_count or ritual.await_count


@pytest.mark.asyncio
async def test_garbage_still_redirects_to_nexus(common_patches):
    """Контроль: обычная бытовая задача без места практики → всё ещё Nexus."""
    work, ritual, redirect = await _route("выкинуть мусор", "nexus_redirect")
    redirect.assert_awaited_once()
    work.assert_not_called()
    ritual.assert_not_called()


@pytest.mark.asyncio
async def test_wfh_not_treated_as_practice(common_patches):
    """«работа из дома» = WFH, не практика (дом исключён из сигнала)."""
    work, ritual, redirect = await _route("работа из дома весь день", "nexus_redirect")
    redirect.assert_awaited_once()


# ── unit: mentions_ritual_place ────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("работа на кладбище", True),
    ("ритуал на погосте", True),
    ("оставить на могиле", True),
    ("на перекрёстке ночью", True),
    ("на перекрестке ночью", True),
    ("обряд у водоёма", True),
    ("бросить в озеро", True),
    ("сходить в церковь", True),
    ("в храме", True),
    ("работа в лесу", True),
    ("выкинуть мусор", False),
    ("работа из дома", False),
    ("поправить поле ввода в форме", False),
    ("купить молоко", False),
])
def test_mentions_ritual_place(text, expected):
    from core.ritual_places import mentions_ritual_place
    assert mentions_ritual_place(text) is expected


def test_place_synonyms_resolve_in_parser_map():
    from arcana.repos.pg_rituals_repo import _PLACE_TO_CODE
    assert _PLACE_TO_CODE["кладбище"] == "graveyard"
    assert _PLACE_TO_CODE["могила"] == "graveyard"
    assert _PLACE_TO_CODE["озеро"] == "water"
    assert _PLACE_TO_CODE["храм"] == "church"
    assert _PLACE_TO_CODE["распутье"] == "crossroad"


def test_looks_like_practice_covers_places():
    from arcana.handlers.intent_resolve import looks_like_practice
    assert looks_like_practice("работа на кладбище") is True
    assert looks_like_practice("сделать миниапп") is False
