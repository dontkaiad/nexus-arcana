"""tests/test_arcana_stage4_client_question.py — #154 стадия 4:
вопрос [🌟 Себе]/[👤 Для клиента] только при полной неоднозначности.
"""
import json
from contextlib import ExitStack

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── is_self_marked ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("txt", [
    "сделала приворот на Машу себе",
    "ритуал на защиту для себя",
    "мой ритуал на Петра",
    "личный ритуал очищения",
    "почистила себя",
])
def test_is_self_marked_positive(txt):
    from arcana.handlers.intent_resolve import is_self_marked
    assert is_self_marked(txt) is True


@pytest.mark.parametrize("txt", [
    "приворот на Машу",
    "ритуал защиты для Петра",
    "сделала чистку Оле",
])
def test_is_self_marked_negative(txt):
    from arcana.handlers.intent_resolve import is_self_marked
    assert is_self_marked(txt) is False


# ── handle_add_ritual: диалог self/клиент ────────────────────────────────────

def _msg(text: str):
    m = MagicMock()
    m.from_user.id = 42
    m.chat.id = 1
    m.text = text
    m.answer = AsyncMock()
    return m


_RIT_JSON = {
    "client_name": None, "subject_name": "Маша", "name": "Приворот",
    "goal": "приворот", "place": "дома", "consumables": "свечи",
    "consumables_cost": 0, "duration_min": 30, "offerings": "", "offerings_cost": 0,
    "forces": "стихии", "structure": "свечи, заговор", "notes": None,
    "amount": 0, "paid": 0, "payment_source": None, "needs_clarification": False,
}


def _rit_patches(json_data):
    st = ExitStack()
    st.enter_context(patch("arcana.handlers.rituals.ask_claude",
                           AsyncMock(return_value=json.dumps(json_data))))
    st.enter_context(patch("arcana.handlers.rituals.get_user_tz", AsyncMock(return_value=3)))
    st.enter_context(patch("arcana.pending_tarot.get_pending", AsyncMock(return_value=None)))
    st.enter_context(patch("arcana.pending_tarot.save_pending", AsyncMock()))
    st.enter_context(patch("arcana.pending_tarot.delete_pending", AsyncMock()))
    return st


@pytest.mark.asyncio
async def test_ritual_ambiguous_asks_self_or_client():
    import arcana.handlers.rituals as rit
    ask = AsyncMock()
    with _rit_patches(_RIT_JSON), \
         patch("arcana.handlers.intent_resolve.ask_ritual_self_or_client", ask), \
         patch.object(rit._repo, "create", AsyncMock()) as m_create:
        await rit.handle_add_ritual(_msg("приворот на Машу"), "приворот на Машу", "u")
    ask.assert_awaited_once()
    assert ask.await_args.args[3] == "Маша"
    m_create.assert_not_called()


@pytest.mark.asyncio
async def test_ritual_self_marker_skips_dialog_saves_personal():
    import arcana.handlers.rituals as rit
    ask = AsyncMock()
    with _rit_patches(_RIT_JSON), \
         patch("arcana.handlers.intent_resolve.ask_ritual_self_or_client", ask), \
         patch("core.client_resolve.resolve_self_client", AsyncMock(return_value="self-1")), \
         patch("core.work_relation.link_practice_record", AsyncMock(return_value=(None, False))), \
         patch("core.message_pages.save_message_page", AsyncMock()), \
         patch.object(rit._repo, "create",
                      AsyncMock(return_value=MagicMock(id="r1"))) as m_create:
        await rit.handle_add_ritual(
            _msg("приворот на Машу себе"), "приворот на Машу себе", "u",
        )
    ask.assert_not_awaited()
    m_create.assert_awaited_once()
    assert m_create.await_args.kwargs["client_id"] == "self-1"


@pytest.mark.asyncio
async def test_ritual_forced_self_skips_dialog():
    import arcana.handlers.rituals as rit
    ask = AsyncMock()
    with _rit_patches(_RIT_JSON), \
         patch("arcana.handlers.intent_resolve.ask_ritual_self_or_client", ask), \
         patch("core.client_resolve.resolve_self_client", AsyncMock(return_value="self-1")), \
         patch("core.work_relation.link_practice_record", AsyncMock(return_value=(None, False))), \
         patch("core.message_pages.save_message_page", AsyncMock()), \
         patch.object(rit._repo, "create",
                      AsyncMock(return_value=MagicMock(id="r1"))) as m_create:
        await rit.handle_add_ritual(
            _msg("приворот на Машу"), "приворот на Машу", "u", forced_self=True,
        )
    ask.assert_not_awaited()
    assert m_create.await_args.kwargs["client_id"] == "self-1"


@pytest.mark.asyncio
async def test_ritual_forced_client_name_resolves_client():
    import arcana.handlers.rituals as rit
    ask = AsyncMock()
    with _rit_patches(_RIT_JSON), \
         patch("arcana.handlers.intent_resolve.ask_ritual_self_or_client", ask), \
         patch("core.client_resolve.resolve_or_create", AsyncMock(return_value="c-olia")), \
         patch("core.client_resolve.is_valid_client_name", return_value=True), \
         patch("core.work_relation.link_practice_record", AsyncMock(return_value=("w1", True))), \
         patch("core.message_pages.save_message_page", AsyncMock()), \
         patch("core.client_resolve.client_get_type", AsyncMock(return_value="🤝 Платный")), \
         patch("core.client_resolve.should_skip_payment", return_value=False), \
         patch.object(rit._repo, "create",
                      AsyncMock(return_value=MagicMock(id="r1"))) as m_create:
        await rit.handle_add_ritual(
            _msg("приворот на Машу"), "приворот на Машу", "u",
            forced_client_name="Оля",
        )
    ask.assert_not_awaited()
    assert m_create.await_args.kwargs["client_id"] == "c-olia"


@pytest.mark.asyncio
async def test_multi_session_person_subject_shows_resolve_dialog():
    """#154 стадия 4: subject_name = человек, клиент не найден, нет self-
    маркера → диалог self/клиент (а не тихий self)."""
    import arcana.handlers.sessions as sess
    from datetime import timezone, timedelta

    data = {"session_name": "Пётр", "subject_name": "Пётр", "client_name": None,
            "deck": "Уэйт", "session_category": "Отношения"}
    items = [{"question": "что чувствует Пётр", "cards": ["a", "b", "c"]},
             {"question": "что думает", "cards": ["d", "e", "f"]}]
    msg = _msg("что чувствует Пётр\nчто думает")
    save_pending = AsyncMock()

    with patch.object(sess._client_repo, "find", AsyncMock(return_value=None)), \
         patch("arcana.pending_tarot.save_pending", save_pending), \
         patch("core.client_resolve.is_valid_client_name", return_value=True):
        await sess._handle_multi_session(
            msg, data, items, timezone(timedelta(hours=3)), 3.0, "u",
        )

    assert save_pending.await_args.args[1]["type"] == "client_resolve_pending"
    txt = msg.answer.await_args.args[0]
    assert "Пётр" in txt


@pytest.mark.asyncio
async def test_multi_session_self_marker_no_dialog():
    """Есть self-маркер в тексте → диалог не показываем, идём в self."""
    import arcana.handlers.sessions as sess
    from datetime import timezone, timedelta

    data = {"session_name": "Пётр", "subject_name": "Пётр", "client_name": None,
            "deck": "Уэйт", "session_category": "Отношения"}
    items = [{"question": "что чувствует Пётр ко мне", "cards": ["a"]},
             {"question": "наши перспективы", "cards": ["b"]}]
    msg = _msg("что чувствует Пётр ко мне, наши перспективы — это мне расклад")
    save_pending = AsyncMock()

    with patch.object(sess._client_repo, "find", AsyncMock(return_value=None)), \
         patch("arcana.pending_tarot.save_pending", save_pending), \
         patch("core.client_resolve.resolve_self_client", AsyncMock(return_value="self-1")), \
         patch("core.client_resolve.is_valid_client_name", return_value=True), \
         patch.object(sess, "_resolve_category", AsyncMock(side_effect=RuntimeError("stop"))):
        with pytest.raises(Exception):
            await sess._handle_multi_session(
                msg, data, items, timezone(timedelta(hours=3)), 3.0, "u",
            )

    # до диалога дело не дошло — client_resolve_pending не сохранён
    assert not any(
        c.args and isinstance(c.args[1], dict)
        and c.args[1].get("type") == "client_resolve_pending"
        for c in save_pending.await_args_list
    )


@pytest.mark.asyncio
async def test_ritual_no_subject_saves_personal_no_dialog():
    """Тема без человека-цели → личный ритуал, ничего не спрашиваем."""
    import arcana.handlers.rituals as rit
    j = dict(_RIT_JSON, subject_name=None, name="Чистка дома", goal="очищение")
    ask = AsyncMock()
    with _rit_patches(j), \
         patch("arcana.handlers.intent_resolve.ask_ritual_self_or_client", ask), \
         patch("core.client_resolve.resolve_self_client", AsyncMock(return_value="self-1")), \
         patch("core.work_relation.link_practice_record", AsyncMock(return_value=(None, False))), \
         patch("core.message_pages.save_message_page", AsyncMock()), \
         patch.object(rit._repo, "create",
                      AsyncMock(return_value=MagicMock(id="r1"))) as m_create:
        await rit.handle_add_ritual(_msg("почистила дом"), "почистила дом", "u")
    ask.assert_not_awaited()
    assert m_create.await_args.kwargs["client_id"] == "self-1"
