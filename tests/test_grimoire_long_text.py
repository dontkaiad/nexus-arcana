"""Гримуар: длинный свободный текст не должен попадать целиком в title.
Тексты синтетические (не реальные записи)."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import arcana.handlers.grimoire as gmod
from arcana.handlers.grimoire import (
    GRIMOIRE_CATEGORIES, GRIMOIRE_THEMES, handle_grimoire_add,
)

VALID_CATS = set(GRIMOIRE_CATEGORIES.values())
VALID_THEMES = set(GRIMOIRE_THEMES.values())

# одна тема, длинный текст, без разделителей " — "/" : " в первых 80 символах
ONE_TOPIC = ("Заговор на дорогу. " + "Читать вслух на растущую луну, держа в руке ключ. " * 40).strip()

# длинное вступление, и только потом маркированный список (" - " далеко от начала)
WITH_LIST = ("Описание ключа и ножа в отливках на воске, " + "подробности и контекст " * 20
             + "\n- первый пункт про ключ\n- второй пункт про нож")

# несколько самостоятельных тем с заголовками
MULTI = "\n\n".join([
    "Тема А:\nпервое описание про ключ, " + "слова " * 60,
    "Тема Б:\nвторое описание про нож, " + "слова " * 60,
    "Тема В:\nтретье описание про воск, " + "слова " * 60,
])


def _msg():
    m = MagicMock()
    m.from_user.id = 7
    m.answer = AsyncMock()
    return m


async def _add(text, llm_return):
    msg = _msg()
    with patch.object(gmod, "ask_claude", AsyncMock(return_value=llm_return)) as llm, \
         patch.object(gmod._repo, "add", AsyncMock(return_value="g-1")) as add, \
         patch.object(gmod, "log_error", AsyncMock()):
        await handle_grimoire_add(msg, text, user_id="u1")
    return msg, llm, add


@pytest.mark.asyncio
@pytest.mark.parametrize("llm_return", [
    "",                                                          # API недоступен
    '{"title": "Заговор на дорогу", "category": "заговор", "text": "Читать вслух на раст',  # обрезан по max_tokens
])
async def test_long_text_title_is_short_and_text_is_complete(llm_return):
    msg, llm, add = await _add(ONE_TOPIC, llm_return)
    kw = add.await_args.kwargs
    assert 0 < len(kw["title"]) <= 80 and "\n" not in kw["title"]
    assert kw["text"].strip() == ONE_TOPIC            # полный текст без потерь
    assert kw["category"] in VALID_CATS


@pytest.mark.asyncio
async def test_list_dash_far_from_start_does_not_become_title_boundary():
    """Раньше эвристика резала по первому « - » где угодно: title = всё вступление."""
    msg, llm, add = await _add(WITH_LIST, "")
    kw = add.await_args.kwargs
    assert len(kw["title"]) <= 80
    assert kw["text"].strip() == WITH_LIST


@pytest.mark.asyncio
async def test_llm_gets_only_head_not_full_text():
    ok = json.dumps({"title": "Заговор на дорогу", "category": "заговор", "themes": ["защита"]})
    msg, llm, add = await _add(ONE_TOPIC, ok)
    sent = llm.await_args.args[0]
    assert len(sent) < 600                           # хвост в LLM не уходит
    assert llm.await_args.kwargs["max_tokens"] <= 300


@pytest.mark.asyncio
async def test_bad_llm_fields_are_normalised():
    bad = json.dumps({"title": "Длинное название " * 30, "category": "что-то левое",
                      "themes": ["защита", "несуществующая", "ДЕНЬГИ"], "text": "эхо"})
    msg, llm, add = await _add(ONE_TOPIC, bad)
    kw = add.await_args.kwargs
    assert len(kw["title"]) <= 80
    assert kw["category"] in VALID_CATS
    assert set(kw["themes"] or []) <= VALID_THEMES
    assert kw["text"].strip() == ONE_TOPIC            # текст не из ответа LLM


@pytest.mark.asyncio
async def test_multi_topic_asks_instead_of_silently_saving():
    msg, llm, add = await _add(MULTI, "")
    add.assert_not_awaited()                          # ничего не записано до ответа
    kwargs = msg.answer.await_args.kwargs
    assert "reply_markup" in kwargs
    buttons = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert any(b.startswith("grim_split_one") for b in buttons)
    assert any(b.startswith("grim_split_many") for b in buttons)


@pytest.mark.asyncio
async def test_split_callbacks_one_and_many():
    from arcana.handlers.grimoire import cb_grimoire_split
    msg, llm, add = await _add(MULTI, "")
    for data, expected_calls in (("grim_split_one:7", 1), ("grim_split_many:7", 3)):
        # заново создаём pending (pop одноразовый)
        msg, llm, add = await _add(MULTI, "")
        q = MagicMock()
        q.data = data
        q.from_user.id = 7
        q.message = _msg()
        q.answer = AsyncMock()
        with patch.object(gmod._repo, "add", AsyncMock(return_value="g-1")) as add2:
            await cb_grimoire_split(q)
        assert add2.await_count == expected_calls, data
        for call in add2.await_args_list:
            assert len(call.kwargs["title"]) <= 80
