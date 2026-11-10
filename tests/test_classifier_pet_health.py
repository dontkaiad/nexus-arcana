"""tests/test_classifier_pet_health.py — «Тучке сделали прививку 5 ноября» больше не перехватывается префильтром «сделали → task_done»:
сообщение со словом про здоровье животного уходит к модели (у неё есть правило memory_save), остальные «сделала …» работают как раньше без LLM.
Граница LLM подменена, имена вымышленные."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from core import classifier


def classify(text, llm='[{"type":"memory_save","text":"x"}]'):
    called = AsyncMock(return_value=llm)
    with patch.object(classifier, "ask_claude", called), patch.object(classifier, "_known_budget_positions", AsyncMock(return_value=([], []))):
        items = asyncio.run(classifier.classify(text, tz_offset=3, user_id=""))
    return items, called


@pytest.mark.parametrize("text", [
    "Тучке сделали прививку 5 ноября", "Пончику сделала прививку от бешенства", "кексу сделали вакцинацию вчера", "Тучку обработала от блох, сделала всё",
    "Пончику сделали глистогонку", "Кексу сделал укол и дал капли", "сделала таблетку Кексу", "Тучку взвесила, сделала запись", "Кекса отвезла к ветеринару, всё сделали",
])
def test_pet_health_words_skip_the_done_prefilter_and_reach_the_model(text):
    items, llm = classify(text)
    assert llm.await_count == 1 and items[0]["type"] == "memory_save"


@pytest.mark.parametrize("text", ["сделала покупку корма", "выполнила задачу про отчёт", "сходила в аптеку", "готово", "позвонила маме, сделано", "закончила уборку"])
def test_ordinary_done_messages_still_skip_the_model(text):
    items, llm = classify(text)
    assert llm.await_count == 0 and items == [{"type": "task_done", "task_hint": text}]


def test_the_prompt_tells_the_model_what_to_do_with_them():
    src = open("core/classifier.py", encoding="utf-8").read()
    assert "'тучке сделали прививку 5 ноября' → memory_save" in src and "НЕ task и НЕ task_done" in src
