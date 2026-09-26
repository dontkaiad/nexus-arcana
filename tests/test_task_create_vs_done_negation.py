"""Баг (live, скрин): «создай задачу пофиксить баг зари когда не закончила
бронирование в лс…» → 🔥 + «🔍 Не нашёл задачу по: …». _DONE_RE ловил
«закончила» (даже с «не» перед ним) раньше fast-path'а явного создания
задачи — любая задача со словом «закончила/сделала» улетала в task_done.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.classifier import classify, _done_signal

BUG = "пофиксить баг зари когда не закончила бронирование в лс написала в чат где она есть и она бронит"
LLM_TASK = ('[{"type":"task","title":"пофиксить баг зари","category":"💳 Прочее",'
            '"priority":"Важно","deadline":null,"repeat":"Нет","repeat_time":null,'
            '"day_of_week":null,"confidence":"high"}]')


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["создай задачу ", "заведи задачу ", "задача "])
async def test_explicit_create_with_done_verb_inside_is_task(prefix):
    cat = AsyncMock(return_value="💳 Прочее")
    llm = AsyncMock(return_value=LLM_TASK)
    with patch("core.classifier._haiku_task_category", cat), \
         patch("core.classifier.ask_claude", llm):
        items = await classify(prefix + BUG, tz_offset=3)
    assert items[0]["type"] == "task"


@pytest.mark.asyncio
async def test_negated_done_verb_goes_to_llm_not_task_done():
    llm = AsyncMock(return_value=LLM_TASK)
    with patch("core.classifier.ask_claude", llm), \
         patch("core.classifier._haiku_task_category", AsyncMock(return_value="💳 Прочее")):
        items = await classify(BUG, tz_offset=3)
    assert items[0]["type"] != "task_done"


def test_done_signal_negation():
    assert _done_signal("закончила отчёт")
    assert _done_signal("сделала тест")
    assert not _done_signal("когда не закончила бронирование")
    # отрицание одного глагола не гасит другой
    assert _done_signal("не закончила, но сделала отчёт")
    assert not _done_signal("пофиксить баг когда она написала в чат")


@pytest.mark.asyncio
async def test_plain_done_still_task_done():
    items = await classify("закончила отчёт", tz_offset=3)
    assert items[0]["type"] == "task_done"
