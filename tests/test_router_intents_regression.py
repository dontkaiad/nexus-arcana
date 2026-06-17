"""tests/test_router_intents_regression.py — регресс по 10 типичным
вводам Кай + контракт: ROUTER зовёт Haiku, не Sonnet.

Тест проверяет ДИСПЕТЧИНГ (правильный handler вызван), а не качество
самого Haiku (Haiku мокаем). Защита если Haiku вернёт legacy
work/task — fallback из bd1ddb8 покрыт test_intent_fallback.py.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _msg(text: str) -> MagicMock:
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
    """Общий набор мок-патчей для всех тестов: pending всегда пуст,
    нет фото, нет reply, message_collector ничего не делает."""
    with patch("arcana.handlers.base.react", AsyncMock()), \
         patch("arcana.pending_clients.get_pending_client",
               AsyncMock(return_value=None)), \
         patch("arcana.handlers.grimoire.check_pending_search",
               AsyncMock(return_value=False)), \
         patch("arcana.pending_tarot.get_pending",
               AsyncMock(return_value=None)), \
         patch("arcana.handlers.work_preview.has_pending", return_value=False), \
         patch("arcana.handlers.lists.handle_list_pending",
               AsyncMock(return_value=False)), \
         patch("core.preprocess.normalize_text",
               AsyncMock(side_effect=lambda t, **kw: t)):
        yield


@pytest.mark.asyncio
async def test_router_uses_haiku_model(common_patches):
    """Контракт: ROUTER зовёт Haiku. Если Sonnet — деньги Кай."""
    from arcana.handlers import base
    captured = {}

    async def fake_ask(prompt, **kw):
        captured["model"] = kw.get("model")
        captured["system"] = kw.get("system", "")
        return "ritual_planned"

    msg = _msg("сделать ритуал")
    with patch("arcana.handlers.base.ask_claude", side_effect=fake_ask), \
         patch("arcana.handlers.works.handle_add_work", AsyncMock()):
        await base.route_message(msg, user_notion_id="u")

    assert captured.get("model") == "claude-haiku-4-5-20251001", \
        "ROUTER должен использовать Haiku, не Sonnet (деньги Кай)"


@pytest.mark.asyncio
async def test_router_prompt_has_8_fewshot_examples(common_patches):
    """Промпт включает минимум 8 пар «Вход:/Выход:» (паттерн bd1ddb8)."""
    from arcana.handlers.base import ROUTER_SYSTEM
    n = ROUTER_SYSTEM.count("Вход:")
    assert n >= 8, f"few-shot < 8 примеров (got {n})"
    # Ключевые intent'ы покрыты
    for must in ("ritual_planned", "ritual_done", "session_planned",
                 "session_done", "nexus_redirect"):
        assert must in ROUTER_SYSTEM


# ── 10 типичных вводов → ожидаемый handler вызван ───────────────────────────

INTENT_CASES = [
    # (haiku_returns, text, expected_handler, why)
    ("ritual_planned",  "сделать ритуал маше",         "work",   "инфинитив + клиент → planned"),
    ("ritual_done",     "провела маше ритуал на защиту", "ritual", "прошедшее → done"),
    ("session_planned", "разложу маше на работу завтра", "work",   "session_planned → preview-flow"),
    ("session_done",    "разложила машe три карты: шут маг жрица", "session", "прошедшее + карты → done"),
    ("ritual_planned",  "провести очищение в субботу",  "work",   "инфинитив без клиента → planned"),
    ("session_planned", "разложить себе на месяц",      "work",   "self-расклад planned (есть «разложить»)"),
    ("nexus_redirect",  "сделать миниапп",              "redirect", "не практика → nexus"),
    ("nexus_redirect",  "починить погоду на сайте",     "redirect", "не практика → nexus"),
    ("ritual_done",     "вчера делала ритуал на любовь", "ritual",  "вчера + ритуал → done"),
    ("session_search",  "что падало на машу",           "search",  "search-фраза"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("haiku_resp,text,expected,why", INTENT_CASES)
async def test_intent_dispatch_regression(
    common_patches, haiku_resp, text, expected, why,
):
    """Haiku вернул intent X → ожидаем что dispatch вызвал нужный handler."""
    from arcana.handlers import base

    msg = _msg(text)
    with patch("arcana.handlers.base.ask_claude",
               AsyncMock(return_value=haiku_resp)), \
         patch("arcana.handlers.works.handle_add_work",
               AsyncMock()) as work_mock, \
         patch("arcana.handlers.rituals.handle_add_ritual",
               AsyncMock()) as ritual_mock, \
         patch("arcana.handlers.sessions.handle_add_session",
               AsyncMock()) as session_mock, \
         patch("arcana.handlers.sessions.handle_session_search",
               AsyncMock()) as search_mock, \
         patch("arcana.handlers.intent_resolve.send_nexus_redirect",
               AsyncMock()) as redirect_mock:
        await base.route_message(msg, user_notion_id="u")

    handlers = {
        "work":     work_mock,
        "ritual":   ritual_mock,
        "session":  session_mock,
        "search":   search_mock,
        "redirect": redirect_mock,
    }
    handlers[expected].assert_awaited(), f"{why}: ждём вызов handlers[{expected}]"
    # Остальные не должны быть вызваны
    for k, mock in handlers.items():
        if k != expected:
            assert not mock.await_count, (
                f"{why}: handlers[{k}] не должен был быть вызван "
                f"(вызвано {mock.await_count} раз)"
            )


# ── B6 регресс: ritual_done без прошедшего → ambiguous, не planned ──────────

@pytest.mark.asyncio
async def test_ritual_done_no_past_tense_routes_to_disambiguation(common_patches):
    """Регресс B6: ritual_done без прошедшего времени → ritual_ambiguous (переспрос).

    До фикса guard конвертировал в ritual_planned → handle_add_work (Works).
    После: ritual_ambiguous → ask_ritual_disambiguation.
    """
    from arcana.handlers import base

    msg = _msg("ритуал чистка соль свечи")
    disambig_mock = AsyncMock()
    work_mock = AsyncMock()
    ritual_mock = AsyncMock()

    with patch("arcana.handlers.base.ask_claude",
               AsyncMock(return_value="ritual_done")), \
         patch("arcana.handlers.intent_resolve.ask_ritual_disambiguation",
               disambig_mock), \
         patch("arcana.handlers.works.handle_add_work", work_mock), \
         patch("arcana.handlers.rituals.handle_add_ritual", ritual_mock):
        await base.route_message(msg, user_notion_id="u")

    disambig_mock.assert_awaited_once()
    work_mock.assert_not_awaited()
    ritual_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_ritual_done_with_past_tense_still_routes_to_ritual(common_patches):
    """ritual_done + прошедшее время → guard не срабатывает → handle_add_ritual."""
    from arcana.handlers import base

    msg = _msg("провела ритуал чистка")
    ritual_mock = AsyncMock()
    work_mock = AsyncMock()

    with patch("arcana.handlers.base.ask_claude",
               AsyncMock(return_value="ritual_done")), \
         patch("arcana.handlers.rituals.handle_add_ritual", ritual_mock), \
         patch("arcana.handlers.works.handle_add_work", work_mock):
        await base.route_message(msg, user_notion_id="u")

    ritual_mock.assert_awaited_once()
    work_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_ritual_planned_still_routes_to_works(common_patches):
    """Явный ritual_planned от Haiku → guard не трогает → handle_add_work (Works)."""
    from arcana.handlers import base

    msg = _msg("сделать ритуал X")
    work_mock = AsyncMock()
    ritual_mock = AsyncMock()

    with patch("arcana.handlers.base.ask_claude",
               AsyncMock(return_value="ritual_planned")), \
         patch("arcana.handlers.works.handle_add_work", work_mock), \
         patch("arcana.handlers.rituals.handle_add_ritual", ritual_mock):
        await base.route_message(msg, user_notion_id="u")

    work_mock.assert_awaited_once()
    ritual_mock.assert_not_awaited()
