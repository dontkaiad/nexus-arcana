"""tests/test_arcana_mode_a_authored.py — режим A: сохранение АВТОРСКОЙ трактовки.

Аркана-CRM в двух режимах:
- (A) Кай надиктовала свою трактовку → её ПРИЧЁСЫВАЕМ (PERSONAL_INTERP_SYSTEM),
  не сочиняем; авторский текст идёт в interpretation → саммари/RAG;
- (B) трактовки в голосе нет → генерим по картам (TAROT_SYSTEM, как раньше).

Проверяем: парс-схема и промпт причёсывания, развилку A/B (single+multi),
что машинный блок дна НЕ загрязняет авторский текст, и что в саммари/RAG в
режиме A уходит авторский (а не машинный) текст.
"""
from __future__ import annotations

import json
from contextlib import ExitStack
from datetime import timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arcana.handlers import sessions
from arcana.handlers.sessions import (
    PARSE_SESSION_SYSTEM,
    PERSONAL_INTERP_SYSTEM,
    _polish_authored_interpretation,
    _save_and_post_triplet,
    handle_add_session,
    _handle_multi_session,
)

TZ = timezone(timedelta(hours=3))


# ───────────────────────── 1. Парс-схема + промпт ──────────────────────────

def test_parse_schema_has_interpretation_field():
    """Схема извлечения тянет авторскую трактовку (single + multi-триплет)."""
    assert '"interpretation"' in PARSE_SESSION_SYSTEM
    # критерий различения «только карты» vs «карты + трактовка»
    assert "ТОЛЬКО КАРТЫ" in PARSE_SESSION_SYSTEM
    assert "interpretation = null" in PARSE_SESSION_SYSTEM
    assert "ДОСЛОВНО" in PARSE_SESSION_SYSTEM
    # few-shot обоих случаев присутствует
    assert "король кубков тёплый но закрыт" in PARSE_SESSION_SYSTEM  # пример с текстом
    assert "у КАЖДОГО триплета" in PARSE_SESSION_SYSTEM  # multi — своя трактовка


def test_personal_interp_prompt_polishes_not_invents():
    """PERSONAL_INTERP_SYSTEM: причесать, не сочинять, тот же HTML-формат."""
    p = PERSONAL_INTERP_SYSTEM
    assert "ПРИЧЕСАТЬ" in p
    assert "НЕ сочиня" in p or "не сочиня" in p.lower()
    assert "справочник" in p.lower()  # запрет подмены значениями из справочника
    # выход — те же теги, что у TAROT_SYSTEM (sanitize/html_to_telegram не трогаем)
    for tag in ("<h3>", "<b>", "<i>", "<p>"):
        assert tag in p


# ───────────────────── 2. _polish_authored_interpretation ───────────────────

@pytest.mark.asyncio
async def test_polish_uses_personal_system_and_keeps_author_text():
    with patch.object(sessions, "ask_claude",
                      AsyncMock(return_value="<p>причёсано</p>")) as ask:
        out = await _polish_authored_interpretation(
            "король тёплый, туз режет", "король кубков, туз мечей", "двойка", "что чувствует"
        )
    assert out == "<p>причёсано</p>"
    # system == режим A, не TAROT
    assert ask.await_args.kwargs["system"] == PERSONAL_INTERP_SYSTEM
    # сырой авторский текст передан в промпт (причёсываем именно его)
    assert "король тёплый, туз режет" in ask.await_args.args[0]
    # Sonnet (CLAUDE.md: трактовки sessions.py — Sonnet)
    assert ask.await_args.kwargs["model"] == sessions._cfg.model_sonnet


@pytest.mark.asyncio
async def test_polish_empty_returns_blank_no_llm():
    with patch.object(sessions, "ask_claude", AsyncMock()) as ask:
        out = await _polish_authored_interpretation("", "карты", "", "вопрос")
    assert out == ""
    ask.assert_not_awaited()


# ─────────────── 3. _save_and_post_triplet: блок дна + саммари/RAG ───────────

def _save_post_patches(summary_mock, rag_mock):
    repo = MagicMock()
    repo.add = AsyncMock(return_value="pg-1")
    return [
        patch.object(sessions, "_repo", repo),
        patch.object(sessions, "_canon_card", lambda c, d: c),
        patch.object(sessions, "_canon_cards_str", lambda c, d: c),
        patch.object(sessions, "_make_triplet_summary", summary_mock),
        patch.object(sessions, "_rag_index_safe", rag_mock),
        patch("core.message_pages.save_message_page", AsyncMock()),
    ]


@pytest.mark.asyncio
async def test_mode_a_does_not_append_machine_bottom_block():
    """Режим A (authored=True): машинный «Скрытый фон расклада» НЕ дописывается
    к авторскому тексту; в саммари и RAG уходит чистый авторский текст."""
    summary_mock = AsyncMock(return_value="саммари")
    rag_mock = AsyncMock()
    msg = MagicMock()
    msg.answer = AsyncMock(return_value=MagicMock(chat=MagicMock(id=1), message_id=2))

    with ExitStack() as st:
        for p in _save_post_patches(summary_mock, rag_mock):
            st.enter_context(p)
        await _save_and_post_triplet(
            msg, tz=TZ, user_notion_id="u", client_id=None, client_name=None,
            deck="Уэйт", spread_type="🔺 Триплет", question="что чувствует",
            cards_text="король кубков, туз мечей, шут", bottom_card="двойка кубков",
            area="Отношения", interpretation="<p>авторский текст Кай</p>",
            authored=True,
        )

    # саммари считается по interpretation ДО sanitize → проверяем переданный текст
    summ_interp = summary_mock.await_args.args[3]
    assert "авторский текст Кай" in summ_interp
    assert "Скрытый фон расклада" not in summ_interp
    # в RAG — авторский, без машинного блока дна
    rag_interp = rag_mock.await_args.kwargs["interpretation"]
    assert "авторский текст Кай" in rag_interp
    assert "Скрытый фон расклада" not in rag_interp


@pytest.mark.asyncio
async def test_mode_b_still_appends_machine_bottom_block():
    """Режим B (authored=False) — старое поведение: блок дна дописывается."""
    summary_mock = AsyncMock(return_value="саммари")
    rag_mock = AsyncMock()
    msg = MagicMock()
    msg.answer = AsyncMock(return_value=MagicMock(chat=MagicMock(id=1), message_id=2))

    with ExitStack() as st:
        for p in _save_post_patches(summary_mock, rag_mock):
            st.enter_context(p)
        await _save_and_post_triplet(
            msg, tz=TZ, user_notion_id="u", client_id=None, client_name=None,
            deck="Уэйт", spread_type="🔺 Триплет", question="что чувствует",
            cards_text="король кубков, туз мечей, шут", bottom_card="двойка кубков",
            area="Отношения", interpretation="<h3>Общий смысл</h3><p>сгенерено</p>",
            authored=False,
        )

    rag_interp = rag_mock.await_args.kwargs["interpretation"]
    assert "Скрытый фон расклада" in rag_interp


# ───────────────── 4. Развилка A/B: single + multi флоу ─────────────────────

def _make_fake_ask(parse_json):
    """ask_claude-мок, различающий вызовы по system-промпту."""
    cap = {"systems": [], "personal_prompt": None, "tarot_prompt": None}

    async def fake_ask(prompt, system=None, **kw):
        sys = system or ""
        cap["systems"].append(sys)
        if "Извлеки данные о сеансе" in sys:
            return json.dumps(parse_json)
        if "ПРИЧЕСАТЬ её речь" in sys:           # PERSONAL_INTERP_SYSTEM (режим A)
            cap["personal_prompt"] = prompt
            return "<p>POLISHED Kai</p>"
        if "Трактуй строго по справочнику" in sys:  # TAROT_SYSTEM (режим B)
            cap["tarot_prompt"] = prompt
            return "<h3>Общий смысл</h3><p>MACHINE</p>"
        if "Output as plain Russian" in sys:     # TRIPLET_SUMMARY_SYSTEM
            return "summary"
        return "сводка сессии"                    # финальное саммари (system=None)

    return fake_ask, cap


def _common_handler_patches(fake_ask, rag_safe=None, rag_batch=None):
    repo = MagicMock()
    repo.add = AsyncMock(return_value="pg-1")
    repo.prev_for_client = AsyncMock(return_value=[])
    repo.session_group_exists = AsyncMock(return_value=False)
    repo.set_photo_url = AsyncMock(return_value=True)
    repo.set_session_summary = AsyncMock(return_value=True)
    repo.clear_theme_summary = AsyncMock(return_value=0)
    return [
        patch.object(sessions, "ask_claude", side_effect=fake_ask),
        patch.object(sessions, "get_user_tz", AsyncMock(return_value=3)),
        patch.object(sessions, "_repo", repo),
        patch.object(sessions, "_rag_voice_block", AsyncMock(return_value="")),
        patch.object(sessions, "_rag_index_safe", rag_safe or AsyncMock()),
        patch.object(sessions, "_rag_index_batch_safe", rag_batch or AsyncMock()),
        patch.object(sessions, "_upload_spread_photo", AsyncMock(return_value="")),
        patch("arcana.tarot_loader.get_cards_context", MagicMock(return_value="")),
        patch("arcana.tarot_loader.missing_cards", MagicMock(return_value=[])),
        patch("core.memory.get_memories_for_context", AsyncMock(return_value="")),
        patch("core.memory.extract_context_keywords", MagicMock(return_value=[])),
        patch("core.message_pages.save_message_page", AsyncMock()),
        patch("core.client_resolve.resolve_self_client", AsyncMock(return_value="client-1")),
        patch("core.client_resolve.client_get_type", AsyncMock(return_value=None)),
        patch("core.client_resolve.should_skip_payment", MagicMock(return_value=True)),
        patch("core.work_relation.find_active_work_for_client", AsyncMock(return_value=None)),
    ]


def _msg():
    m = MagicMock()
    m.from_user = MagicMock()
    m.from_user.id = 12345
    m.text = "голосовой расклад"
    m.answer = AsyncMock(return_value=MagicMock(chat=MagicMock(id=1), message_id=2))
    return m


@pytest.mark.asyncio
async def test_single_mode_a_routes_to_personal_and_rag_gets_author():
    """Single: парсер вернул авторскую трактовку → PERSONAL, не TAROT;
    в RAG уходит причёсанный авторский текст."""
    rag = AsyncMock()
    fake_ask, cap = _make_fake_ask({
        "client_name": None, "spread_type": "триплет", "question": "что чувствует",
        "cards": ["король кубков", "туз мечей", "шут"], "bottom_card": "двойка кубков",
        "area": "Отношения", "deck": "Уэйт", "amount": 0, "paid": 0,
        "payment_source": None,
        "interpretation": "король тёплый но закрыт, туз режет иллюзии",
    })
    with ExitStack() as st:
        for p in _common_handler_patches(fake_ask, rag_safe=rag):
            st.enter_context(p)
        await handle_add_session(_msg(), "голос", user_notion_id="u")

    assert any("ПРИЧЕСАТЬ её речь" in s for s in cap["systems"]), "режим A не сработал"
    assert not any("Трактуй строго по справочнику" in s for s in cap["systems"]), \
        "в режиме A не должно быть генерации TAROT"
    assert "король тёплый но закрыт" in cap["personal_prompt"]  # причёсываем авторский
    assert "POLISHED Kai" in rag.await_args.kwargs["interpretation"]


@pytest.mark.asyncio
async def test_single_mode_b_routes_to_tarot_when_no_authored():
    """Single: трактовки в голосе нет (interpretation=null) → TAROT, не PERSONAL."""
    rag = AsyncMock()
    fake_ask, cap = _make_fake_ask({
        "client_name": None, "spread_type": "триплет", "question": "что чувствует",
        "cards": ["король кубков", "туз мечей", "шут"], "bottom_card": None,
        "area": "Отношения", "deck": "Уэйт", "amount": 0, "paid": 0,
        "payment_source": None, "interpretation": None,
    })
    with ExitStack() as st:
        for p in _common_handler_patches(fake_ask, rag_safe=rag):
            st.enter_context(p)
        await handle_add_session(_msg(), "голос", user_notion_id="u")

    assert any("Трактуй строго по справочнику" in s for s in cap["systems"]), "режим B не сработал"
    assert not any("ПРИЧЕСАТЬ её речь" in s for s in cap["systems"]), \
        "без авторского текста не должно быть причёсывания"
    assert "MACHINE" in rag.await_args.kwargs["interpretation"]


@pytest.mark.asyncio
async def test_multi_mode_a_per_triplet():
    """Multi: триплет с авторской трактовкой → PERSONAL; без → TAROT.
    В RAG-батч уходят оба (авторский и машинный) — каждый из своего поля."""
    rag_batch = AsyncMock()
    fake_ask, cap = _make_fake_ask({})  # parse не вызывается — items передаём явно
    items = [
        {"question": "что чувствует", "cards": ["король кубков", "туз мечей", "шут"],
         "bottom_card": None, "area": "Отношения", "spread_type": "Триплет",
         "interpretation": "король тёплый, туз режет иллюзии"},
        {"question": "что думает", "cards": ["шут", "маг", "жрица"],
         "bottom_card": None, "area": "Отношения", "spread_type": "Триплет",
         "interpretation": None},
    ]
    data = {"session_name": None, "deck": "Уэйт", "session_category": "Сфера жизни"}

    with ExitStack() as st:
        for p in _common_handler_patches(fake_ask, rag_batch=rag_batch):
            st.enter_context(p)
        await _handle_multi_session(
            _msg(), data, items, TZ, 3.0, "u", forced_is_personal=True,
        )

    assert any("ПРИЧЕСАТЬ её речь" in s for s in cap["systems"]), "режим A (триплет 1) не сработал"
    assert any("Трактуй строго по справочнику" in s for s in cap["systems"]), "режим B (триплет 2) не сработал"
    # RAG-батч: триплет 1 — авторский (POLISHED), триплет 2 — машинный (MACHINE)
    batch = rag_batch.await_args.args[0]
    interps = [t["interpretation"] for t in batch]
    assert any("POLISHED Kai" in i for i in interps), "авторская трактовка не попала в RAG"
    assert any("MACHINE" in i for i in interps), "машинная трактовка триплета B не попала в RAG"
