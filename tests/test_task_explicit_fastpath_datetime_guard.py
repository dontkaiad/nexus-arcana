"""tests/test_task_explicit_fastpath_datetime_guard.py

Баг (live report, screenshot): «создай задачу написать на восстановление
аттестата завтра в 10 утра напомни завтра в 9:30» создало задачу с
title = вся фраза целиком (дата и «напомни завтра в 9:30» внутри title,
не распарсены), Дедлайн: не указан, Напоминание: нет, и неверной
категорией.

Root cause: `_TASK_EXPLICIT_RE` / `_BUY_TASK_RE` — детерминированные
no-LLM fast-path'ы для «добавь/поставь/создай задачу X» и «купи X» — не
парсят deadline/reminder вообще: жёстко ставят `deadline=None`, ключа
`reminder` в возвращаемом dict нет вовсе, а весь текст (включая дату)
уходит в title как есть. Обычный classify() через Claude умеет извлекать
и deadline, и reminder («напомни»+время → reminder, см. build_system()) —
но эти fast-path'ы его для скорости не вызывают.

Фикс: `_HAS_DATETIME_SIGNAL_RE` — если в захваченном title/тексте есть
явный признак даты/времени/напоминания, fast-path не подходит, текст
уходит в полный classify() через Claude.

Privacy: generic формулировки, без реальных данных Кай.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.classifier import classify


@pytest.mark.asyncio
async def test_task_explicit_with_reminder_and_deadline_skips_fastpath_uses_full_llm():
    """«создай задачу X завтра в 10 напомни завтра в 9:30» → не fast-path,
    полный classify() (Claude парсит и deadline, и reminder)."""
    full_llm_response = (
        '[{"type":"task","title":"написать на восстановление аттестата",'
        '"category":"💳 Прочее","priority":"Важно",'
        '"deadline":"2026-08-21T10:00","reminder":"2026-08-21T09:30",'
        '"repeat":"Нет","repeat_time":null,"day_of_week":null,"confidence":"high"}]'
    )
    ask_claude_mock = AsyncMock(return_value=full_llm_response)
    with patch("core.classifier.ask_claude", ask_claude_mock):
        items = await classify(
            "создай задачу написать на восстановление аттестата завтра в "
            "10 утра напомни завтра в 9:30",
            tz_offset=3,
        )

    assert len(items) == 1
    data = items[0]
    assert data["type"] == "task"
    # title НЕ должен содержать дату/напоминание целиком нетронутыми
    assert data["title"] == "написать на восстановление аттестата"
    assert data["deadline"] == "2026-08-21T10:00"
    assert data["reminder"] == "2026-08-21T09:30"
    # Полный пайплайн вызывал Claude ровно один раз (тот самый ask_claude с build_system)
    ask_claude_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_task_explicit_without_datetime_still_uses_fastpath_no_llm_classify():
    """Без даты/времени — быстрый детерминированный путь остаётся (без полного classify-запроса)."""
    category_mock = AsyncMock(return_value="🏠 Жильё")
    ask_claude_mock = AsyncMock(side_effect=AssertionError("full classify() must not be called"))
    with patch("core.classifier._haiku_task_category", category_mock), \
         patch("core.classifier.ask_claude", ask_claude_mock):
        items = await classify("создай задачу полить цветы", tz_offset=3)

    assert len(items) == 1
    data = items[0]
    assert data["type"] == "task"
    assert data["title"] == "полить цветы"
    assert data["deadline"] is None
    category_mock.assert_awaited_once()
    ask_claude_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_buy_task_re_datetime_guard_unit():
    """_BUY_TASK_RE fast-path тоже не парсит deadline/reminder — тот же
    guard применён и там (defense in depth). В обычном classify() «купи X»
    почти всегда сначала ловит более ранний _LIST_BUY_RE (список покупок,
    отдельная маршрутизация product vs task, вне рамок этого бага) — поэтому
    здесь проверяем guard напрямую на уровне regex, а не через classify()."""
    from core.classifier import _BUY_TASK_RE, _HAS_DATETIME_SIGNAL_RE

    with_reminder = "купи корм завтра напомни в 18"
    without = "купи корм коту"

    assert _BUY_TASK_RE.match(with_reminder)
    assert _HAS_DATETIME_SIGNAL_RE.search(with_reminder), (
        "guard должен сработать и не дать fast-path'у проглотить дату"
    )
    assert _BUY_TASK_RE.match(without)
    assert not _HAS_DATETIME_SIGNAL_RE.search(without)
