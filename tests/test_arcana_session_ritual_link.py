"""tests/test_arcana_session_ritual_link.py — #84: расклад-просмотр ↔ ритуал.

Юнит-тесты на обвязку (мокнутые репозитории), без реальной БД.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── _link_session_to_ritual ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_link_session_to_ritual_sets_ritual_id_on_all_pages():
    import arcana.handlers.sessions as sess

    rit = MagicMock()
    rit.id = 42
    rit.name = "Приворот на Олю"
    fake_rituals = MagicMock()
    fake_rituals.find_recent_for_client = AsyncMock(return_value=rit)
    set_rid = AsyncMock(return_value=True)

    with patch("arcana.repos.pg_rituals_repo.PgRitualsRepo",
               MagicMock(return_value=fake_rituals)), \
         patch.object(sess._repo, "set_ritual_id", set_rid):
        title = await sess._link_session_to_ritual(["p1", "p2"], "c-olia", "u")

    assert title == "Приворот на Олю"
    assert {c.args[1] for c in set_rid.await_args_list} == {"42"}
    assert {c.args[0] for c in set_rid.await_args_list} == {"p1", "p2"}


@pytest.mark.asyncio
async def test_link_session_to_ritual_no_client_noop():
    import arcana.handlers.sessions as sess
    assert await sess._link_session_to_ritual(["p1"], None, "u") is None


@pytest.mark.asyncio
async def test_link_session_to_ritual_no_recent_ritual():
    import arcana.handlers.sessions as sess
    fake_rituals = MagicMock()
    fake_rituals.find_recent_for_client = AsyncMock(return_value=None)
    with patch("arcana.repos.pg_rituals_repo.PgRitualsRepo",
               MagicMock(return_value=fake_rituals)):
        assert await sess._link_session_to_ritual(["p1"], "c1", "u") is None


# ── handle_add_session (single) прокидывает link_ritual ───────────────────────

_SINGLE_JSON = json.dumps({
    "client_name": "оля",
    "after_ritual": True,
    "spread_type": "Триплет",
    "question": "как лёг приворот",
    "cards": ["шут", "маг", "жрица"],
    "bottom_card": None,
    "area": "Общая ситуация",
    "amount": 0, "paid": 0, "payment_source": None, "deck": "Уэйт",
    "interpretation": None,
})


def _session_msg():
    bot_msg = MagicMock()
    bot_msg.chat.id = 100
    bot_msg.message_id = 555
    msg = MagicMock()
    msg.from_user.id = 7
    msg.answer = AsyncMock(return_value=bot_msg)
    return msg


@pytest.mark.asyncio
async def test_handle_add_session_single_passes_link_ritual_true():
    import arcana.handlers.sessions as sess
    import core.client_resolve as cr

    saver = AsyncMock(return_value="page-1")
    with patch("arcana.handlers.sessions.ask_claude",
               AsyncMock(side_effect=[_SINGLE_JSON, "<p>ok</p>"])), \
         patch("arcana.handlers.sessions.get_user_tz", AsyncMock(return_value=3)), \
         patch("core.memory.get_figurant_facts", AsyncMock(return_value=[])), \
         patch.object(cr, "resolve_or_create", AsyncMock(return_value="c-olia")), \
         patch("arcana.handlers.sessions._save_and_post_triplet", saver):
        await sess.handle_add_session(_session_msg(), "оля как лёг приворот", user_id="u")

    assert saver.await_args.kwargs["link_ritual"] is True


@pytest.mark.asyncio
async def test_handle_add_session_single_link_ritual_false_by_default():
    import arcana.handlers.sessions as sess
    import core.client_resolve as cr

    j = json.loads(_SINGLE_JSON)
    j["after_ritual"] = False
    saver = AsyncMock(return_value="page-1")
    with patch("arcana.handlers.sessions.ask_claude",
               AsyncMock(side_effect=[json.dumps(j), "<p>ok</p>"])), \
         patch("arcana.handlers.sessions.get_user_tz", AsyncMock(return_value=3)), \
         patch("core.memory.get_figurant_facts", AsyncMock(return_value=[])), \
         patch.object(cr, "resolve_or_create", AsyncMock(return_value="c-olia")), \
         patch("arcana.handlers.sessions._save_and_post_triplet", saver):
        await sess.handle_add_session(_session_msg(), "оля расклад", user_id="u")

    assert saver.await_args.kwargs["link_ritual"] is False
