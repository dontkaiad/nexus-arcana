"""tests/test_arcana_area_hint.py — area-классификатор с подсказкой темы (#190).

ADR-0018 design intent: area = тема КОНКРЕТНОГО вопроса, не наследуется
жёстко (в отличие от category_id, у которого есть client anchor). Проблема:
расплывчатые вопросы без явного триггера молча падали в дефолт "Общая
ситуация" без сигнала неуверенности.

Фикс — контекстная ПОДСКАЗКА, не анкер:
- основной парсер теперь может вернуть area=null, когда в тексте вопроса нет
  явного триггера (раньше форсировал "Общая ситуация");
- если у сессии уже есть подтверждённый subject_id (тема известна) — null
  area переклассифицируется отдельным дешёвым Haiku-вызовом с подсказкой
  (последние 2-3 area темы), но явные слова в самом вопросе всё ещё
  приоритетнее — сюда попадаем только когда их не было;
- нет subject_id ИЛИ у темы нет истории area → старое поведение без
  изменений: дефолт "Общая ситуация", ни одного лишнего LLM-вызова.
"""
from __future__ import annotations

import json
from contextlib import ExitStack
from datetime import timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arcana.handlers import sessions
from arcana.handlers.sessions import (
    AREA_DEFAULT,
    AREA_HINT_SYSTEM,
    _reclassify_area_with_subject_hint,
    _handle_multi_session,
)

TZ = timezone(timedelta(hours=3))


# ───────────────────── 1. _reclassify_area_with_subject_hint ───────────────

@pytest.mark.asyncio
async def test_no_history_returns_default_without_llm_call():
    """Нет истории area у темы → дефолт, БЕЗ вызова Haiku (деньги Кай)."""
    ask = AsyncMock()
    repo = MagicMock()
    repo.recent_areas_for_subject = AsyncMock(return_value=[])
    with patch.object(sessions, "_repo", repo), \
         patch.object(sessions, "ask_claude", ask):
        area = await _reclassify_area_with_subject_hint("диагностика", 42)

    assert area == AREA_DEFAULT
    ask.assert_not_awaited()


@pytest.mark.asyncio
async def test_history_present_passes_hint_and_uses_result():
    repo = MagicMock()
    repo.recent_areas_for_subject = AsyncMock(return_value=["Отношения", "Здоровье"])
    ask = AsyncMock(return_value=json.dumps({"area": "Отношения"}))
    with patch.object(sessions, "_repo", repo), \
         patch.object(sessions, "ask_claude", ask):
        area = await _reclassify_area_with_subject_hint("диагностика ситуации", 42)

    assert area == "Отношения"
    ask.assert_awaited_once()
    kwargs = ask.await_args
    assert kwargs.kwargs.get("system") == AREA_HINT_SYSTEM
    user_prompt = kwargs.args[0]
    assert "диагностика ситуации" in user_prompt
    assert "Отношения" in user_prompt and "Здоровье" in user_prompt


@pytest.mark.asyncio
async def test_llm_failure_falls_back_to_default():
    repo = MagicMock()
    repo.recent_areas_for_subject = AsyncMock(return_value=["Отношения"])
    ask = AsyncMock(side_effect=RuntimeError("boom"))
    with patch.object(sessions, "_repo", repo), \
         patch.object(sessions, "ask_claude", ask):
        area = await _reclassify_area_with_subject_hint("диагностика", 42)

    assert area == AREA_DEFAULT


@pytest.mark.asyncio
async def test_llm_returns_null_falls_back_to_default():
    repo = MagicMock()
    repo.recent_areas_for_subject = AsyncMock(return_value=["Отношения"])
    ask = AsyncMock(return_value=json.dumps({"area": None}))
    with patch.object(sessions, "_repo", repo), \
         patch.object(sessions, "ask_claude", ask):
        area = await _reclassify_area_with_subject_hint("что-то неясное", 42)

    assert area == AREA_DEFAULT


# ───────────────────── 2. _handle_multi_session wiring ─────────────────────

def _fake_ask_for_multi(hint_area="Отношения"):
    async def fake_ask(prompt, system=None, **kw):
        sys = system or ""
        if sys == AREA_HINT_SYSTEM:
            return json.dumps({"area": hint_area})
        if "Output as plain Russian" in sys:
            return "summary"
        if "Трактуй строго по справочнику" in sys:
            return "<h3>Общий смысл</h3><p>MACHINE</p>"
        return "сводка сессии"
    return fake_ask


def _multi_repo(*, known_subject_id=None, recent_areas=None):
    repo = MagicMock()
    repo.add = AsyncMock(return_value="pg-1")
    repo.prev_for_client = AsyncMock(return_value=[])
    repo.session_group_exists = AsyncMock(return_value=False)
    repo.group_subject_id = AsyncMock(return_value=known_subject_id)
    repo.recent_areas_for_subject = AsyncMock(return_value=recent_areas or [])
    repo.set_photo_url = AsyncMock(return_value=True)
    repo.set_session_summary = AsyncMock(return_value=True)
    repo.clear_theme_summary = AsyncMock(return_value=0)
    repo.get_mode_category_for_client = AsyncMock(return_value=(None, None))
    repo.resolve_category_code = AsyncMock(return_value=None)
    return repo


def _patches(repo, fake_ask):
    return [
        patch.object(sessions, "ask_claude", side_effect=fake_ask),
        patch.object(sessions, "get_user_tz", AsyncMock(return_value=3)),
        patch.object(sessions, "_repo", repo),
        patch.object(sessions, "_rag_index_batch_safe", AsyncMock()),
        patch.object(sessions, "_upload_spread_photo", AsyncMock(return_value="")),
        patch("arcana.tarot_loader.get_cards_context", MagicMock(return_value="")),
        patch("arcana.tarot_loader.missing_cards", MagicMock(return_value=[])),
        patch("core.message_pages.save_message_page", AsyncMock()),
    ]


def _msg():
    m = MagicMock()
    m.from_user = MagicMock()
    m.from_user.id = 12345
    m.text = "голосовой расклад"
    m.answer = AsyncMock(return_value=MagicMock(chat=MagicMock(id=1), message_id=2))
    return m


@pytest.mark.asyncio
async def test_null_area_with_known_subject_uses_hint():
    """Вопрос без явного триггера ('диагностика') + тема с историей area
    ('Отношения') → area переклассифицируется подсказкой, а не падает в дефолт."""
    repo = _multi_repo(known_subject_id=42, recent_areas=["Отношения"])
    items = [{
        "question": "диагностика ситуации", "cards": ["шут", "маг", "жрица"],
        "bottom_card": None, "area": None, "spread_type": "Триплет",
        "interpretation": None,
    }]
    data = {"session_name": "Вадим — диагностика", "deck": "Уэйт"}

    with ExitStack() as st:
        for p in _patches(repo, _fake_ask_for_multi("Отношения")):
            st.enter_context(p)
        await _handle_multi_session(
            _msg(), data, items, TZ, 3.0, "u",
            forced_client_id=None, forced_is_personal=True,
        )

    repo.recent_areas_for_subject.assert_awaited_once_with(42, limit=3)
    _, kwargs = repo.add.await_args
    assert kwargs["area"] == "Отношения"


@pytest.mark.asyncio
async def test_explicit_area_wins_over_hint_even_with_known_subject():
    """В вопросе явный триггер ('Финансы') → подсказка НЕ используется, даже
    если у темы есть история про другое."""
    repo = _multi_repo(known_subject_id=42, recent_areas=["Отношения"])
    items = [{
        "question": "хватит ли денег", "cards": ["шут", "маг", "жрица"],
        "bottom_card": None, "area": "Финансы", "spread_type": "Триплет",
        "interpretation": None,
    }]
    data = {"session_name": "Вадим — финансы", "deck": "Уэйт"}

    with ExitStack() as st:
        for p in _patches(repo, _fake_ask_for_multi("Отношения")):
            st.enter_context(p)
        await _handle_multi_session(
            _msg(), data, items, TZ, 3.0, "u",
            forced_client_id=None, forced_is_personal=True,
        )

    repo.recent_areas_for_subject.assert_not_awaited()
    _, kwargs = repo.add.await_args
    assert kwargs["area"] == "Финансы"


@pytest.mark.asyncio
async def test_no_known_subject_keeps_old_default_behavior():
    """subject_id ещё не подтверждён (первая отправка темы) → area=null падает
    в старый дефолт "Общая ситуация", без единого лишнего LLM-вызова."""
    repo = _multi_repo(known_subject_id=None)
    items = [{
        "question": "диагностика ситуации", "cards": ["шут", "маг", "жрица"],
        "bottom_card": None, "area": None, "spread_type": "Триплет",
        "interpretation": None,
    }]
    data = {"session_name": "Вадим — диагностика", "deck": "Уэйт"}

    with ExitStack() as st:
        for p in _patches(repo, _fake_ask_for_multi("Отношения")):
            st.enter_context(p)
        await _handle_multi_session(
            _msg(), data, items, TZ, 3.0, "u",
            forced_client_id=None, forced_is_personal=True,
        )

    repo.recent_areas_for_subject.assert_not_awaited()
    _, kwargs = repo.add.await_args
    assert kwargs["area"] == AREA_DEFAULT
