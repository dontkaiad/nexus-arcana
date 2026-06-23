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
    # императив (не сравнительное «лучше…чем»)
    assert "ЗАПРЕЩЕНО ВЫДУМЫВАТЬ КАРТУ" in p
    assert "ОБЯЗАН вернуть его ДОСЛОВНО" in p
    assert "НЕ угадывай ранг" in p
    assert "null" in p
    # few-shot дословного fallback (recon: Haiku имитирует примеры)
    assert "крыльева мячей" in p, "нет few-shot искажённой карты → дословно"
    assert "Король Жезлов" in p, "нет контрпримера подмены, который запрещаем"


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


# ─────── NEW FIX 1: транскрипт в ops-группу мониторинга ПЕРЕД парсингом ──────

def test_handle_voice_logs_transcript_to_group_before_parse():
    """handle_voice (nested, не импортируется) — source-guard: сырой транскрипт
    уходит в ops-группу (notify_log_group) ДО нормализации и парсинга."""
    src = (REPO / "arcana" / "bot.py").read_text(encoding="utf-8")
    start = src.index("async def handle_voice")
    end = src.index("async def handle_photo", start)
    body = src[start:end]
    assert "notify_log_group(" in body, "транскрипт не логируется в ops-группу"
    assert "config.log_thread_arcana" in body, "не в топик Arcana"
    # порядок ВЫЗОВОВ: лог транскрипта → нормализация → парсинг
    i_log = body.index("notify_log_group(")
    assert i_log < body.index("normalize_text(text"), "лог должен быть до нормализации (сырой)"
    assert i_log < body.index("route_message(msg"), "лог должен быть ДО парсинга"


# ─────── NEW FIX 2: «клиент я» → self (null), не залипший «Оля» ──────────────

def test_parse_prompt_has_self_client_rule_and_no_personal_names():
    import re
    from arcana.handlers.sessions import PARSE_SESSION_SYSTEM as p
    # явное self-правило
    assert "client_name = null" in p
    for marker in ("клиент я", "себе", "на себя", "для себя", "личный"):
        assert marker in p, f"нет self-маркера {marker!r}"
    # субъект вопроса ≠ клиент
    assert "субъект вопроса" in p
    assert "ДАЖЕ ЕСЛИ в вопросах фигурируют имена" in p
    # примеры помечены как плейсхолдеры
    assert "ПЛЕЙСХОЛДЕРЫ" in p
    # залипающие личные имена убраны из промпта
    assert not re.search(r"Вадим|вадим|Маша|Маше|Машей|Оля|оля", p), \
        "в промпте остались личные имена — модель будет их тянуть в вывод"


def _session_patches(fake_ask, self_spy, create_spy):
    """Минимальный харнесс handle_add_session: обрываем на _save_and_post_triplet."""
    from arcana.handlers import sessions
    repo = MagicMock()
    repo.add = AsyncMock(return_value="pg-1")
    repo.prev_for_client = AsyncMock(return_value=[])
    return [
        patch.object(sessions, "ask_claude", side_effect=fake_ask),
        patch.object(sessions, "get_user_tz", AsyncMock(return_value=3)),
        patch.object(sessions, "_repo", repo),
        patch.object(sessions, "_rag_voice_block", AsyncMock(return_value="")),
        patch.object(sessions, "_rag_index_safe", AsyncMock()),
        patch.object(sessions, "_upload_spread_photo", AsyncMock(return_value="")),
        patch.object(sessions, "_save_and_post_triplet", AsyncMock(return_value="pg-1")),
        patch("arcana.tarot_loader.get_cards_context", MagicMock(return_value="")),
        patch("arcana.tarot_loader.missing_cards", MagicMock(return_value=[])),
        patch("core.memory.get_memories_for_context", AsyncMock(return_value="")),
        patch("core.memory.extract_context_keywords", MagicMock(return_value=[])),
        patch("core.client_resolve.resolve_self_client", self_spy),
        patch("core.client_resolve.resolve_or_create", create_spy),
        patch("core.client_resolve.is_valid_client_name", MagicMock(return_value=True)),
    ]


def _parse_fake(parse_json):
    async def fake_ask(prompt, system=None, **kw):
        if "Извлеки данные о сеансе" in (system or ""):
            import json
            return json.dumps(parse_json)
        return "<p>трактовка</p>"  # генерация
    return fake_ask


@pytest.mark.asyncio
async def test_client_ya_routes_to_self_not_named():
    """Парсер вернул client_name=null («клиент я») → self-клиент, без создания
    именованного клиента."""
    from arcana.handlers.sessions import handle_add_session
    import contextlib
    self_spy = AsyncMock(return_value="self-1")
    create_spy = AsyncMock(return_value="named-1")
    fake = _parse_fake({
        "client_name": None, "spread_type": "триплет", "question": "что меня ждёт",
        "cards": ["шут", "маг", "жрица"], "bottom_card": None, "area": "Общая ситуация",
        "deck": "Уэйт", "amount": 0, "paid": 0, "payment_source": None,
        "interpretation": None,
    })
    with contextlib.ExitStack() as st:
        for p in _session_patches(fake, self_spy, create_spy):
            st.enter_context(p)
        await handle_add_session(_msg(text="клиент я, что меня ждёт — шут маг жрица"),
                                 "клиент я", user_notion_id="u")
    self_spy.assert_awaited()
    create_spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_name_in_question_but_client_ya_still_self():
    """Имя в ВОПРОСЕ + client_name=null → self (имя из вопроса не цепляется как клиент)."""
    from arcana.handlers.sessions import handle_add_session
    import contextlib
    self_spy = AsyncMock(return_value="self-1")
    create_spy = AsyncMock(return_value="named-1")
    fake = _parse_fake({
        "client_name": None, "spread_type": "триплет",
        "question": "что чувствует Артём",  # имя — субъект вопроса, НЕ клиент
        "cards": ["король кубков", "туз мечей", "шут"], "bottom_card": None,
        "area": "Отношения", "deck": "Уэйт", "amount": 0, "paid": 0,
        "payment_source": None, "interpretation": None,
    })
    with contextlib.ExitStack() as st:
        for p in _session_patches(fake, self_spy, create_spy):
            st.enter_context(p)
        await handle_add_session(_msg(text="себе, что чувствует Артём — ..."),
                                 "себе, что чувствует Артём", user_notion_id="u")
    self_spy.assert_awaited()
    create_spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_named_client_routes_to_resolve_or_create():
    """Явный заказчик (client_name задан) → resolve_or_create, не self."""
    from arcana.handlers.sessions import handle_add_session
    import contextlib
    self_spy = AsyncMock(return_value="self-1")
    create_spy = AsyncMock(return_value="named-1")
    fake = _parse_fake({
        "client_name": "Клиент-Тест", "spread_type": "триплет", "question": "вопрос",
        "cards": ["шут", "маг", "жрица"], "bottom_card": None, "area": "Общая ситуация",
        "deck": "Уэйт", "amount": 0, "paid": 0, "payment_source": None,
        "interpretation": None,
    })
    with contextlib.ExitStack() as st:
        for p in _session_patches(fake, self_spy, create_spy):
            st.enter_context(p)
        await handle_add_session(_msg(text="клиентка Клиент-Тест ..."),
                                 "клиентка Клиент-Тест", user_notion_id="u")
    create_spy.assert_awaited()
    self_spy.assert_not_awaited()
