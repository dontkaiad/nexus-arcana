"""tests/test_classifier_night_owl_deadline_correction.py

Live report: "позвонить завтра" typed at 00:26 (21 августа) → бот поставил
дедлайн на 22 августа. build_system() уже содержит "НОЧНАЯ ЛОГИКА: до
05:00 'завтра' = СЕГОДНЯ" (см. test_classifier_night_rule_example.py —
предыдущий фикс контекстно противоречащего примера в самом промпте), но
это инструкция ДЛЯ Haiku внутри длинного промпта — маленькая модель не
гарантированно её соблюдает на каждый запрос.

Фикс: process_item() для type=="task" теперь ДЕТЕРМИНИРОВАННО поправляет
deadline/reminder назад на день, если 1) в исходном тексте есть «завтра»
(не «послезавтра»), 2) сейчас ночь (до 05:00), и 3) Claude всё равно вернул
дату, совпадающую с настоящим «завтра» (нарушив ночную логику). Если Claude
и так вернул сегодня — коррекция не трогает данные (иначе можно было бы по
ошибке откатить УЖЕ верную дату на день назад).

Privacy: synthetic uid, generic task text.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.classifier as clf


def _fake_now_patch(hour: int, minute: int = 0, day: int = 21, month: int = 8, year: int = 2026):
    fake_now = datetime(year, month, day, hour, minute, tzinfo=timezone(timedelta(hours=3)))

    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fake_now.astimezone(tz) if tz else fake_now

    return patch.object(clf, "datetime", FakeDatetime)


def _msg(uid: int = 780_001):
    m = MagicMock()
    m.from_user = MagicMock()
    m.from_user.id = uid
    m.answer = AsyncMock()
    return m


@pytest.mark.asyncio
async def test_night_owl_corrects_wrong_tomorrow_deadline():
    """00:26, 'позвонить завтра', Claude вернул '2026-08-22' (реальное завтра,
    нарушив ночную логику) → должно поправиться на '2026-08-21' (сегодня)."""
    msg = _msg()
    data = {
        "type": "task", "title": "позвонить", "category": "💳 Прочее",
        "priority": "Важно", "deadline": "2026-08-22", "reminder": None,
        "repeat": "Нет", "repeat_time": None, "day_of_week": None,
    }
    handle_task_parsed_mock = AsyncMock()

    with _fake_now_patch(hour=0, minute=26), \
         patch.object(clf, "react", AsyncMock()), \
         patch("nexus.handlers.tasks._get_user_tz", AsyncMock(return_value=3)), \
         patch("nexus.handlers.tasks.handle_task_parsed", handle_task_parsed_mock):
        await clf.process_item(data, "позвонить завтра", msg, {})

    handle_task_parsed_mock.assert_awaited_once()
    passed_data = handle_task_parsed_mock.await_args[0][1]
    assert passed_data["deadline"] == "2026-08-21"


@pytest.mark.asyncio
async def test_night_owl_leaves_already_correct_today_deadline_alone():
    """Если Claude УЖЕ вернул сегодня (2026-08-21, следуя ночной логике сам) —
    коррекция не должна откатывать её ещё на день назад."""
    msg = _msg()
    data = {
        "type": "task", "title": "позвонить", "category": "💳 Прочее",
        "priority": "Важно", "deadline": "2026-08-21", "reminder": None,
        "repeat": "Нет", "repeat_time": None, "day_of_week": None,
    }
    handle_task_parsed_mock = AsyncMock()

    with _fake_now_patch(hour=0, minute=26), \
         patch.object(clf, "react", AsyncMock()), \
         patch("nexus.handlers.tasks._get_user_tz", AsyncMock(return_value=3)), \
         patch("nexus.handlers.tasks.handle_task_parsed", handle_task_parsed_mock):
        await clf.process_item(data, "позвонить завтра", msg, {})

    passed_data = handle_task_parsed_mock.await_args[0][1]
    assert passed_data["deadline"] == "2026-08-21"


@pytest.mark.asyncio
async def test_night_owl_correction_preserves_time_component():
    """Дедлайн с временем ('2026-08-22T10:00') поправляется на сегодня, время сохраняется."""
    msg = _msg()
    data = {
        "type": "task", "title": "позвонить", "category": "💳 Прочее",
        "priority": "Важно", "deadline": "2026-08-22T10:00", "reminder": None,
        "repeat": "Нет", "repeat_time": None, "day_of_week": None,
    }
    handle_task_parsed_mock = AsyncMock()

    with _fake_now_patch(hour=0, minute=26), \
         patch.object(clf, "react", AsyncMock()), \
         patch("nexus.handlers.tasks._get_user_tz", AsyncMock(return_value=3)), \
         patch("nexus.handlers.tasks.handle_task_parsed", handle_task_parsed_mock):
        await clf.process_item(data, "позвонить завтра в 10", msg, {})

    passed_data = handle_task_parsed_mock.await_args[0][1]
    assert passed_data["deadline"] == "2026-08-21T10:00"


@pytest.mark.asyncio
async def test_night_owl_correction_skipped_during_day():
    """Днём (не ночь) коррекция не применяется — реальное 'завтра' остаётся как есть."""
    msg = _msg()
    data = {
        "type": "task", "title": "позвонить", "category": "💳 Прочее",
        "priority": "Важно", "deadline": "2026-08-22", "reminder": None,
        "repeat": "Нет", "repeat_time": None, "day_of_week": None,
    }
    handle_task_parsed_mock = AsyncMock()

    with _fake_now_patch(hour=14, minute=0), \
         patch.object(clf, "react", AsyncMock()), \
         patch("nexus.handlers.tasks._get_user_tz", AsyncMock(return_value=3)), \
         patch("nexus.handlers.tasks.handle_task_parsed", handle_task_parsed_mock):
        await clf.process_item(data, "позвонить завтра", msg, {})

    passed_data = handle_task_parsed_mock.await_args[0][1]
    assert passed_data["deadline"] == "2026-08-22"


@pytest.mark.asyncio
async def test_night_owl_correction_skipped_for_posle_zavtra():
    """'послезавтра' — реальное +2 дня, ночная логика на него не распространяется."""
    msg = _msg()
    data = {
        "type": "task", "title": "позвонить", "category": "💳 Прочее",
        "priority": "Важно", "deadline": "2026-08-23", "reminder": None,
        "repeat": "Нет", "repeat_time": None, "day_of_week": None,
    }
    handle_task_parsed_mock = AsyncMock()

    with _fake_now_patch(hour=0, minute=26), \
         patch.object(clf, "react", AsyncMock()), \
         patch("nexus.handlers.tasks._get_user_tz", AsyncMock(return_value=3)), \
         patch("nexus.handlers.tasks.handle_task_parsed", handle_task_parsed_mock):
        await clf.process_item(data, "позвонить послезавтра", msg, {})

    passed_data = handle_task_parsed_mock.await_args[0][1]
    assert passed_data["deadline"] == "2026-08-23"
